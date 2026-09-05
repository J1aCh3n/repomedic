from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, TypedDict, cast
import json

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import ValidationError

from repomedic.agent_schemas import (
    ApprovalDecision,
    CodeProposal,
    InvestigationReport,
    InvestigationRequest,
    PlanReport,
    ReviewReport,
)
from repomedic.agent_tools import (
    RepositoryTools,
    ToolBudgetExceeded,
    ToolExecutionError,
)
from repomedic.artifacts import ArtifactWriter
from repomedic.harness import DeterministicHarness, PreparedRun
from repomedic.manifest import load_manifest
from repomedic.memory import (
    DEFAULT_MEMORY_CONTEXT_BUDGET_CHARS,
    EpisodicMemoryStore,
    memory_prompt_chars,
    pack_memory_matches,
    validate_memory_context_budget,
)
from repomedic.model_clients import (
    ModelClientError,
    ModelResult,
    ModelUsage,
    StructuredModel,
)
from repomedic.models import Change, PolicyReport, RunOutcome, TestResult
from repomedic.policy import build_patch, collect_changes, evaluate_policy
from repomedic.prompts import (
    CODER_PROMPT,
    INVESTIGATOR_REPORT_PROMPT,
    INVESTIGATOR_SELECT_PROMPT,
    PLANNER_PROMPT,
    PROMPT_VERSION,
    REVIEWER_PROMPT,
    SINGLE_AGENT_PROMPT,
)
from repomedic.workspace import RunLayout


class AgentState(TypedDict, total=False):
    case_id: str
    run_id: str
    case_dir: str
    run_dir: str
    workspace: str
    source_repo: str
    evaluator_dir: str
    issue: str
    expected_behavior: list[str]
    allowed_paths: list[str]
    forbidden_paths: list[str]
    wall_time_seconds: int
    tool_limit: int
    repair_limit: int
    status: str
    error: str
    iterations: int
    tool_calls: int
    model_calls: int
    usage: dict[str, int]
    repository_files: list[str]
    plan: dict[str, Any]
    investigation: dict[str, Any]
    proposal: dict[str, Any]
    proposal_diff: str
    approval: dict[str, Any]
    public_result: dict[str, Any]
    policy: dict[str, Any]
    review: dict[str, Any]
    review_feedback: str
    memory_lessons: list[dict[str, Any]]
    memory: dict[str, Any]


AgentMode = Literal[
    "single_agent",
    "multi_agent_no_review",
    "multi_agent_review",
]
AGENT_MODES: tuple[AgentMode, ...] = (
    "single_agent",
    "multi_agent_no_review",
    "multi_agent_review",
)
DEFAULT_AGENT_MODE: AgentMode = "multi_agent_review"


@dataclass(frozen=True)
class AgentRunResult:
    case_id: str
    run_id: str
    run_dir: str
    status: str
    awaiting_approval: bool
    proposal: dict[str, Any] | None
    proposal_diff: str | None
    error: str | None
    usage: dict[str, int] | None = None
    memory: dict[str, Any] | None = None


def _thread_config(run_id: str) -> dict[str, dict[str, str]]:
    return {"configurable": {"thread_id": run_id}}


def _add_model_usage(
    usage: dict[str, int], model_usage: ModelUsage
) -> dict[str, int]:
    updated = dict(usage)
    updated["input_tokens"] += model_usage.input_tokens
    updated["output_tokens"] += model_usage.output_tokens
    updated["total_tokens"] += model_usage.total_tokens
    updated["latency_ms"] += model_usage.latency_ms
    updated["calls"] += 1
    return updated


def _add_usage(
    usage: dict[str, int], result: ModelResult[Any]
) -> dict[str, int]:
    return _add_model_usage(usage, result.usage)


