from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
import uuid

from repomedic.artifacts import ArtifactWriter
from repomedic.manifest import load_manifest
from repomedic.models import CaseManifest, CommandSpec, RunOutcome, TestResult
from repomedic.policy import build_patch, collect_changes, evaluate_policy
from repomedic.sandbox import DockerSandbox
from repomedic.workspace import RunLayout, reset_workspace


class RunStateError(RuntimeError):
    """Raised when a prepared run is evaluated more than once."""


@dataclass(frozen=True)
class PreparedRun:
    manifest: CaseManifest
    layout: RunLayout
    source_repo: Path
    evaluator_dir: Path


class SandboxRunner(Protocol):
    backend: str
    image: str

    def run(
        self,
        *,
        case_id: str,
        run_id: str,
        workspace: Path,
        evaluator_dir: Path | None,
        spec: CommandSpec,
        kind: str,
        timeout_seconds: int,
    ) -> TestResult: ...


def _new_run_id() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{uuid.uuid4().hex[:8]}"


def _status(results: tuple[TestResult, ...], policy_compliant: bool) -> str:
    if any(result.infrastructure_error for result in results):
        return "infrastructure_error"
    if not policy_compliant:
        return "policy_violation"
    if not results:
        return "infrastructure_error"
    if all(result.exit_code == 0 and not result.timed_out for result in results):
        return "verified"
    return "tests_failed"


def _final_report(outcome: RunOutcome) -> str:
    lines = [
        f"# RepoMedic run {outcome.run_id}",
        "",
        f"- Case: `{outcome.case_id}`",
        f"- Status: `{outcome.status}`",
        f"- Policy compliant: `{str(outcome.policy.compliant).lower()}`",
        f"- Changed paths: `{len(outcome.policy.changes)}`",
        "",
        "## Test results",
        "",
    ]
    for result in outcome.results:
        exit_code = "timeout" if result.timed_out else str(result.exit_code)
        lines.append(
            f"- `{result.kind}`: exit `{exit_code}`, {result.duration_ms} ms, "
            f"infrastructure error `{str(result.infrastructure_error).lower()}`"
        )
    if outcome.policy.violations:
        lines.extend(["", "## Policy violations", ""])
        lines.extend(
            f"- `{violation.path}`: {violation.reason}"
            for violation in outcome.policy.violations
        )
    lines.append("")
    return "\n".join(lines)


class DeterministicHarness:
    def __init__(self, sandbox: SandboxRunner | None = None) -> None:
        self.sandbox = sandbox or DockerSandbox()

    def prepare_case(
        self,
        case_dir: Path,
        runs_root: Path,
        *,
        run_id: str | None = None,
    ) -> PreparedRun:
        resolved_case = case_dir.resolve()
        manifest = load_manifest(resolved_case / "manifest.yaml")
        layout = RunLayout.create(
            runs_root,
            manifest.case_id,
            run_id or _new_run_id(),
        )
        writer = ArtifactWriter(layout.run_dir)
        source_repo = resolved_case / "repo"
        evaluator_dir = resolved_case / "evaluator"

        writer.write_json(
            "config.json",
            {
                "case_id": manifest.case_id,
                "fixture": asdict(manifest.fixture),
                "issue": manifest.issue,
                "limits": asdict(manifest.limits),
                "paths": {
                    "allowed": manifest.allowed_paths,
                    "forbidden": manifest.forbidden_paths,
                },
                "sandbox": {
                    "backend": self.sandbox.backend,
                    "image": self.sandbox.image,
                    "network": "none",
                    "root_filesystem": "read-only",
                },
            },
        )
        reset_workspace(source_repo, layout)
        writer.append_trace(
            "workspace_prepared",
            {"case_id": manifest.case_id, "workspace": "workspace"},
        )
        return PreparedRun(
            manifest=manifest,
            layout=layout,
            source_repo=source_repo,
            evaluator_dir=evaluator_dir,
        )

    def evaluate(self, prepared: PreparedRun) -> RunOutcome:
        manifest = prepared.manifest
        layout = prepared.layout
        source_repo = prepared.source_repo
        evaluator_dir = prepared.evaluator_dir
        writer = ArtifactWriter(layout.run_dir)
        if (layout.run_dir / "final-report.md").exists():
            raise RunStateError(f"run has already been evaluated: {layout.run_id}")

        results_list: list[TestResult] = []
        changes = collect_changes(source_repo, layout.workspace)
        policy = evaluate_policy(
            changes,
            allowed=manifest.allowed_paths,
            forbidden=manifest.forbidden_paths,
        )
        writer.append_trace(
            "policy_checked",
            {
                "stage": "before_tests",
                "compliant": policy.compliant,
                "changes": [asdict(change) for change in policy.changes],
                "violations": [asdict(item) for item in policy.violations],
            },
        )

        if policy.compliant:
            public_result = self.sandbox.run(
                case_id=manifest.case_id,
                run_id=layout.run_id,
                workspace=layout.workspace,
                evaluator_dir=None,
                spec=manifest.public_test,
                kind="public",
                timeout_seconds=manifest.limits.wall_time_seconds,
            )
            results_list.append(public_result)
            writer.append_trace("test_completed", asdict(public_result))

            changes = collect_changes(source_repo, layout.workspace)
            policy = evaluate_policy(
                changes,
                allowed=manifest.allowed_paths,
                forbidden=manifest.forbidden_paths,
            )
            if policy.compliant:
                evaluator_result = self.sandbox.run(
                    case_id=manifest.case_id,
                    run_id=layout.run_id,
                    workspace=layout.workspace,
                    evaluator_dir=evaluator_dir,
                    spec=manifest.evaluator_test,
                    kind="evaluator",
                    timeout_seconds=manifest.limits.wall_time_seconds,
                )
                results_list.append(evaluator_result)
                writer.append_trace("test_completed", asdict(evaluator_result))

        changes = collect_changes(source_repo, layout.workspace)
        policy = evaluate_policy(
            changes,
            allowed=manifest.allowed_paths,
            forbidden=manifest.forbidden_paths,
        )
        writer.append_trace(
            "policy_checked",
            {
                "stage": "after_tests",
                "compliant": policy.compliant,
                "changes": [asdict(change) for change in policy.changes],
                "violations": [asdict(item) for item in policy.violations],
            },
        )
        results = tuple(results_list)
        status = _status(results, policy.compliant)
        outcome = RunOutcome(
            case_id=manifest.case_id,
            run_id=layout.run_id,
            run_dir=str(layout.run_dir),
            status=status,
            results=results,
            policy=policy,
        )

        writer.write_json(
            "test-results.json", {"results": [asdict(result) for result in results]}
        )
        writer.write_json("policy.json", asdict(policy))
        writer.write_text("patch.diff", build_patch(source_repo, layout.workspace, changes))
        writer.write_text("final-report.md", _final_report(outcome))
        writer.append_trace("run_completed", {"status": status})
        return outcome

    def run_case(
        self,
        case_dir: Path,
        runs_root: Path,
        *,
        run_id: str | None = None,
    ) -> RunOutcome:
        prepared = self.prepare_case(case_dir, runs_root, run_id=run_id)
        return self.evaluate(prepared)
