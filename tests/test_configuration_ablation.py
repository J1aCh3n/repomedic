from pathlib import Path
from unittest.mock import patch
import json
import unittest

from repomedic.configuration_ablation import compare_agent_configurations
from tests.helpers import temporary_directory


MODES = (
    "single_agent",
    "multi_agent_no_review",
    "multi_agent_review",
)


def _write_run(root: Path, mode: str) -> Path:
    run_dir = root / mode
    run_dir.mkdir()
    (run_dir / "benchmark.json").write_text(
        json.dumps(
            {
                "suite_id": "initial_12",
                "protocol_version": "agent-config-ablation-v1",
                "model": "scripted",
                "reasoning_effort": None,
                "prompt_version": "agent-graph-v5",
                "agent_mode": mode,
                "memory": {"enabled": False},
                "case_runs": [
                    {"case_id": "case_001"},
                    {"case_id": "case_002"},
                ],
            }
        ),
        encoding="utf-8",
    )
    return run_dir


def _summary(mode: str, verified: int) -> dict:
    statuses = ["verified"] * verified + ["tests_failed"] * (2 - verified)
    return {
        "suite_id": "initial_12",
        "protocol_version": "agent-config-ablation-v1",
        "model": "scripted",
        "reasoning_effort": None,
        "prompt_version": "agent-graph-v5",
        "agent_mode": mode,
        "complete": True,
        "case_count": 2,
        "verified": verified,
        "usage": {
            "total_tokens": 10 + verified,
            "model_calls": 8 + verified,
            "tool_calls": 12,
            "latency_ms": 100,
        },
        "cases": [
            {"case_id": case_id, "status": status}
            for case_id, status in zip(("case_001", "case_002"), statuses)
        ],
    }


class ConfigurationAblationTests(unittest.TestCase):
    def test_compares_three_matched_memory_free_modes(self) -> None:
        with temporary_directory() as temp_dir:
            root = Path(temp_dir)
            runs = [_write_run(root, mode) for mode in MODES]
            output = root / "comparison"

            with patch(
                "repomedic.configuration_ablation.summarize_benchmark",
                side_effect=[
                    _summary("single_agent", 0),
                    _summary("multi_agent_no_review", 1),
                    _summary("multi_agent_review", 2),
                ],
            ):
                report = compare_agent_configurations(*runs, output_dir=output)

            self.assertEqual(report["configurations"][2]["verified"], 2)
            self.assertEqual(report["comparisons"][1]["verified_tasks_delta"], 1)
            self.assertTrue((output / "summary.json").is_file())
            self.assertTrue((output / "summary.md").is_file())

    def test_rejects_enabled_memory(self) -> None:
        with temporary_directory() as temp_dir:
            root = Path(temp_dir)
            runs = [_write_run(root, mode) for mode in MODES]
            record = json.loads((runs[1] / "benchmark.json").read_text(encoding="utf-8"))
            record["memory"]["enabled"] = True
            (runs[1] / "benchmark.json").write_text(
                json.dumps(record), encoding="utf-8"
            )

            with patch(
                "repomedic.configuration_ablation.summarize_benchmark",
                side_effect=[
                    _summary("single_agent", 0),
                    _summary("multi_agent_no_review", 1),
                    _summary("multi_agent_review", 2),
                ],
            ):
                with self.assertRaisesRegex(ValueError, "disable memory"):
                    compare_agent_configurations(*runs, output_dir=root / "comparison")

    def test_rejects_mismatched_case_order(self) -> None:
        with temporary_directory() as temp_dir:
            root = Path(temp_dir)
            runs = [_write_run(root, mode) for mode in MODES]
            record = json.loads((runs[2] / "benchmark.json").read_text(encoding="utf-8"))
            record["case_runs"].reverse()
            (runs[2] / "benchmark.json").write_text(
                json.dumps(record), encoding="utf-8"
            )

            with patch(
                "repomedic.configuration_ablation.summarize_benchmark",
                side_effect=[
                    _summary("single_agent", 0),
                    _summary("multi_agent_no_review", 1),
                    _summary("multi_agent_review", 2),
                ],
            ):
                with self.assertRaisesRegex(ValueError, "same ordered case IDs"):
                    compare_agent_configurations(*runs, output_dir=root / "comparison")


if __name__ == "__main__":
    unittest.main()
