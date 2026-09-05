from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from math import comb
from pathlib import Path
from typing import Any
import json
import uuid

from langgraph.checkpoint.sqlite import SqliteSaver

from repomedic.agent_graph import (
    DEFAULT_AGENT_MODE,
    AgentGraphRunner,
    AgentMode,
    AgentRunResult,
)
from repomedic.artifacts import ArtifactWriter
from repomedic.benchmark import BenchmarkSuite
from repomedic.harness import DeterministicHarness
from repomedic.memory import (
    DEFAULT_MEMORY_CONTEXT_BUDGET_CHARS,
    EpisodicMemoryStore,
)
from repomedic.model_clients import ScriptedModel, StructuredModel
from repomedic.prompts import PROMPT_VERSION
from repomedic.workspace import resolve_within


BENCHMARK_PROTOCOL_VERSION = "agent-config-ablation-v2"
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
    memory_store: EpisodicMemoryStore | None = None,
    memory_limit: int = 3,
    memory_context_budget_chars: int = DEFAULT_MEMORY_CONTEXT_BUDGET_CHARS,
    agent_mode: AgentMode = DEFAULT_AGENT_MODE,
    attempts_per_case: int = 1,
) -> BenchmarkStartResult:
    if (
        not isinstance(attempts_per_case, int)
        or isinstance(attempts_per_case, bool)
        or not 1 <= attempts_per_case <= 3
    ):
        raise ValueError("attempts per case must be between 1 and 3")
    root = runs_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    suite_root = resolve_within(root, suite.suite_id)
    suite_root.mkdir(exist_ok=True)
    benchmark_id = run_id or _new_run_id()
    run_dir = resolve_within(suite_root, benchmark_id)
    run_dir.mkdir(exist_ok=False)
    cases_root = resolve_within(run_dir, "cases")
    cases_root.mkdir()
    corpus = memory_store.snapshot() if memory_store is not None else None
    record: dict[str, Any] = {
        "suite_id": suite.suite_id,
        "suite_path": str(suite.suite_path),
        "split": suite.split,
        "protocol_version": BENCHMARK_PROTOCOL_VERSION,
        "model": model.model_id,
        "reasoning_effort": getattr(model, "reasoning_effort", None),
        "prompt_version": PROMPT_VERSION,
        "agent_mode": agent_mode,
        "attempts_per_case": attempts_per_case,
        "memory": {
            "enabled": memory_store is not None,
            "write_enabled": False,
            "database": str(memory_store.path) if memory_store else None,
            "limit": memory_limit,
            "context_budget_chars": memory_context_budget_chars,
            "corpus": asdict(corpus) if corpus is not None else None,
        },
        "case_runs": [],
    }
    _write_benchmark(run_dir, record)

    results: list[AgentRunResult] = []
    for case in suite.cases:
        for attempt in range(1, attempts_per_case + 1):
            prepared = harness.prepare_case(
                case.case_dir, cases_root, run_id=f"attempt_{attempt}"
            )
            checkpoint = prepared.layout.run_dir / "checkpoint.sqlite"
            with SqliteSaver.from_conn_string(str(checkpoint)) as saver:
                result = AgentGraphRunner(
                    model,
                    harness,
                    saver,
                    memory_store=memory_store,
                    memory_limit=memory_limit,
                    memory_context_budget_chars=memory_context_budget_chars,
                    memory_write_enabled=False,
                    agent_mode=agent_mode,
                ).start(prepared)
            results.append(result)
            record["case_runs"].append(
                {
                    "case_id": case.case_id,
                    "attempt": attempt,
                    "run_id": result.run_id,
                    "run_dir": str(Path(result.run_dir).relative_to(run_dir)),
                    "initial_status": result.status,
                }
            )
            _write_benchmark(run_dir, record)

    return BenchmarkStartResult(run_dir=run_dir, case_results=tuple(results))


