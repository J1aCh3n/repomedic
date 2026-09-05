from pathlib import Path
from typing import Any
import json

from repomedic.artifacts import ArtifactWriter
from repomedic.benchmark_run import summarize_benchmark
from repomedic.memory import memory_prompt_chars, validate_memory_context_budget
from repomedic.workspace import resolve_within


_PAIR_FIELDS = (
    "suite_id",
    "protocol_version",
    "model",
    "reasoning_effort",
    "prompt_version",
    "agent_mode",
    "attempts_per_case",
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


def _run_keys(record: dict[str, Any], run_dir: Path) -> tuple[tuple[str, int], ...]:
    rows = record.get("case_runs")
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"benchmark has no case runs: {run_dir}")
    run_keys: list[tuple[str, int]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(f"benchmark contains an invalid case run: {run_dir}")
        case_id = row.get("case_id")
        attempt = row.get("attempt", 1)
        if (
            not isinstance(case_id, str)
            or not case_id
            or not isinstance(attempt, int)
            or isinstance(attempt, bool)
            or attempt < 1
        ):
            raise ValueError(f"benchmark contains an invalid case run: {run_dir}")
        run_keys.append((case_id, attempt))
    if len(run_keys) != len(set(run_keys)):
        raise ValueError(f"benchmark contains duplicate case attempts: {run_dir}")
    return tuple(run_keys)


def _memory_config(record: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    config = record.get("memory")
    if not isinstance(config, dict) or not isinstance(config.get("enabled"), bool):
        raise ValueError(f"benchmark has no explicit memory configuration: {run_dir}")
    return config


def _retrieval_evidence(
    memory_run: Path, record: dict[str, Any]
) -> tuple[list[dict[str, Any]], int, int]:
    memory_config = _memory_config(record, memory_run)
    context_budget = memory_config.get("context_budget_chars")
    try:
        validate_memory_context_budget(context_budget)
    except ValueError as error:
        raise ValueError("memory treatment has an invalid context budget") from error
    corpus = memory_config.get("corpus")
    if (
        not isinstance(corpus, dict)
        or not isinstance(corpus.get("entry_count"), int)
        or isinstance(corpus.get("entry_count"), bool)
        or corpus["entry_count"] < 1
        or not isinstance(corpus.get("content_hash"), str)
        or len(corpus["content_hash"]) != 64
    ):
        raise ValueError("memory treatment has no valid corpus snapshot")
    evidence: list[dict[str, Any]] = []
    covered_runs = 0
    covered_case_ids: set[str] = set()
    for row in record["case_runs"]:
        case_id = row["case_id"]
        attempt = row.get("attempt", 1)
        case_run = resolve_within(memory_run, row["run_dir"])
        try:
            artifact = json.loads((case_run / "memory.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(
                f"cannot load memory evidence for {case_id}: {error}"
            ) from error
        if not isinstance(artifact, dict):
            raise ValueError(f"memory evidence for {case_id} must be an object")
        retrieved = artifact.get("retrieved")
        if not isinstance(retrieved, list):
            raise ValueError(f"memory evidence for {case_id} has no retrieval list")
        if artifact.get("corpus") != corpus:
            raise ValueError(
                f"memory evidence for {case_id} uses a different corpus snapshot"
            )
        actual_context_chars = memory_prompt_chars(retrieved)
        if (
            artifact.get("context_budget_chars") != context_budget
            or artifact.get("context_chars") != actual_context_chars
            or actual_context_chars > context_budget
        ):
            raise ValueError(
                f"memory evidence for {case_id} has invalid context budget accounting"
            )
        if retrieved:
            covered_runs += 1
            covered_case_ids.add(case_id)
        for item in retrieved:
            if not isinstance(item, dict):
                raise ValueError(f"memory evidence for {case_id} contains an invalid item")
            if not isinstance(item.get("entry_id"), str) or not item["entry_id"].strip():
                raise ValueError(f"memory evidence for {case_id} lacks an entry ID")
            if not isinstance(item.get("score"), int) or isinstance(item["score"], bool):
                raise ValueError(f"memory evidence for {case_id} lacks a numeric score")
            provenance = item.get("provenance")
            required = ("fixture_id", "fixture_version", "case_id", "run_id")
            if not isinstance(provenance, dict) or any(
                not isinstance(provenance.get(field), str)
                or not provenance[field].strip()
                for field in required
            ):
                raise ValueError(
                    f"memory evidence for {case_id} lacks complete provenance"
                )
            if provenance["case_id"] == case_id:
                raise ValueError(f"memory run leaked same-case memory into {case_id}")
            evidence.append(
                {
                    "target_case_id": case_id,
                    "target_attempt": attempt,
                    "entry_id": item.get("entry_id"),
                    "score": item.get("score"),
                    "provenance": provenance,
                }
            )
    if not evidence:
        raise ValueError("memory treatment retrieved no entries; no memory effect was tested")
    return evidence, covered_runs, len(covered_case_ids)


def _markdown(report: dict[str, Any]) -> str:
    baseline = report["baseline"]
    treatment = report["memory_treatment"]
    baseline_pass_at_3 = (
        f"{baseline['pass_at_3']:.1%}"
        if baseline["pass_at_3"] is not None
        else "not measured"
    )
    treatment_pass_at_3 = (
        f"{treatment['pass_at_3']:.1%}"
        if treatment["pass_at_3"] is not None
        else "not measured"
    )
    lines = [
        "# Memory ablation",
        "",
        f"- Suite: `{report['suite_id']}`",
        f"- Model: `{report['model']}`",
        f"- Prompt: `{report['prompt_version']}`",
        f"- Agent mode: `{report['agent_mode']}`",
        f"- Cases: `{report['case_count']}`",
        f"- Attempts per case: `{report['attempts_per_case']}`",
        f"- Total runs per configuration: `{report['run_count']}`",
        f"- Memory corpus entries: `{report['memory_evidence']['corpus']['entry_count']}`",
        f"- Memory corpus hash: `{report['memory_evidence']['corpus']['content_hash']}`",
        f"- Memory context budget: `{report['memory_evidence']['context_budget_chars']}` chars",
        f"- Memory-covered cases: `{report['memory_evidence']['covered_cases']}`",
        f"- Memory-covered runs: `{report['memory_evidence']['covered_runs']}`",
        f"- Retrieved entries: `{report['memory_evidence']['retrieval_count']}`",
        "",
        "## Verified outcomes",
        "",
        f"- No memory: `{baseline['verified']}/{report['run_count']}` verified runs, "
        f"pass@1 `{baseline['pass_at_1']:.1%}`, pass@3 `{baseline_pass_at_3}`",
        f"- With memory: `{treatment['verified']}/{report['run_count']}` verified runs, "
        f"pass@1 `{treatment['pass_at_1']:.1%}`, pass@3 `{treatment_pass_at_3}`",
        f"- Verified-run uplift: `{report['delta']['verified_runs']:+d}`",
        f"- Verified-rate uplift: `{report['delta']['verified_rate']:+.1%}`",
        f"- pass@1 uplift: `{report['delta']['pass_at_1']:+.1%}`",
        "",
        "## Per case",
        "",
    ]
    for row in report["cases"]:
        lines.append(
            f"- `{row['case_id']}` attempt `{row['attempt']}`: "
            f"`{row['baseline_status']}` -> "
            f"`{row['memory_status']}`"
        )
    lines.append("")
    return "\n".join(lines)


def compare_memory_ablation(
    baseline_run: Path,
    memory_run: Path,
    output_dir: Path,
) -> dict[str, Any]:
    baseline_resolved = baseline_run.resolve()
    memory_resolved = memory_run.resolve()
    baseline_record = _load_record(baseline_resolved)
    memory_record = _load_record(memory_resolved)
    baseline_summary = summarize_benchmark(baseline_resolved)
    memory_summary = summarize_benchmark(memory_resolved)
    baseline_memory = _memory_config(baseline_record, baseline_resolved)
    treatment_memory = _memory_config(memory_record, memory_resolved)

    if not baseline_summary.get("complete") or not memory_summary.get("complete"):
        raise ValueError("both benchmark runs must be complete before comparison")
    for field in _PAIR_FIELDS:
        if baseline_summary.get(field) != memory_summary.get(field):
            raise ValueError(f"benchmark runs differ on {field}")
    baseline_runs = _run_keys(baseline_record, baseline_resolved)
    memory_runs = _run_keys(memory_record, memory_resolved)
    if baseline_runs != memory_runs:
        raise ValueError("benchmark runs must contain the same ordered case attempts")
    if baseline_memory["enabled"]:
        raise ValueError("baseline benchmark must disable memory")
    if not treatment_memory["enabled"]:
        raise ValueError("memory treatment benchmark must enable memory")

    retrievals, covered_runs, covered_cases = _retrieval_evidence(
        memory_resolved, memory_record
    )
    baseline_by_run = {
        (row["case_id"], row.get("attempt", 1)): row
        for row in baseline_summary["cases"]
    }
    memory_by_run = {
        (row["case_id"], row.get("attempt", 1)): row
        for row in memory_summary["cases"]
    }
    rows = [
        {
            "case_id": case_id,
            "attempt": attempt,
            "baseline_status": baseline_by_run[(case_id, attempt)]["status"],
            "memory_status": memory_by_run[(case_id, attempt)]["status"],
        }
        for case_id, attempt in baseline_runs
    ]
    case_count = int(baseline_summary["case_count"])
    run_count = len(baseline_runs)
    baseline_verified = int(baseline_summary["verified"])
    memory_verified = int(memory_summary["verified"])
    usage_delta = {
        field: int(memory_summary["usage"][field])
        - int(baseline_summary["usage"][field])
        for field in _USAGE_FIELDS
    }
    report: dict[str, Any] = {
        "suite_id": baseline_summary["suite_id"],
        "protocol_version": baseline_summary["protocol_version"],
        "model": baseline_summary["model"],
        "reasoning_effort": baseline_summary["reasoning_effort"],
        "prompt_version": baseline_summary["prompt_version"],
        "agent_mode": baseline_summary["agent_mode"],
        "attempts_per_case": baseline_summary["attempts_per_case"],
        "case_count": case_count,
        "run_count": run_count,
        "baseline": {
            "run_dir": str(baseline_resolved),
            "verified": baseline_verified,
            "verified_rate": baseline_verified / run_count,
            "pass_at_1": baseline_summary["pass_at_1"],
            "pass_at_3": baseline_summary["pass_at_3"],
            "usage": baseline_summary["usage"],
        },
        "memory_treatment": {
            "run_dir": str(memory_resolved),
            "verified": memory_verified,
            "verified_rate": memory_verified / run_count,
            "pass_at_1": memory_summary["pass_at_1"],
            "pass_at_3": memory_summary["pass_at_3"],
            "usage": memory_summary["usage"],
        },
        "delta": {
            "verified_runs": memory_verified - baseline_verified,
            "verified_rate": (memory_verified - baseline_verified) / run_count,
            "pass_at_1": memory_summary["pass_at_1"]
            - baseline_summary["pass_at_1"],
            "pass_at_3": (
                memory_summary["pass_at_3"] - baseline_summary["pass_at_3"]
                if baseline_summary["pass_at_3"] is not None
                and memory_summary["pass_at_3"] is not None
                else None
            ),
            "usage": usage_delta,
        },
        "memory_evidence": {
            "context_budget_chars": treatment_memory["context_budget_chars"],
            "corpus": treatment_memory["corpus"],
            "covered_cases": covered_cases,
            "covered_runs": covered_runs,
            "retrieval_count": len(retrievals),
            "retrievals": retrievals,
        },
        "cases": rows,
    }
    writer = ArtifactWriter(output_dir)
    writer.write_json("summary.json", report)
    writer.write_text("summary.md", _markdown(report))
    return report
