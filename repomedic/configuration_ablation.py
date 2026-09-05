from pathlib import Path
from typing import Any
import json

from repomedic.artifacts import ArtifactWriter
from repomedic.benchmark_run import summarize_benchmark


_MODES = (
    "single_agent",
    "multi_agent_no_review",
    "multi_agent_review",
)
_PAIR_FIELDS = (
    "suite_id",
    "protocol_version",
    "model",
    "reasoning_effort",
    "prompt_version",
)
_USAGE_FIELDS = ("total_tokens", "model_calls", "tool_calls", "latency_ms")


def _load_record(run_dir: Path) -> dict[str, Any]:
    try:
        value = json.loads((run_dir / "benchmark.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot load benchmark record from {run_dir}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"benchmark record must be an object: {run_dir}")
    return value


def _case_ids(record: dict[str, Any], run_dir: Path) -> tuple[str, ...]:
    rows = record.get("case_runs")
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"benchmark has no case runs: {run_dir}")
    case_ids = tuple(
        row.get("case_id") if isinstance(row, dict) else None for row in rows
    )
    if any(not isinstance(case_id, str) or not case_id for case_id in case_ids):
        raise ValueError(f"benchmark contains an invalid case run: {run_dir}")
    if len(case_ids) != len(set(case_ids)):
        raise ValueError(f"benchmark contains duplicate case IDs: {run_dir}")
    return case_ids


def _configuration_row(
    mode: str, run_dir: Path, summary: dict[str, Any]
) -> dict[str, Any]:
    case_count = int(summary["case_count"])
    verified = int(summary["verified"])
    return {
        "agent_mode": mode,
        "run_dir": str(run_dir),
        "verified": verified,
        "verified_rate": verified / case_count,
        "usage": summary["usage"],
    }


def _comparison(
    before: dict[str, Any], after: dict[str, Any], case_count: int
) -> dict[str, Any]:
    verified_delta = after["verified"] - before["verified"]
    return {
        "from": before["agent_mode"],
        "to": after["agent_mode"],
        "verified_tasks_delta": verified_delta,
        "verified_rate_delta": verified_delta / case_count,
        "usage_delta": {
            field: int(after["usage"][field]) - int(before["usage"][field])
            for field in _USAGE_FIELDS
        },
    }


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Agent configuration ablation",
        "",
        f"- Suite: `{report['suite_id']}`",
        f"- Model: `{report['model']}`",
        f"- Prompt: `{report['prompt_version']}`",
        f"- Cases: `{report['case_count']}`",
        "",
        "## Configurations",
        "",
    ]
    for row in report["configurations"]:
        lines.append(
            f"- `{row['agent_mode']}`: `{row['verified']}/{report['case_count']}` "
            f"verified, `{row['usage']['total_tokens']}` tokens, "
            f"`{row['usage']['model_calls']}` model calls"
        )
    lines.extend(["", "## Incremental comparisons", ""])
    for row in report["comparisons"]:
        lines.append(
            f"- `{row['from']}` -> `{row['to']}`: "
            f"verified `{row['verified_tasks_delta']:+d}`, "
            f"tokens `{row['usage_delta']['total_tokens']:+d}`, "
            f"latency `{row['usage_delta']['latency_ms']:+d} ms`"
        )
    lines.extend(["", "## Per case", ""])
    for row in report["cases"]:
        lines.append(
            f"- `{row['case_id']}`: single `{row['single_agent']}`, "
            f"no-review `{row['multi_agent_no_review']}`, "
            f"review `{row['multi_agent_review']}`"
        )
    lines.append("")
    return "\n".join(lines)


def compare_agent_configurations(
    single_agent_run: Path,
    no_review_run: Path,
    review_run: Path,
    *,
    output_dir: Path,
) -> dict[str, Any]:
    run_dirs = tuple(
        path.resolve() for path in (single_agent_run, no_review_run, review_run)
    )
    records = tuple(_load_record(run_dir) for run_dir in run_dirs)
    summaries = tuple(summarize_benchmark(run_dir) for run_dir in run_dirs)

    for expected_mode, run_dir, record, summary in zip(
        _MODES, run_dirs, records, summaries
    ):
        if not summary.get("complete"):
            raise ValueError(f"benchmark must be complete before comparison: {run_dir}")
        if record.get("agent_mode") != expected_mode:
            raise ValueError(
                f"expected {expected_mode} benchmark, found "
                f"{record.get('agent_mode')!r}: {run_dir}"
            )
        memory = record.get("memory")
        if not isinstance(memory, dict) or memory.get("enabled") is not False:
            raise ValueError("configuration ablation runs must disable memory")

    first_summary = summaries[0]
    for summary in summaries[1:]:
        for field in _PAIR_FIELDS:
            if summary.get(field) != first_summary.get(field):
                raise ValueError(f"benchmark runs differ on {field}")

    case_lists = tuple(
        _case_ids(record, run_dir) for record, run_dir in zip(records, run_dirs)
    )
    if any(case_ids != case_lists[0] for case_ids in case_lists[1:]):
        raise ValueError("benchmark runs must contain the same ordered case IDs")

    case_count = len(case_lists[0])
    configuration_rows = [
        _configuration_row(mode, run_dir, summary)
        for mode, run_dir, summary in zip(_MODES, run_dirs, summaries)
    ]
    statuses = [
        {row["case_id"]: row["status"] for row in summary["cases"]}
        for summary in summaries
    ]
    case_rows = [
        {
            "case_id": case_id,
            **{
                mode: status_by_case[case_id]
                for mode, status_by_case in zip(_MODES, statuses)
            },
        }
        for case_id in case_lists[0]
    ]
    report: dict[str, Any] = {
        "suite_id": first_summary["suite_id"],
        "protocol_version": first_summary["protocol_version"],
        "model": first_summary["model"],
        "reasoning_effort": first_summary["reasoning_effort"],
        "prompt_version": first_summary["prompt_version"],
        "case_count": case_count,
        "configurations": configuration_rows,
        "comparisons": [
            _comparison(configuration_rows[0], configuration_rows[1], case_count),
            _comparison(configuration_rows[1], configuration_rows[2], case_count),
            _comparison(configuration_rows[0], configuration_rows[2], case_count),
        ],
        "cases": case_rows,
    }
    writer = ArtifactWriter(output_dir)
    writer.write_json("summary.json", report)
    writer.write_text("summary.md", _markdown(report))
    return report
