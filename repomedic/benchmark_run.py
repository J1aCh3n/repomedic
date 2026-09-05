from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
import json
import uuid

from langgraph.checkpoint.sqlite import SqliteSaver

from repomedic.agent_graph import AgentGraphRunner, AgentRunResult
from repomedic.artifacts import ArtifactWriter
from repomedic.benchmark import BenchmarkSuite
from repomedic.harness import DeterministicHarness
from repomedic.model_clients import ScriptedModel, StructuredModel
from repomedic.prompts import PROMPT_VERSION
from repomedic.workspace import resolve_within


BENCHMARK_PROTOCOL_VERSION = "multi-agent-review-v2"
_TERMINAL_STATUSES = {
    "verified",
    "tests_failed",
    "policy_violation",
    "infrastructure_error",
    "model_error",
    "tool_error",
    "approval_error",
    "review_error",
    "iteration_exhausted",
    "rejected",
    "stopped",
}


@dataclass(frozen=True)
class BenchmarkStartResult:
    run_dir: Path
    case_results: tuple[AgentRunResult, ...]


def _new_run_id() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{uuid.uuid4().hex[:8]}"


def _write_benchmark(run_dir: Path, value: dict[str, Any]) -> None:
    ArtifactWriter(run_dir).write_json("benchmark.json", value)


def start_benchmark(
    suite: BenchmarkSuite,
    *,
    model: StructuredModel,
    harness: DeterministicHarness,
    runs_root: Path,
    run_id: str | None = None,
) -> BenchmarkStartResult:
    root = runs_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    suite_root = resolve_within(root, suite.suite_id)
    suite_root.mkdir(exist_ok=True)
    benchmark_id = run_id or _new_run_id()
    run_dir = resolve_within(suite_root, benchmark_id)
    run_dir.mkdir(exist_ok=False)
    cases_root = resolve_within(run_dir, "cases")
    cases_root.mkdir()
    record: dict[str, Any] = {
        "suite_id": suite.suite_id,
        "suite_path": str(suite.suite_path),
        "split": suite.split,
        "protocol_version": BENCHMARK_PROTOCOL_VERSION,
        "model": model.model_id,
        "reasoning_effort": getattr(model, "reasoning_effort", None),
        "prompt_version": PROMPT_VERSION,
        "case_runs": [],
    }
    _write_benchmark(run_dir, record)

    results: list[AgentRunResult] = []
    for case in suite.cases:
        prepared = harness.prepare_case(case.case_dir, cases_root, run_id="attempt_1")
        checkpoint = prepared.layout.run_dir / "checkpoint.sqlite"
        with SqliteSaver.from_conn_string(str(checkpoint)) as saver:
            result = AgentGraphRunner(model, harness, saver).start(prepared)
        results.append(result)
        record["case_runs"].append(
            {
                "case_id": case.case_id,
                "run_id": result.run_id,
                "run_dir": str(Path(result.run_dir).relative_to(run_dir)),
                "initial_status": result.status,
            }
        )
        _write_benchmark(run_dir, record)

    return BenchmarkStartResult(run_dir=run_dir, case_results=tuple(results))


def _summary_markdown(summary: dict[str, Any]) -> str:
    lines = [
        f"# Benchmark run: {summary['suite_id']}",
        "",
        f"- Complete: `{str(summary['complete']).lower()}`",
        f"- Verified: `{summary['verified']}/{summary['case_count']}`",
        f"- Total input tokens: `{summary['usage']['input_tokens']}`",
        f"- Total output tokens: `{summary['usage']['output_tokens']}`",
        f"- Total model calls: `{summary['usage']['model_calls']}`",
        f"- Cumulative model latency: `{summary['usage']['latency_ms']} ms`",
        f"- Average model-call latency: `{summary['usage']['average_model_latency_ms']} ms`",
        "",
        "## Cases",
        "",
    ]
    for case in summary["cases"]:
        lines.append(f"- `{case['case_id']}`: `{case['status']}`")
    lines.append("")
    return "\n".join(lines)


def summarize_benchmark(run_dir: Path) -> dict[str, Any]:
    resolved = run_dir.resolve()
    try:
        record = json.loads((resolved / "benchmark.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot load benchmark run: {error}") from error
    case_rows: list[dict[str, Any]] = []
    usage_totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "model_calls": 0,
        "tool_calls": 0,
        "latency_ms": 0,
    }
    status_counts: dict[str, int] = {}

    for case in record["case_runs"]:
        case_run = resolve_within(resolved, case["run_dir"])
        checkpoint = case_run / "checkpoint.sqlite"
        with SqliteSaver.from_conn_string(str(checkpoint)) as saver:
            result = AgentGraphRunner(
                ScriptedModel({}), DeterministicHarness(), saver
            ).inspect(case["run_id"])
        status_counts[result.status] = status_counts.get(result.status, 0) + 1
        usage_path = case_run / "usage.json"
        usage = (
            json.loads(usage_path.read_text(encoding="utf-8"))
            if usage_path.is_file()
            else result.usage or {}
        )
        for field in usage_totals:
            value = usage.get(field, 0)
            if isinstance(value, int):
                usage_totals[field] += value
        case_rows.append(
            {
                "case_id": case["case_id"],
                "run_dir": str(case_run),
                "status": result.status,
                "usage": usage,
            }
        )

    model_calls = usage_totals["model_calls"]
    usage_totals["average_model_latency_ms"] = (
        round(usage_totals["latency_ms"] / model_calls) if model_calls else 0
    )

    complete = all(row["status"] in _TERMINAL_STATUSES for row in case_rows)
    summary: dict[str, Any] = {
        "suite_id": record["suite_id"],
        "protocol_version": record["protocol_version"],
        "model": record["model"],
        "reasoning_effort": record.get("reasoning_effort"),
        "prompt_version": record["prompt_version"],
        "complete": complete,
        "case_count": len(case_rows),
        "verified": status_counts.get("verified", 0),
        "status_counts": status_counts,
        "usage": usage_totals,
        "cases": case_rows,
    }
    writer = ArtifactWriter(resolved)
    writer.write_json("summary.json", summary)
    writer.write_text("summary.md", _summary_markdown(summary))
    return summary
