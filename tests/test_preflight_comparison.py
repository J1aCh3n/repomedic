from pathlib import Path
from unittest.mock import patch
import unittest

from repomedic.preflight_comparison import compare_preflight_configurations
from tests.helpers import temporary_directory


def _usage(value: int) -> dict[str, int]:
    return {
        "total_tokens": value,
        "model_calls": value,
        "tool_calls": value,
        "latency_ms": value,
    }


class PreflightComparisonTests(unittest.TestCase):
    def test_combines_four_matched_configurations(self) -> None:
        agent_report = {
            "suite_id": "initial_12",
            "protocol_version": "agent-config-ablation-v2",
            "model": "scripted",
            "reasoning_effort": "low",
            "prompt_version": "agent-graph-v5",
            "attempts_per_case": 3,
            "case_count": 1,
            "run_count": 3,
            "configurations": [
                {
                    "agent_mode": mode,
                    "run_dir": mode,
                    "case_count": 1,
                    "run_count": 3,
                    "verified": verified,
                    "verified_rate": verified / 3,
                    "pass_at_1": verified / 3,
                    "pass_at_3": float(verified > 0),
                    "usage": _usage(10 + verified),
                }
                for mode, verified in (
                    ("single_agent", 0),
                    ("multi_agent_no_review", 1),
                    ("multi_agent_review", 2),
                )
            ],
            "cases": [
                {
                    "case_id": "case_001",
                    "attempt": attempt,
                    "single_agent": "tests_failed",
                    "multi_agent_no_review": "verified",
                    "multi_agent_review": "verified",
                }
                for attempt in (1, 2, 3)
            ],
        }
        memory_report = {
            "agent_mode": "multi_agent_review",
            "case_count": 1,
            "run_count": 3,
            "memory_treatment": {
                "run_dir": "memory",
                "verified": 3,
                "verified_rate": 1.0,
                "pass_at_1": 1.0,
                "pass_at_3": 1.0,
                "usage": _usage(20),
            },
            "memory_evidence": {
                "corpus": {"entry_count": 2, "content_hash": "a" * 64},
                "covered_runs": 3,
                "retrieval_count": 3,
            },
            "cases": [
                {
                    "case_id": "case_001",
                    "attempt": attempt,
                    "baseline_status": "verified",
                    "memory_status": "verified",
                }
                for attempt in (1, 2, 3)
            ],
        }
        with temporary_directory() as temp_dir:
            output = Path(temp_dir) / "comparison"
            with patch(
                "repomedic.preflight_comparison.compare_agent_configurations",
                return_value=agent_report,
            ), patch(
                "repomedic.preflight_comparison.compare_memory_ablation",
                return_value=memory_report,
            ):
                report = compare_preflight_configurations(
                    Path("single"),
                    Path("no-review"),
                    Path("review"),
                    Path("memory"),
                    output_dir=output,
                )

            self.assertEqual(len(report["configurations"]), 4)
            self.assertEqual(
                report["configurations"][-1]["configuration"],
                "multi_agent_review_with_memory",
            )
            self.assertEqual(report["comparisons"][2]["verified_runs"], 1)
            self.assertEqual(
                report["cases"][0]["multi_agent_review_with_memory"], "verified"
            )
            self.assertTrue((output / "summary.json").is_file())
            self.assertTrue((output / "summary.md").is_file())


if __name__ == "__main__":
    unittest.main()