class AgentGraphRunner:
    def __init__(
        self,
        model: StructuredModel,
        harness: DeterministicHarness,
        checkpointer: Any,
        memory_store: EpisodicMemoryStore | None = None,
        memory_limit: int = 3,
        memory_context_budget_chars: int = DEFAULT_MEMORY_CONTEXT_BUDGET_CHARS,
        memory_write_enabled: bool = True,
        agent_mode: AgentMode = DEFAULT_AGENT_MODE,
    ) -> None:
        if not 1 <= memory_limit <= 10:
            raise ValueError("memory limit must be between 1 and 10")
        validate_memory_context_budget(memory_context_budget_chars)
        if not isinstance(memory_write_enabled, bool):
            raise ValueError("memory write setting must be a boolean")
        if agent_mode not in AGENT_MODES:
            raise ValueError(f"unsupported agent mode: {agent_mode!r}")
        self.model = model
        self.harness = harness
        self.checkpointer = checkpointer
        self.memory_store = memory_store
        self.memory_limit = memory_limit
        self.memory_context_budget_chars = memory_context_budget_chars
        self.memory_write_enabled = memory_write_enabled
        self.agent_mode = agent_mode
        self.graph = self._compile()

    def _compile(self) -> Any:
        builder = StateGraph(AgentState)
        builder.add_node("planner", self._planner)
        builder.add_node("investigator", self._investigator)
        builder.add_node("coder", self._coder)
        builder.add_node("approval", self._approval)
        builder.add_node("apply", self._apply)
        builder.add_node("test", self._test)
        builder.add_node("reviewer", self._reviewer)
        builder.add_node("finalize", self._finalize)
        builder.add_node("terminal", self._terminal)
        builder.add_edge(START, "planner")
        builder.add_conditional_edges(
            "planner", self._route_running, {"next": "investigator", "terminal": "terminal"}
        )
        builder.add_conditional_edges(
            "investigator", self._route_running, {"next": "coder", "terminal": "terminal"}
        )
        builder.add_conditional_edges(
            "coder", self._route_running, {"next": "approval", "terminal": "terminal"}
        )
        builder.add_conditional_edges(
            "approval",
            self._route_approval,
            {"apply": "apply", "coder": "coder", "terminal": "terminal"},
        )
        builder.add_conditional_edges(
            "apply", self._route_running, {"next": "test", "terminal": "terminal"}
        )
        builder.add_conditional_edges(
            "test",
            self._route_test,
            {"reviewer": "reviewer", "finalize": "finalize", "terminal": "terminal"},
        )
        builder.add_conditional_edges(
            "reviewer",
            self._route_review,
            {
                "finalize": "finalize",
                "coder": "coder",
                "planner": "planner",
                "terminal": "terminal",
            },
        )
        builder.add_edge("finalize", END)
        builder.add_edge("terminal", END)
        return builder.compile(checkpointer=self.checkpointer)

    @staticmethod
    def _route_running(state: AgentState) -> str:
        return "next" if state["status"] == "running" else "terminal"

    @staticmethod
    def _route_approval(state: AgentState) -> str:
        if state["status"] != "running":
            return "terminal"
        action = state["approval"]["action"]
        return "apply" if action == "approve" else "coder"

    def _route_test(self, state: AgentState) -> str:
        if state["status"] != "running":
            return "terminal"
        if self.agent_mode == "multi_agent_no_review":
            return "finalize"
        return "reviewer"

    @staticmethod
    def _route_review(state: AgentState) -> str:
        if state["status"] != "running":
            return "terminal"
        return {
            "pass": "finalize",
            "revise": "coder",
            "replan": "planner",
        }.get(state["review"]["verdict"], "terminal")

    @staticmethod
    def _writer(state: AgentState) -> ArtifactWriter:
        return ArtifactWriter(Path(state["run_dir"]))

    @staticmethod
    def _tools(state: AgentState) -> RepositoryTools:
        return RepositoryTools(
            Path(state["workspace"]),
            allowed_paths=tuple(state["allowed_paths"]),
        )

    @staticmethod
    def _consume_tool(state: AgentState, count: int) -> int:
        if count >= state["tool_limit"]:
            raise ToolBudgetExceeded(
                f"tool-call limit exhausted ({state['tool_limit']})"
            )
        return count + 1

    def _model_agent(self, stage: str) -> str:
        return "repairer" if self.agent_mode == "single_agent" else stage

    def _generate(
        self,
        state: AgentState,
        *,
        agent: str,
        instructions: str,
        input_data: dict[str, Any],
        output_type: type[Any],
        usage: dict[str, int],
    ) -> tuple[Any, dict[str, int]]:
        model_agent = self._model_agent(agent)
        model_instructions = (
            SINGLE_AGENT_PROMPT
            if self.agent_mode == "single_agent"
            else instructions
        )
        try:
            result = self.model.generate(
                agent=model_agent,
                instructions=model_instructions,
                input_data=input_data,
                output_type=output_type,
            )
        except ModelClientError as error:
            updated = _add_model_usage(usage, error.usage)
            usage.clear()
            usage.update(updated)
            raise
        return result.output, _add_usage(usage, result)

    def _model_failure(
        self,
        state: AgentState,
        agent: str,
        error: Exception,
        *,
        usage: dict[str, int],
        tool_calls: int,
    ) -> AgentState:
        message = str(error)
        self._writer(state).append_trace(
            "model_failed",
            {"agent": self._model_agent(agent), "stage": agent, "error": message},
        )
        return {
            "status": "model_error",
            "error": message,
            "usage": usage,
            "model_calls": usage["calls"],
            "tool_calls": tool_calls,
        }

    def _tool_failure(
        self,
        state: AgentState,
        operation: str,
        error: Exception,
        *,
        usage: dict[str, int],
        tool_calls: int,
    ) -> AgentState:
        message = str(error)
        self._writer(state).append_trace(
            "tool_failed", {"operation": operation, "error": message}
        )
        return {
            "status": "tool_error",
            "error": message,
            "usage": usage,
            "model_calls": usage["calls"],
            "tool_calls": tool_calls,
        }

    def _planner(self, state: AgentState) -> AgentState:
        usage = dict(state["usage"])
        tool_calls = state["tool_calls"]
        try:
            tool_calls = self._consume_tool(state, tool_calls)
            files = self._tools(state).list_files()
            plan, usage = self._generate(
                state,
                agent="planner",
                instructions=PLANNER_PROMPT,
                input_data={
                    "issue": state["issue"],
                    "expected_behavior": state["expected_behavior"],
                    "repository_files": files,
                    "allowed_paths": state["allowed_paths"],
                    "memory_lessons": state["memory_lessons"],
                    "review_feedback": state.get("review_feedback", ""),
                },
                output_type=PlanReport,
                usage=usage,
            )
        except ModelClientError as error:
            return self._model_failure(
                state, "planner", error, usage=usage, tool_calls=tool_calls
            )
        except ToolExecutionError as error:
            return self._tool_failure(
                state, "list_files", error, usage=usage, tool_calls=tool_calls
            )
        data = plan.model_dump(mode="json")
        self._writer(state).write_json("plan.json", data)
        self._writer(state).append_trace(
            "agent_completed",
            {
                "agent": self._model_agent("planner"),
                "stage": "planner",
                "prompt_version": PROMPT_VERSION,
            },
        )
        return {
            "repository_files": list(files),
            "plan": data,
            "tool_calls": tool_calls,
            "model_calls": state["model_calls"] + 1,
            "usage": usage,
            "status": "running",
        }

    def _investigator(self, state: AgentState) -> AgentState:
        usage = dict(state["usage"])
        tool_calls = state["tool_calls"]
        try:
            selection, usage = self._generate(
                state,
                agent="investigator_select",
                instructions=INVESTIGATOR_SELECT_PROMPT,
                input_data={
                    "issue": state["issue"],
                    "plan": state["plan"],
                    "repository_files": state["repository_files"],
                },
                output_type=InvestigationRequest,
                usage=usage,
            )
            tools = self._tools(state)
            observations: list[dict[str, Any]] = []
            for query in selection.searches:
                tool_calls = self._consume_tool(state, tool_calls)
                observations.append(
                    {"operation": "search", "query": query, "matches": tools.search(query)}
                )
            for path in selection.reads:
                tool_calls = self._consume_tool(state, tool_calls)
                observations.append(
                    {"operation": "read", "path": path, "content": tools.read(path)}
                )
            report, usage = self._generate(
                state,
                agent="investigator",
                instructions=INVESTIGATOR_REPORT_PROMPT,
                input_data={
                    "issue": state["issue"],
                    "plan": state["plan"],
                    "tool_observations": observations,
                },
                output_type=InvestigationReport,
                usage=usage,
            )
        except ModelClientError as error:
            return self._model_failure(
                state, "investigator", error, usage=usage, tool_calls=tool_calls
            )
        except ToolExecutionError as error:
            return self._tool_failure(
                state, "investigation", error, usage=usage, tool_calls=tool_calls
            )
        data = report.model_dump(mode="json")
        self._writer(state).write_json("investigation.json", data)
        self._writer(state).append_trace(
            "agent_completed",
            {
                "agent": self._model_agent("investigator"),
                "stage": "investigator",
                "prompt_version": PROMPT_VERSION,
                "operations": len(selection.searches) + len(selection.reads),
            },
        )
        return {
            "investigation": data,
            "tool_calls": tool_calls,
            "model_calls": state["model_calls"] + 2,
            "usage": usage,
            "status": "running",
        }

    def _coder(self, state: AgentState) -> AgentState:
        usage = dict(state["usage"])
        tool_calls = state["tool_calls"]
        try:
            tools = self._tools(state)
            current_files: dict[str, str] = {}
            for path in state["investigation"]["relevant_files"]:
                tool_calls = self._consume_tool(state, tool_calls)
                current_files[path] = tools.read(path)
            proposal, usage = self._generate(
                state,
                agent="coder",
                instructions=CODER_PROMPT,
                input_data={
                    "issue": state["issue"],
                    "plan": state["plan"],
                    "investigation": state["investigation"],
                    "current_files": current_files,
                    "review_feedback": state.get("review_feedback", ""),
                    "allowed_paths": state["allowed_paths"],
                },
                output_type=CodeProposal,
                usage=usage,
            )
            tool_calls = self._consume_tool(state, tool_calls)
            preview = tools.preview(proposal.edits)
        except ModelClientError as error:
            return self._model_failure(
                state, "coder", error, usage=usage, tool_calls=tool_calls
            )
        except ToolExecutionError as error:
            return self._tool_failure(
                state,
                "proposal_preview",
                error,
                usage=usage,
                tool_calls=tool_calls,
            )
        data = proposal.model_dump(mode="json")
        self._writer(state).write_text("proposal.diff", preview)
        self._writer(state).append_trace(
            "agent_completed",
            {
                "agent": self._model_agent("coder"),
                "stage": "coder",
                "prompt_version": PROMPT_VERSION,
            },
        )
        return {
            "proposal": data,
            "proposal_diff": preview,
            "tool_calls": tool_calls,
            "model_calls": state["model_calls"] + 1,
            "usage": usage,
            "status": "running",
        }

    def _approval(self, state: AgentState) -> AgentState:
        raw = interrupt(
            {
                "type": "patch_approval",
                "case_id": state["case_id"],
                "run_id": state["run_id"],
                "proposal": state["proposal"],
                "diff": state["proposal_diff"],
                "allowed_decisions": ["approve", "revise", "reject"],
            }
        )
        try:
            decision = ApprovalDecision.model_validate(raw)
        except ValidationError as error:
            message = f"invalid approval decision: {error}"
            self._writer(state).append_trace("approval_failed", {"error": message})
            return {"status": "approval_error", "error": message}
        data = decision.model_dump(mode="json")
        self._writer(state).append_trace(
            "approval_decided", {"action": decision.action, "feedback": decision.feedback}
        )
        if decision.action == "reject":
            return {"approval": data, "status": "rejected"}
        if decision.action == "revise":
            return {
                "approval": data,
                "review_feedback": decision.feedback or "Human requested a revision.",
                "status": "running",
            }
        return {"approval": data, "status": "running"}

    def _apply(self, state: AgentState) -> AgentState:
        tool_calls = state["tool_calls"]
        try:
            proposal = CodeProposal.model_validate(state["proposal"])
            tool_calls = self._consume_tool(state, tool_calls)
            self._tools(state).apply(proposal.edits)
        except (ValidationError, ToolExecutionError) as error:
            return self._tool_failure(
                state,
                "apply_patch",
                error,
                usage=dict(state["usage"]),
                tool_calls=tool_calls,
            )
        iteration = state["iterations"] + 1
        self._writer(state).append_trace(
            "patch_applied", {"iteration": iteration, "edits": len(proposal.edits)}
        )
        return {
            "iterations": iteration,
            "tool_calls": tool_calls,
            "status": "running",
        }

    def _test(self, state: AgentState) -> AgentState:
        manifest = load_manifest(Path(state["case_dir"]) / "manifest.yaml")
        changes = collect_changes(Path(state["source_repo"]), Path(state["workspace"]))
        policy = evaluate_policy(
            changes,
            allowed=manifest.allowed_paths,
            forbidden=manifest.forbidden_paths,
        )
        writer = self._writer(state)
        writer.append_trace(
            "policy_checked",
            {"stage": f"agent_iteration_{state['iterations']}", **asdict(policy)},
        )
        if not policy.compliant:
            return {
                "policy": asdict(policy),
                "status": "policy_violation",
                "error": "proposed patch violates the manifest path policy",
            }
        result = self.harness.sandbox.run(
            case_id=state["case_id"],
            run_id=state["run_id"],
            workspace=Path(state["workspace"]),
            evaluator_dir=None,
            spec=manifest.public_test,
            kind="public",
            timeout_seconds=state["wall_time_seconds"],
        )
        writer.append_trace(
            "test_completed", {"iteration": state["iterations"], **asdict(result)}
        )
        if result.infrastructure_error or result.timed_out:
            return {
                "public_result": asdict(result),
                "policy": asdict(policy),
                "status": "infrastructure_error",
                "error": "public test infrastructure failed",
            }
        if self.agent_mode == "multi_agent_no_review" and result.exit_code != 0:
            return {
                "public_result": asdict(result),
                "policy": asdict(policy),
                "status": "tests_failed",
                "error": "public tests failed and Reviewer reflection is disabled",
            }
        return {
            "public_result": asdict(result),
            "policy": asdict(policy),
            "status": "running",
        }

    def _reviewer(self, state: AgentState) -> AgentState:
        usage = dict(state["usage"])
        changes = collect_changes(Path(state["source_repo"]), Path(state["workspace"]))
        diff = build_patch(Path(state["source_repo"]), Path(state["workspace"]), changes)
        try:
            review, usage = self._generate(
                state,
                agent="reviewer",
                instructions=REVIEWER_PROMPT,
                input_data={
                    "issue": state["issue"],
                    "expected_behavior": state["expected_behavior"],
                    "plan": state["plan"],
                    "investigation": state["investigation"],
                    "diff": diff,
                    "public_test": state["public_result"],
                    "policy": state["policy"],
                    "allowed_paths": state["allowed_paths"],
                    "forbidden_paths": state["forbidden_paths"],
                },
                output_type=ReviewReport,
                usage=usage,
            )
        except ModelClientError as error:
            return self._model_failure(
                state,
                "reviewer",
                error,
                usage=usage,
                tool_calls=state["tool_calls"],
            )
        data = review.model_dump(mode="json")
        self._writer(state).write_json("review.json", data)
        self._writer(state).append_trace(
            "agent_completed",
            {
                "agent": self._model_agent("reviewer"),
                "stage": "reviewer",
                "prompt_version": PROMPT_VERSION,
                "verdict": review.verdict,
                "requested_paths": list(review.requested_paths),
            },
        )
        updates: AgentState = {
            "review": data,
            "review_feedback": review.feedback,
            "model_calls": state["model_calls"] + 1,
            "usage": usage,
            "status": "running",
        }
        public = TestResult(**state["public_result"])
        review_path_error = self._review_path_error(state, review)
        if review_path_error:
            updates.update(status="review_error", error=review_path_error)
        elif review.verdict == "pass" and (
            public.exit_code != 0 or public.timed_out or public.infrastructure_error
        ):
            updates.update(
                status="review_error",
                error="Reviewer returned pass while the public tests were failing",
            )
        elif review.verdict in {"revise", "replan"} and state["iterations"] >= state["repair_limit"]:
            updates.update(
                status="iteration_exhausted",
                error=f"repair iteration limit exhausted ({state['repair_limit']})",
            )
        elif review.verdict == "stop":
            updates.update(status="stopped")
        return updates

    @staticmethod
    def _review_path_error(state: AgentState, review: ReviewReport) -> str | None:
        requested_paths = tuple(
            path.replace("\\", "/") for path in review.requested_paths
        )
        if review.verdict in {"revise", "replan"} and not requested_paths:
            return "Reviewer requested a revision without an actionable path"
        if review.verdict in {"pass", "stop"} and requested_paths:
            return f"Reviewer returned {review.verdict} with requested edit paths"
        if not requested_paths:
            return None
        policy = evaluate_policy(
            tuple(Change(path=path, kind="modified") for path in requested_paths),
            allowed=tuple(state["allowed_paths"]),
            forbidden=tuple(state["forbidden_paths"]),
        )
        if policy.compliant:
            return None
        paths = ", ".join(violation.path for violation in policy.violations)
        return f"Reviewer requested paths outside the edit policy: {paths}"

    @staticmethod
    def _prepared(state: AgentState) -> PreparedRun:
        run_dir = Path(state["run_dir"])
        layout = RunLayout(
            run_root=run_dir.parent.parent,
            run_dir=run_dir,
            workspace=Path(state["workspace"]),
            case_id=state["case_id"],
            run_id=state["run_id"],
        )
        return PreparedRun(
            manifest=load_manifest(Path(state["case_dir"]) / "manifest.yaml"),
            layout=layout,
            source_repo=Path(state["source_repo"]),
            evaluator_dir=Path(state["evaluator_dir"]),
        )

    def _finalize(self, state: AgentState) -> AgentState:
        final_report = Path(state["run_dir"]) / "final-report.md"
        if final_report.exists():
            status = self._status_from_report(final_report)
        else:
            outcome = self.harness.evaluate(self._prepared(state))
            status = outcome.status
        self._writer(state).write_json(
            "usage.json",
            {
                "model": self.model.model_id,
                "reasoning_effort": getattr(self.model, "reasoning_effort", None),
                "prompt_version": PROMPT_VERSION,
                "agent_mode": self.agent_mode,
                "model_calls": state["model_calls"],
                "tool_calls": state["tool_calls"],
                "repair_iterations": state["iterations"],
                **state["usage"],
            },
        )
        updates: AgentState = {"status": status}
        if (
            status == "verified"
            and self.memory_store is not None
            and self.memory_write_enabled
        ):
            entry = self.memory_store.record_verified_run(Path(state["run_dir"]))
            memory = dict(state["memory"])
            memory["write"] = {
                "status": "stored",
                "entry_id": entry.entry_id,
                "case_id": entry.case_id,
                "run_id": entry.run_id,
            }
            self._writer(state).write_json("memory.json", memory)
            self._writer(state).append_trace(
                "memory_written", {"entry_id": entry.entry_id}
            )
            updates["memory"] = memory
        return updates

    @staticmethod
    def _status_from_report(path: Path) -> str:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("- Status: `") and line.endswith("`"):
                return line.removeprefix("- Status: `").removesuffix("`")
        raise RuntimeError("completed report does not contain a status")

    def _terminal(self, state: AgentState) -> AgentState:
        writer = self._writer(state)
        writer.write_json(
            "usage.json",
            {
                "model": self.model.model_id,
                "reasoning_effort": getattr(self.model, "reasoning_effort", None),
                "prompt_version": PROMPT_VERSION,
                "agent_mode": self.agent_mode,
                "model_calls": state["model_calls"],
                "tool_calls": state["tool_calls"],
                "repair_iterations": state["iterations"],
                **state["usage"],
            },
        )
        report = Path(state["run_dir"]) / "final-report.md"
        if not report.exists():
            writer.write_text(
                "final-report.md",
                "\n".join(
                    [
                        f"# RepoMedic run {state['run_id']}",
                        "",
                        f"- Case: `{state['case_id']}`",
                        f"- Status: `{state['status']}`",
                        f"- Error: {state.get('error', '')}",
                        "",
                    ]
                ),
            )
        writer.append_trace("run_completed", {"status": state["status"]})
        return {}

    def start(self, prepared: PreparedRun) -> AgentRunResult:
        corpus = self.memory_store.snapshot() if self.memory_store is not None else None
        matches = (
            self.memory_store.search(
                prepared.manifest.issue,
                fixture_id=prepared.manifest.fixture.fixture_id,
                exclude_case_id=prepared.manifest.case_id,
                limit=self.memory_limit,
            )
            if self.memory_store is not None
            else ()
        )
        lessons = list(
            pack_memory_matches(
                matches,
                context_budget_chars=self.memory_context_budget_chars,
            )
        )
        retrieved_entry_ids = [item["entry_id"] for item in lessons]
        memory_data: dict[str, Any] = {
            "enabled": self.memory_store is not None,
            "database": str(self.memory_store.path) if self.memory_store else None,
            "limit": self.memory_limit,
            "context_budget_chars": self.memory_context_budget_chars,
            "context_chars": memory_prompt_chars(lessons),
            "corpus": asdict(corpus) if corpus is not None else None,
            "write_enabled": self.memory_write_enabled,
            "matched_entry_ids": [match.entry.entry_id for match in matches],
            "retrieved": lessons,
            "write": None,
        }
        state: AgentState = {
            "case_id": prepared.manifest.case_id,
            "run_id": prepared.layout.run_id,
            "case_dir": str(prepared.source_repo.parent),
            "run_dir": str(prepared.layout.run_dir),
            "workspace": str(prepared.layout.workspace),
            "source_repo": str(prepared.source_repo),
            "evaluator_dir": str(prepared.evaluator_dir),
            "issue": prepared.manifest.issue,
            "expected_behavior": list(prepared.manifest.expected_behavior),
            "allowed_paths": list(prepared.manifest.allowed_paths),
            "forbidden_paths": list(prepared.manifest.forbidden_paths),
            "wall_time_seconds": prepared.manifest.limits.wall_time_seconds,
            "tool_limit": prepared.manifest.limits.tool_calls,
            "repair_limit": prepared.manifest.limits.repair_iterations,
            "status": "running",
            "error": "",
            "iterations": 0,
            "tool_calls": 0,
            "model_calls": 0,
            "usage": {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "latency_ms": 0,
                "calls": 0,
            },
            "review_feedback": "",
            "memory_lessons": lessons,
            "memory": memory_data,
        }
        config = _thread_config(prepared.layout.run_id)
        config_path = prepared.layout.run_dir / "config.json"
        config_data = json.loads(config_path.read_text(encoding="utf-8"))
        config_data["agent_graph"] = {
            "model": self.model.model_id,
            "reasoning_effort": getattr(self.model, "reasoning_effort", None),
            "prompt_version": PROMPT_VERSION,
            "mode": self.agent_mode,
            "checkpoint": "checkpoint.sqlite",
        }
        config_data["memory"] = {
            "enabled": self.memory_store is not None,
            "database": str(self.memory_store.path) if self.memory_store else None,
            "limit": self.memory_limit,
            "context_budget_chars": self.memory_context_budget_chars,
            "context_chars": memory_prompt_chars(lessons),
            "corpus": asdict(corpus) if corpus is not None else None,
            "retrieved_entry_ids": retrieved_entry_ids,
            "write_enabled": self.memory_write_enabled,
        }
        writer = ArtifactWriter(prepared.layout.run_dir)
        writer.write_json("config.json", config_data)
        writer.write_json("memory.json", memory_data)
        writer.append_trace(
            "memory_retrieved",
            {
                "enabled": self.memory_store is not None,
                "context_budget_chars": self.memory_context_budget_chars,
                "context_chars": memory_prompt_chars(lessons),
                "entries": [
                    {"entry_id": item["entry_id"], "score": item["score"]}
                    for item in lessons
                ],
            },
        )
        self.graph.invoke(state, config)
        return self.inspect(prepared.layout.run_id)

    def resume(
        self, run_id: str, decision: ApprovalDecision
    ) -> AgentRunResult:
        config = _thread_config(run_id)
        self.graph.invoke(Command(resume=decision.model_dump(mode="json")), config)
        return self.inspect(run_id)

    def inspect(self, run_id: str) -> AgentRunResult:
        snapshot = self.graph.get_state(_thread_config(run_id))
        state = cast(AgentState, snapshot.values)
        if not state:
            raise ValueError(f"no checkpoint state exists for run {run_id!r}")
        awaiting_approval = any(task.interrupts for task in snapshot.tasks)
        return AgentRunResult(
            case_id=state["case_id"],
            run_id=state["run_id"],
            run_dir=state["run_dir"],
            status="awaiting_approval" if awaiting_approval else state["status"],
            awaiting_approval=awaiting_approval,
            proposal=state.get("proposal"),
            proposal_diff=state.get("proposal_diff"),
            error=state.get("error") or None,
            usage={
                "model_calls": state["model_calls"],
                "tool_calls": state["tool_calls"],
                "repair_iterations": state["iterations"],
                **state["usage"],
            },
            memory=state.get("memory"),
        )
