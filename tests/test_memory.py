from pathlib import Path
import json
import unittest

from repomedic.artifacts import ArtifactWriter
from repomedic.memory import (
    EpisodicMemoryStore,
    MemoryEvidenceError,
    memory_prompt_chars,
    pack_memory_matches,
)
from tests.helpers import temporary_directory


def _verified_run(
    root: Path,
    *,
    case_id: str,
    fixture_id: str,
    issue: str,
    root_cause: str,
    approved: bool = True,
    status: str = "verified",
) -> Path:
    run_dir = root / case_id / "attempt_1"
    writer = ArtifactWriter(run_dir)
    writer.write_json(
        "config.json",
        {
            "case_id": case_id,
            "fixture": {"fixture_id": fixture_id, "version": f"{fixture_id}-v1"},
            "issue": issue,
        },
    )
    writer.write_json(
        "investigation.json",
        {
            "root_cause": root_cause,
            "evidence": [{"path": f"{fixture_id}/service.py"}],
        },
    )
    writer.write_json("review.json", {"feedback": "Apply the validated repair."})
    writer.write_json(
        "policy.json",
        {
            "compliant": True,
            "changes": [{"path": f"{fixture_id}/service.py", "kind": "modified"}],
            "violations": [],
        },
    )
    writer.write_json(
        "test-results.json",
        {
            "results": [
                {
                    "kind": "public",
                    "exit_code": 0,
                    "timed_out": False,
                    "infrastructure_error": False,
                    "stderr": "public output",
                },
                {
                    "kind": "evaluator",
                    "exit_code": 0,
                    "timed_out": False,
                    "infrastructure_error": False,
                    "stderr": "HIDDEN_SENTINEL",
                },
            ]
        },
    )
    if approved:
        writer.append_trace("approval_decided", {"action": "approve"})
    writer.append_trace("run_completed", {"status": status})
    writer.write_text(
        "final-report.md",
        f"# Run\n\n- Status: `{status}`\n",
    )
    return run_dir


class EpisodicMemoryStoreTests(unittest.TestCase):
    def test_records_verified_run_idempotently_without_hidden_output(self) -> None:
        with temporary_directory() as temp_dir:
            root = Path(temp_dir)
            run_dir = _verified_run(
                root,
                case_id="document_001",
                fixture_id="document",
                issue="Normalize whitespace in a document.",
                root_cause="Literal splitting ignores tabs.",
            )
            store = EpisodicMemoryStore(root / "memory.sqlite")

            first = store.record_verified_run(run_dir)
            second = store.record_verified_run(run_dir)

            self.assertEqual(first.entry_id, second.entry_id)
            self.assertEqual(store.count(), 1)
            self.assertEqual(first.changed_paths, ("document/service.py",))
            database_bytes = (root / "memory.sqlite").read_bytes()
            self.assertNotIn(b"HIDDEN_SENTINEL", database_bytes)

    def test_rejects_unverified_or_unapproved_runs(self) -> None:
        with temporary_directory() as temp_dir:
            root = Path(temp_dir)
            store = EpisodicMemoryStore(root / "memory.sqlite")
            failed = _verified_run(
                root,
                case_id="failed_case",
                fixture_id="document",
                issue="A failed issue.",
                root_cause="A failed root cause.",
                status="tests_failed",
            )
            unapproved = _verified_run(
                root,
                case_id="unapproved_case",
                fixture_id="document",
                issue="An unapproved issue.",
                root_cause="An unapproved root cause.",
                approved=False,
            )

            with self.assertRaisesRegex(MemoryEvidenceError, "only verified"):
                store.record_verified_run(failed)
            with self.assertRaisesRegex(MemoryEvidenceError, "approved patch"):
                store.record_verified_run(unapproved)
            self.assertEqual(store.count(), 0)

    def test_search_is_deterministic_ranked_and_excludes_current_case(self) -> None:
        with temporary_directory() as temp_dir:
            root = Path(temp_dir)
            store = EpisodicMemoryStore(root / "memory.sqlite")
            document = _verified_run(
                root,
                case_id="document_001",
                fixture_id="document",
                issue="Normalize whitespace in document paragraphs.",
                root_cause="Literal space splitting leaves tabs unchanged.",
            )
            order = _verified_run(
                root,
                case_id="order_001",
                fixture_id="order",
                issue="Restore inventory after a failed order.",
                root_cause="Reservation happens before validation.",
            )
            store.record_verified_run(document)
            store.record_verified_run(order)

            matches = store.search(
                "Document title whitespace keeps tabs",
                fixture_id="document",
                exclude_case_id="document_999",
            )
            excluded = store.search(
                "Document title whitespace keeps tabs",
                fixture_id="document",
                exclude_case_id="document_001",
            )

            self.assertEqual(matches[0].entry.case_id, "document_001")
            self.assertEqual(matches, store.search(
                "Document title whitespace keeps tabs",
                fixture_id="document",
                exclude_case_id="document_999",
            ))
            self.assertNotIn("document_001", [item.entry.case_id for item in excluded])
            prompt = matches[0].prompt_value()
            self.assertEqual(prompt["provenance"]["run_id"], "attempt_1")
            self.assertNotIn("HIDDEN_SENTINEL", json.dumps(prompt))

    def test_snapshot_identifies_logical_corpus_content(self) -> None:
        with temporary_directory() as temp_dir:
            root = Path(temp_dir)
            store = EpisodicMemoryStore(root / "memory.sqlite")
            empty = store.snapshot()
            run_dir = _verified_run(
                root,
                case_id="document_001",
                fixture_id="document",
                issue="Normalize whitespace in a document.",
                root_cause="Literal splitting ignores tabs.",
            )

            store.record_verified_run(run_dir)
            populated = store.snapshot()

            self.assertEqual(empty.entry_count, 0)
            self.assertEqual(populated.entry_count, 1)
            self.assertNotEqual(empty.content_hash, populated.content_hash)
            self.assertEqual(populated, store.snapshot())

    def test_prompt_packing_enforces_total_and_per_field_limits(self) -> None:
        with temporary_directory() as temp_dir:
            root = Path(temp_dir)
            store = EpisodicMemoryStore(root / "memory.sqlite")
            run_dir = _verified_run(
                root,
                case_id="document_001",
                fixture_id="document",
                issue="Normalize " + "very verbose whitespace details " * 100,
                root_cause="Literal splitting " + "misses separators " * 100,
            )
            store.record_verified_run(run_dir)
            matches = store.search(
                "normalize whitespace",
                fixture_id="document",
                exclude_case_id="document_999",
            )

            packed = pack_memory_matches(matches, context_budget_chars=1200)

            self.assertTrue(packed)
            self.assertLessEqual(memory_prompt_chars(packed), 1200)
            self.assertLessEqual(len(packed[0]["lesson"]["issue"]), 300)
            self.assertTrue(packed[0]["lesson"]["issue"].endswith("..."))
            with self.assertRaisesRegex(ValueError, "between 512 and 20000"):
                pack_memory_matches(matches, context_budget_chars=511)


if __name__ == "__main__":
    unittest.main()
