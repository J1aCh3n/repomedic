from dataclasses import dataclass


@dataclass(frozen=True)
class CommandSpec:
    argv: tuple[str, ...]
    cwd: str


@dataclass(frozen=True)
class FixtureIdentity:
    fixture_id: str
    version: str


@dataclass(frozen=True)
class Limits:
    wall_time_seconds: int
    tool_calls: int
    repair_iterations: int


@dataclass(frozen=True)
class CaseManifest:
    case_id: str
    category: str
    fixture: FixtureIdentity
    issue: str
    runnable_entry_point: CommandSpec
    public_test: CommandSpec
    evaluator_test: CommandSpec
    allowed_paths: tuple[str, ...]
    forbidden_paths: tuple[str, ...]
    limits: Limits
    expected_behavior: tuple[str, ...]


@dataclass(frozen=True)
class Change:
    path: str
    kind: str


@dataclass(frozen=True)
class PolicyViolation:
    path: str
    reason: str


@dataclass(frozen=True)
class PolicyReport:
    compliant: bool
    changes: tuple[Change, ...]
    violations: tuple[PolicyViolation, ...]


@dataclass(frozen=True)
class TestResult:
    kind: str
    argv: tuple[str, ...]
    exit_code: int | None
    timed_out: bool
    duration_ms: int
    stdout: str
    stderr: str
    infrastructure_error: bool


@dataclass(frozen=True)
class RunOutcome:
    case_id: str
    run_id: str
    run_dir: str
    status: str
    results: tuple[TestResult, ...]
    policy: PolicyReport

