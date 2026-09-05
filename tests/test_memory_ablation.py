from pathlib import Path
from unittest.mock import patch
import json
import unittest

from repomedic.memory_ablation import compare_memory_ablation
from tests.helpers import temporary_directory


def _summary(*, verified: int, statuses: list[str]) -> dict:
    return {
        "suite_id": "family_suite",
        "protocol_version": "multi-agent-memory-v2",
        "model": "scripted",
        "reasoning_effort": None,
        "prompt_version": "agent-graph-v4",
        "complete": True,
        "case_count": 2,
        "verified": verified,
        "status_counts": {},
        "usage": {
            "input_tokens": 10,
            "output_tokens": 5,
            "total_tokens": 15 + verified,
            "model_calls": 8,
            "tool_calls": 12,
            "latency_ms": 100 + verified,
            "average_model_latency_ms": 12,
        },
        "cases": [
            {"case_id": "family_001", "status": statuses[0]},
            {"case_id": "family_002", "status": statuses[1]},
        ],
    }


def _write_benchmark(run_dir: Path, *, memory_enabled: bool) -> None:
    run_dir.mkdir()
    corpus = (
        {"entry_count": 2, "content_hash": "a" * 64}
        if memory_enabled
        else None
    )
    record = {
        "memory": {
            "enabled": memory_enabled,
            "context_budget_chars": 2400,
            "corpus": corpus,
        },
        "case_runs": [
            {
                "case_id": "family_001",
                "run_id": "attempt_1",
                "run_dir": "cases/family_001/attempt_1",
            },
            {
                "case_id": "family_002",
                "run_id": "attempt_1",
                "run_dir": "cases/family_002/attempt_1",
            },
        ],
    }
    (run_dir / "benchmark.json").write_text(json.dumps(record), encoding="utf-8")
    for case in record["case_runs"]:
        case_dir = run_dir / case["run_dir"]
        case_dir.mkdir(parents=True)
        retrieved = (
            [
                {
                    "entry_id": f"entry-for-{case['case_id']}",
                    "score": 4,
                    "provenance": {
                        "fixture_id": "family",
                        "fixture_version": "family-v1",
                        "case_id": "family_prior",
                        "run_id": "prior_run",
                    },
                    "lesson": {},
                }
            ]
            if memory_enabled
            else []
        )
        (case_dir / "memory.json").write_text(
            json.dumps(
                {
                    "retrieved": retrieved,
                    "context_budget_chars": 2400,
                    "context_chars": len(
                        json.dumps(
                            retrieved,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                    )
                    if retrieved
                    else 0,
                    "corpus": corpus,
                }
            ),
            encoding="utf-8",
        )


class MemoryAblationTests(unittest.TestCase):
    def test_compares_matched_runs_and_writes_provenance_report(self) -> None:
        with temporary_directory() as temp_dir:
            root = Path(temp_dir)
            baseline = root / "baseline"
            treatment = root / "treatment"
            output = root / "comparison"
            _write_benchmark(baseline, memory_enabled=False)
            _write_benchmark(treatment, memory_enabled=True)

            with patch(
                "repomedic.memory_ablation.summarize_benchmark",
                side_effect=[
                    _summary(verified=1, statuses=["verified", "tests_failed"]),
                    _summary(verified=2, statuses=["verified", "verified"]),
                ],
            ):
                report = compare_memory_ablation(baseline, treatment, output)

            self.assertEqual(report["delta"]["verified_tasks"], 1)
            self.assertEqual(report["delta"]["verified_rate"], 0.5)
            self.assertEqual(report["memory_evidence"]["covered_cases"], 2)
            self.assertEqual(report["memory_evidence"]["retrieval_count"], 2)
            self.assertTrue((output / "summary.json").is_file())
            self.assertIn(
                "Verified-task uplift: `+1`",
                (output / "summary.md").read_text(encoding="utf-8"),
            )

    def test_rejects_treatment_that_did_not_retrieve_memory(self) -> None:
        with temporary_directory() as temp_dir:
            root = Path(temp_dir)
            baseline = root / "baseline"
            treatment = root / "treatment"
            _write_benchmark(baseline, memory_enabled=False)
            _write_benchmark(treatment, memory_enabled=True)
            for artifact in treatment.glob("cases/*/*/memory.json"):
                payload = json.loads(artifact.read_text(encoding="utf-8"))
                payload["retrieved"] = []
                payload["context_chars"] = 0
                artifact.write_text(json.dumps(payload), encoding="utf-8")

            with patch(
                "repomedic.memory_ablation.summarize_benchmark",
                side_effect=[
                    _summary(verified=1, statuses=["verified", "tests_failed"]),
                    _summary(verified=1, statuses=["verified", "tests_failed"]),
                ],
            ):
                with self.assertRaisesRegex(ValueError, "retrieved no entries"):
                    compare_memory_ablation(baseline, treatment, root / "comparison")

    def test_rejects_treatment_with_changed_corpus_or_invalid_budget_evidence(self) -> None:
        for field, value, message in (
            ("corpus", {"entry_count": 3, "content_hash": "b" * 64}, "corpus"),
            ("context_chars", 2401, "context"),
        ):
            with self.subTest(field=field):
                with temporary_directory() as temp_dir:
                    root = Path(temp_dir)
                    baseline = root / "baseline"
                    treatment = root / "treatment"
                    _write_benchmark(baseline, memory_enabled=False)
                    _write_benchmark(treatment, memory_enabled=True)
                    artifact = next(treatment.glob("cases/*/*/memory.json"))
                    payload = json.loads(artifact.read_text(encoding="utf-8"))
                    payload[field] = value
                    artifact.write_text(json.dumps(payload), encoding="utf-8")

                    with patch(
                        "repomedic.memory_ablation.summarize_benchmark",
                        side_effect=[
                            _summary(verified=1, statuses=["verified", "tests_failed"]),
                            _summary(verified=1, statuses=["verified", "tests_failed"]),
                        ],
                    ):
                        with self.assertRaisesRegex(ValueError, message):
                            compare_memory_ablation(
                                baseline, treatment, root / "comparison"
                            )


if __name__ == "__main__":
    unittest.main()
