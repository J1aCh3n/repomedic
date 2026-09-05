from pathlib import Path
from typing import Any

from repomedic.artifacts import ArtifactWriter
from repomedic.configuration_ablation import compare_agent_configurations
from repomedic.memory_ablation import compare_memory_ablation


_USAGE_FIELDS = ("total_tokens", "model_calls", "tool_calls", "latency_ms")


def _delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    return {
        "from": before["configuration"],
        "to": after["configuration"],
        "verified_runs": after["verified"] - before["verified"],
        "verified_rate": after["verified_rate"] - before["verified_rate"],
        "pass_at_1": after["pass_at_1"] - before["pass_at_1"],
        "pass_at_3": after["pass_at_3"] - before["pass_at_3"],
        "usage": {
            field: int(after["usage"][field]) - int(before["usage"][field])
            for field in _USAGE_FIELDS
        },
    }


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Unified preflight configuration comparison",
        "",
        f"- Suite: `{report['suite_id']}`",
        f"- Model: `{report['model']}`",
        f"- Reasoning effort: `{report['reasoning_effort']}`",
        f"- Prompt: `{report['prompt_version']}`",
        f"- Cases: `{report['case_count']}`",
        f"- Attempts per case: `{report['attempts_per_case']}`",
        f"- Runs per configuration: `{report['run_count']}`",
        "- Frozen memory corpus entries: "
        f"`{report['memory_evidence']['corpus']['entry_count']}`",
        "- Frozen memory corpus hash: "
        f"`{report['memory_evidence']['corpus']['content_hash']}`",
        "- Memory-covered runs: "
        f"`{report['memory_evidence']['covered_runs']}/{report['run_count']}`",
        "- Retrieved memory entries: "
        f"`{report['memory_evidence']['retrieval_count']}`",
        "",
        "| Configuration | Memory | Verified runs | pass@1 | pass@3 | Tokens | Model calls | Tool calls | Latency (ms) |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in report["configurations"]:
        lines.append(
            f"| `{row['configuration']}` | `{str(row['memory_enabled']).lower()}` | "
            f"{row['verified']}/{row['run_count']} | {row['pass_at_1']:.1%} | "
            f"{row['pass_at_3']:.1%} | {row['usage']['total_tokens']} | "
            f"{row['usage']['model_calls']} | {row['usage']['tool_calls']} | "
            f"{row['usage']['latency_ms']} |"
        )
    lines.extend(["", "## Incremental effects", ""])
    for row in report["comparisons"]:
        lines.append(
            f"- `{row['from']}` -> `{row['to']}`: verified runs "
            f"`{row['verified_runs']:+d}`, pass@1 `{row['pass_at_1']:+.1%}`, "
            f"pass@3 `{row['pass_at_3']:+.1%}`, tokens "
            f"`{row['usage']['total_tokens']:+d}`, latency "
            f"`{row['usage']['latency_ms']:+d} ms`"
        )
    lines.extend(["", "## Per case and attempt", ""])
    for row in report["cases"]:
        lines.append(
            f"- `{row['case_id']}` attempt `{row['attempt']}`: "
            f"single `{row['single_agent']}`, no-review "
            f"`{row['multi_agent_no_review']}`, review "
            f"`{row['multi_agent_review']}`, review+memory "
            f"`{row['multi_agent_review_with_memory']}`"
        )
    lines.append("")
    return "\n".join(lines)


def compare_preflight_configurations(
    single_agent_run: Path,
    no_review_run: Path,
    review_run: Path,
    memory_run: Path,
    *,
    output_dir: Path,
) -> dict[str, Any]:
    resolved_output = output_dir.resolve()
    agent_report = compare_agent_configurations(
        single_agent_run,
        no_review_run,
        review_run,
        output_dir=resolved_output / "agent-configurations",
    )
    memory_report = compare_memory_ablation(
        review_run,
        memory_run,
        resolved_output / "memory-ablation",
    )

    configurations = [
        {
            **row,
            "configuration": row["agent_mode"],
            "memory_enabled": False,
        }
        for row in agent_report["configurations"]
    ]
    memory_treatment = memory_report["memory_treatment"]
    configurations.append(
        {
            "configuration": "multi_agent_review_with_memory",
            "agent_mode": memory_report["agent_mode"],
            "memory_enabled": True,
            "run_dir": memory_treatment["run_dir"],
            "case_count": memory_report["case_count"],
            "run_count": memory_report["run_count"],
            "verified": memory_treatment["verified"],
            "verified_rate": memory_treatment["verified_rate"],
            "pass_at_1": memory_treatment["pass_at_1"],
            "pass_at_3": memory_treatment["pass_at_3"],
            "usage": memory_treatment["usage"],
        }
    )

    memory_statuses = {
        (row["case_id"], row["attempt"]): row["memory_status"]
        for row in memory_report["cases"]
    }
    case_rows = [
        {
            **row,
            "multi_agent_review_with_memory": memory_statuses[
                (row["case_id"], row["attempt"])
            ],
        }
        for row in agent_report["cases"]
    ]
    report: dict[str, Any] = {
        "suite_id": agent_report["suite_id"],
        "protocol_version": agent_report["protocol_version"],
        "model": agent_report["model"],
        "reasoning_effort": agent_report["reasoning_effort"],
        "prompt_version": agent_report["prompt_version"],
        "attempts_per_case": agent_report["attempts_per_case"],
        "case_count": agent_report["case_count"],
        "run_count": agent_report["run_count"],
        "configurations": configurations,
        "comparisons": [
            _delta(configurations[index], configurations[index + 1])
            for index in range(len(configurations) - 1)
        ]
        + [_delta(configurations[0], configurations[-1])],
        "memory_evidence": memory_report["memory_evidence"],
        "cases": case_rows,
    }
    writer = ArtifactWriter(resolved_output)
    writer.write_json("summary.json", report)
    writer.write_text("summary.md", _markdown(report))
    return report
