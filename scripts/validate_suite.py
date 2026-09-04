"""Validate a benchmark suite's clean, faulty, and reference-repaired states."""

from argparse import ArgumentParser
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
import os
import subprocess
import uuid

from repomedic.artifacts import ArtifactWriter
from repomedic.benchmark import load_suite
from repomedic.harness import DeterministicHarness


def _run_id() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{uuid.uuid4().hex[:8]}"


def _report(summary: dict[str, object]) -> str:
    lines = [
        f"# Fixture gate: {summary['suite_id']}",
        "",
        f"- Status: `{summary['status']}`",
        f"- Cases: `{summary['case_count']}`",
        f"- Clean fixtures verified: `{summary['clean_fixtures_verified']}`",
        f"- Faults reproduced: `{summary['faults_reproduced']}`",
        f"- Reference repairs verified: `{summary['references_verified']}`",
        "",
        "## Cases",
        "",
    ]
    for case in summary["cases"]:
        lines.append(
            f"- `{case['case_id']}` ({case['category']}): faulty "
            f"`{case['faulty_status']}`, reference `{case['reference_status']}`"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("suite", type=Path)
    parser.add_argument(
        "--runs-root", type=Path, default=Path("runs") / "suite-gates"
    )
    parser.add_argument("--run-id")
    args = parser.parse_args()

    suite = load_suite(args.suite)
    gate_dir = (
        args.runs_root.resolve() / suite.suite_id / (args.run_id or _run_id())
    )
    gate_dir.mkdir(parents=True, exist_ok=False)
    harness = DeterministicHarness()
    rows: list[dict[str, object]] = []
    fixtures: dict[str, dict[str, object]] = {}

    benchmark_root = suite.suite_path.parent.parent
    for case in suite.cases:
        fixture_id = case.manifest.fixture.fixture_id
        if fixture_id in fixtures:
            continue
        fixture_dir = benchmark_root / "fixtures" / fixture_id
        result = harness.sandbox.run(
            case_id=f"{fixture_id}_clean",
            run_id=gate_dir.name,
            workspace=fixture_dir,
            evaluator_dir=None,
            spec=case.manifest.public_test,
            kind="public",
            timeout_seconds=case.manifest.limits.wall_time_seconds,
        )
        fixtures[fixture_id] = asdict(result)

    for case in suite.cases:
        faulty = harness.run_case(
            case.case_dir,
            gate_dir / "faulty",
            run_id="input",
        )
        repaired = harness.prepare_case(
            case.case_dir,
            gate_dir / "reference",
            run_id="repair",
        )
        patch_path = case.case_dir / "evaluator" / "reference.patch"
        git_environment = {
            name: os.environ[name]
            for name in ("PATH", "SYSTEMROOT", "TEMP", "TMP", "TMPDIR")
            if name in os.environ
        }
        git_environment["GIT_CEILING_DIRECTORIES"] = str(
            repaired.layout.workspace.parent.resolve()
        )
        applied = subprocess.run(
            ["git", "apply", str(patch_path.resolve())],
            cwd=repaired.layout.workspace,
            env=git_environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if applied.returncode != 0:
            raise RuntimeError(
                f"reference patch failed for {case.case_id}: {applied.stderr}"
            )
        reference = harness.evaluate(repaired)
        rows.append(
            {
                "case_id": case.case_id,
                "category": case.manifest.category,
                "faulty_status": faulty.status,
                "reference_status": reference.status,
                "faulty_run_dir": faulty.run_dir,
                "reference_run_dir": reference.run_dir,
            }
        )

    faults_reproduced = sum(row["faulty_status"] == "tests_failed" for row in rows)
    references_verified = sum(row["reference_status"] == "verified" for row in rows)
    clean_fixtures_verified = sum(
        result["exit_code"] == 0
        and not result["timed_out"]
        and not result["infrastructure_error"]
        for result in fixtures.values()
    )
    status = (
        "verified"
        if clean_fixtures_verified == len(fixtures)
        and faults_reproduced == len(rows)
        and references_verified == len(rows)
        else "failed"
    )
    summary: dict[str, object] = {
        "suite_id": suite.suite_id,
        "suite_path": str(suite.suite_path),
        "split": suite.split,
        "status": status,
        "case_count": len(rows),
        "clean_fixtures_verified": clean_fixtures_verified,
        "fixtures": fixtures,
        "faults_reproduced": faults_reproduced,
        "references_verified": references_verified,
        "cases": rows,
        "sandbox": {
            "backend": harness.sandbox.backend,
            "image": harness.sandbox.image,
        },
    }
    writer = ArtifactWriter(gate_dir)
    writer.write_json("summary.json", summary)
    writer.write_text("summary.md", _report(summary))
    print(f"status={status}")
    print(f"run_dir={gate_dir}")
    raise SystemExit(0 if status == "verified" else 1)


if __name__ == "__main__":
    main()