def _summary_markdown(summary: dict[str, Any]) -> str:
    pass_at_3 = (
        f"{summary['pass_at_3']:.1%}"
        if summary["pass_at_3"] is not None
        else "not measured"
    )
    lines = [
        f"# Benchmark run: {summary['suite_id']}",
        "",
        f"- Complete: `{str(summary['complete']).lower()}`",
        f"- Agent mode: `{summary['agent_mode']}`",
        f"- Attempts per case: `{summary['attempts_per_case']}`",
        f"- Verified runs: `{summary['verified']}/{summary['run_count']}`",
        f"- pass@1: `{summary['pass_at_1']:.1%}`",
        f"- pass@3: `{pass_at_3}`",
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
        lines.append(
            f"- `{case['case_id']}` attempt `{case['attempt']}`: `{case['status']}`"
        )
    lines.append("")
    return "\n".join(lines)


def _pass_at_k(
    case_rows: list[dict[str, Any]], case_ids: tuple[str, ...], k: int
) -> float:
    estimates: list[float] = []
    for case_id in case_ids:
        rows = [row for row in case_rows if row["case_id"] == case_id]
        sample_count = len(rows)
        correct_count = sum(row["status"] == "verified" for row in rows)
        if sample_count < k:
            raise ValueError(f"case {case_id} has fewer than {k} attempts")
        failure_probability = (
            comb(sample_count - correct_count, k) / comb(sample_count, k)
            if sample_count - correct_count >= k
            else 0.0
        )
        estimates.append(1.0 - failure_probability)
    return sum(estimates) / len(estimates)


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
                "attempt": case.get("attempt", 1),
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
    case_ids = tuple(dict.fromkeys(row["case_id"] for row in case_rows))
    attempts_per_case = record.get("attempts_per_case", 1)
    if not case_ids:
        raise ValueError("benchmark has no case runs")
    if (
        not isinstance(attempts_per_case, int)
        or isinstance(attempts_per_case, bool)
        or not 1 <= attempts_per_case <= 3
    ):
        raise ValueError("benchmark has invalid attempts_per_case")
    if any(
        not isinstance(row["attempt"], int)
        or isinstance(row["attempt"], bool)
        or not 1 <= row["attempt"] <= attempts_per_case
        for row in case_rows
    ):
        raise ValueError("benchmark contains an invalid case attempt")
    attempts_by_case = {
        case_id: sum(row["case_id"] == case_id for row in case_rows)
        for case_id in case_ids
    }
    if any(count != attempts_per_case for count in attempts_by_case.values()):
        raise ValueError("benchmark case runs do not match attempts_per_case")
    run_keys = [(row["case_id"], row["attempt"]) for row in case_rows]
    if len(run_keys) != len(set(run_keys)):
        raise ValueError("benchmark contains duplicate case attempts")
    pass_at_1 = _pass_at_k(case_rows, case_ids, 1)
    pass_at_3 = (
        _pass_at_k(case_rows, case_ids, 3)
        if attempts_per_case >= 3
        else None
    )
    summary: dict[str, Any] = {
        "suite_id": record["suite_id"],
        "protocol_version": record["protocol_version"],
        "model": record["model"],
        "reasoning_effort": record.get("reasoning_effort"),
        "prompt_version": record["prompt_version"],
        "agent_mode": record.get("agent_mode", DEFAULT_AGENT_MODE),
        "attempts_per_case": attempts_per_case,
        "complete": complete,
        "case_count": len(case_ids),
        "run_count": len(case_rows),
        "verified": status_counts.get("verified", 0),
        "pass_at_1": pass_at_1,
        "pass_at_3": pass_at_3,
        "status_counts": status_counts,
        "usage": usage_totals,
        "cases": case_rows,
    }
    writer = ArtifactWriter(resolved)
    writer.write_json("summary.json", summary)
    writer.write_text("summary.md", _summary_markdown(summary))
    return summary
