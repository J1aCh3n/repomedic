"""LangGraph owns the boundaries; the model chooses actions inside them."""

from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol, TypedDict
import json
import os
import uuid
from urllib.parse import urlsplit

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langgraph.prebuilt.tool_node import ToolInvocationError
from langgraph.types import Command, interrupt
from langsmith.run_helpers import tracing_context
from pydantic import Field, ValidationError, model_validator

from repomedic.artifacts import ArtifactWriter, redact_text, sanitize
from repomedic.changes import (
    PatchError, SafetyError, ScanLimits, build_diff, changed_paths, copy_repository,
    diff_hash, run_path, scan_tree, tree_hash,
)
from repomedic.explorer import Explorer, make_delegate_tool
from repomedic.model_call import ModelFailure, invoke_model, zero_usage
from repomedic.prompt import AGENT_PROMPT, DELEGATION_PROMPT, PROMPT_VERSION, SCOPE_PROMPT
from repomedic.sandbox import DockerSandbox, MAX_OUTPUT_BYTES, SandboxError
from repomedic.task import CommandSpec, Contract, Task
from repomedic.tools import ScopePlan, ToolDenied, make_tools, scan_limits, truncate_output, workspace_snapshot


class RunLimits(Contract):
    # Initial defaults, not validated performance/cost recommendations.
    recursion_limit: int = Field(default=200, ge=6, le=10000)
    max_tokens: int = Field(default=400000, ge=1)
    max_tool_calls: int = Field(default=80, ge=1)
    command_timeout: int = Field(default=60, ge=1, le=600)
    model_timeout: int = Field(default=60, ge=1, le=600)
    max_output_tokens: int = Field(default=4000, ge=1)
    max_entries: int = Field(default=10000, ge=1)
    max_file_bytes: int = Field(default=2000000, ge=1)
    # Explorer sub-budgets (explorer mode only). Its tokens also count against max_tokens.
    max_delegations: int = Field(default=5, ge=1, le=50)
    explorer_max_tool_calls: int = Field(default=15, ge=1, le=200)
    explorer_max_tokens: int = Field(default=60000, ge=1)


AgentMode = Literal["single", "explorer"]
AGENT_MODES: tuple[str, ...] = ("single", "explorer")


class ModelSettings(Contract):
    provider: Literal["openai", "qwen"] = "openai"
    base_url: str | None = None
    enable_thinking: bool | None = None

    @model_validator(mode="after")
    def resolve_options(self) -> "ModelSettings":
        self.base_url = (self.base_url or (
            "https://dashscope-intl.aliyuncs.com/compatible-mode/v1" if self.provider == "qwen"
            else "https://api.openai.com/v1")).rstrip("/")
        parsed = urlsplit(self.base_url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment
                or any(c.isspace() for c in self.base_url)):
            raise ValueError("base URL must be HTTPS without credentials, query or fragment")
        if self.provider == "openai" and self.enable_thinking is not None:
            raise ValueError("thinking mode is a Qwen option; use reasoning-effort for OpenAI")
        if self.provider == "qwen" and self.enable_thinking is None:
            self.enable_thinking = True
        return self


class ApprovalDecision(Contract):
    action: Literal["approve", "revise", "reject"]
    feedback: str = Field(default="", max_length=4000)


class AgentState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    run_dir: str
    run_id: str
    case_id: str
    issue: str
    expected_behavior: list[str]
    public_test: dict[str, Any]
    baseline_snapshot: dict[str, str]
    limits: dict[str, Any]
    mode: str
    status: str
    error: str
    scope: list[str]
    scope_history: list[dict[str, Any]]
    plan: str
    summary: str
    submitted: bool
    graph_steps: int
    model_calls: int
    tool_calls: int
    consecutive_no_tool_calls: int
    usage: dict[str, int]
    last_command: dict[str, Any]
    check_result: dict[str, Any]
    review_diff: str
    review_hash: str
    review_tree_hash: str
    approved_hash: str
    delegations: list[dict[str, Any]]


class RunResult(Contract):
    run_id: str
    run_dir: str
    case_id: str
    status: str
    error: str = ""
    review: dict[str, Any] | None = None
    usage: dict[str, int]


class ChatModel(Protocol):
    def bind_tools(self, tools: Any, **kwargs: Any) -> Any: ...


class ScriptedModel:
    """Deterministic tool-calling fixture; not a measure of model repair ability."""
    model_name = "scripted:test"

    def __init__(self, responses: list[AIMessage | Exception]) -> None:
        self.responses = deque(responses)
        self.calls: list[list[BaseMessage]] = []

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedModel":
        return self

    def invoke(self, messages: list[BaseMessage]) -> AIMessage:
        self.calls.append(messages)
        if not self.responses:
            raise ModelFailure("no scripted model response remains")
        result = self.responses.popleft()
        if isinstance(result, Exception):
            raise result
        return result


def tool_turn(name: str, args: dict[str, Any], *, tokens: int = 0) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args,
                                              "id": f"call_{uuid.uuid4().hex}",
                                              "type": "tool_call"}],
                     usage_metadata={"input_tokens": tokens, "output_tokens": 0,
                                     "total_tokens": tokens})


@dataclass(frozen=True)
class QwenChatModel:
    client: ChatOpenAI

    @property
    def model_name(self) -> str:
        return self.client.model_name

    def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
        choice = kwargs.get("tool_choice")
        if choice not in (None, "auto", "none"):
            kwargs["extra_body"] = {**(self.client.extra_body or {}), "enable_thinking": False}
        else:
            kwargs.setdefault("tool_choice", "auto")
        return self.client.bind_tools(tools, **kwargs)


QWEN_KEY_VARIABLES = ("DASHSCOPE_API_KEY", "QWEN_API_KEY")


def openai_model(model: str, limits: RunLimits, reasoning_effort: str | None = None, *,
                 settings: ModelSettings | None = None) -> ChatModel:
    settings = settings or ModelSettings()
    if settings.provider == "qwen":
        if reasoning_effort is not None:
            raise ValueError("reasoning-effort is an OpenAI option; use thinking mode for Qwen")
        # DashScope's documented name wins; QWEN_API_KEY is an accepted fallback.
        key = next((value for name in QWEN_KEY_VARIABLES
                    if (value := os.getenv(name)) and value.strip()), None)
        if key is None:
            raise ValueError("Qwen requires DASHSCOPE_API_KEY or QWEN_API_KEY in the process environment")
        return QwenChatModel(ChatOpenAI(
            model=model, api_key=key, base_url=settings.base_url, use_responses_api=False,
            store=False, max_retries=0, timeout=limits.model_timeout,
            # This LangChain version renames max_tokens to max_completion_tokens.
            # Send Qwen's documented parameter through the SDK extra body instead.
            extra_body={"enable_thinking": settings.enable_thinking, "max_tokens": limits.max_output_tokens}))
    kwargs: dict[str, Any] = {}
    if reasoning_effort is not None:
        kwargs["reasoning"] = {"effort": reasoning_effort}
    return ChatOpenAI(model=model, base_url=settings.base_url, use_responses_api=True, output_version="responses/v1",
                      store=False, include=["reasoning.encrypted_content"],
                      use_previous_response_id=False, max_retries=0,
                      timeout=limits.model_timeout, max_tokens=limits.max_output_tokens, **kwargs)


def prepare_run(task: Task, runs_root: Path, *, limits: RunLimits | None = None,
                model_id: str = "scripted:test", reasoning_effort: str | None = None,
                mode: Literal["fix", "eval"] = "fix", run_id: str | None = None,
                image: str | None = None, model_settings: ModelSettings | None = None,
                agents: AgentMode = "single") -> Path:
    limits = limits or RunLimits()
    model_settings = model_settings or ModelSettings()
    runs_root = runs_root.resolve()
    runs_root.mkdir(parents=True, exist_ok=True)
    task_root = run_path(runs_root, task.case_id)
    task_root.mkdir(exist_ok=True)
    identity = run_id or (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8])
    run_dir = run_path(task_root, identity)
    # Copying a repo containing its own output directory would recursively copy runs.
    if run_dir.is_relative_to(task.repo.resolve()):
        raise SafetyError("runs root must be outside the source repository")
    run_dir.mkdir(exist_ok=False)
    scans = ScanLimits(limits.max_entries, limits.max_file_bytes)
    copy_repository(task.repo, run_path(run_dir, "baseline"), scans, writable=False)
    copy_repository(run_path(run_dir, "baseline"), run_path(run_dir, "workspace"), scans)
    scratch = run_path(run_dir, "scratch")
    scratch.mkdir()
    scratch.chmod(0o777)
    snapshot = scan_tree(run_path(run_dir, "baseline"), scans)
    writer = ArtifactWriter(run_dir)
    writer.write_json("config.json", {
        "protocol_version": "tool-loop-v3", "prompt_version": PROMPT_VERSION,
        "case_id": task.case_id, "run_id": identity, "mode": mode, "agents": agents,
        "model": model_id, "reasoning_effort": reasoning_effort,
        "model_settings": model_settings.model_dump(),
        "limits": limits.model_dump(), "task": task.agent_context(),
        "fixture": task.fixture.model_dump() if task.fixture else None,
        "fixture_hash": tree_hash(snapshot), "baseline_snapshot": snapshot,
        "sandbox": {"image": image or DockerSandbox().image, "network": "none",
                    "root_filesystem": "read-only", "workspace": "writable",
                    "output_bytes": MAX_OUTPUT_BYTES},
    })
    writer.append_trace("workspace_prepared", {"fixture_hash": tree_hash(snapshot), "mode": mode})
    return run_dir


class AgentRunner:
    def __init__(self, model: ChatModel | None, sandbox: DockerSandbox, checkpointer: Any,
                 agents: AgentMode = "single") -> None:
        if agents not in AGENT_MODES:
            raise ValueError(f"unknown agents mode: {agents!r}")
        self.model, self.sandbox, self.agents = model, sandbox, agents
        self.tools = make_tools(sandbox)
        self.agent_prompt = AGENT_PROMPT
        if agents == "explorer":
            # The explorer is just another tool from the main graph's point of view.
            self.tools.append(make_delegate_tool(Explorer(model, sandbox)))
            self.agent_prompt = AGENT_PROMPT + DELEGATION_PROMPT
        self.tool_node = ToolNode(self.tools, handle_tool_errors=(
            ToolDenied, ToolInvocationError, ValidationError))
        self.scope_model = (model.bind_tools(
            [{"type": "function", "function": {"name": "declare_scope",
              "description": "Declare exact repair files and a brief plan.",
              "parameters": ScopePlan.model_json_schema()}}],
            tool_choice="declare_scope", parallel_tool_calls=False) if model else None)
        self.agent_model = model.bind_tools(self.tools, parallel_tool_calls=False) if model else None
        graph = StateGraph(AgentState)
        for name, function in (("scope", self._scope), ("agent", self._agent),
                               ("tools", self._tools), ("check", self._check),
                               ("review", self._review), ("export", self._export)):
            graph.add_node(name, self._guarded(name, function))
        graph.add_node("terminal", self._terminal)
        graph.add_edge(START, "scope")
        graph.add_conditional_edges("scope", lambda s: "agent" if s["status"] == "running" else "terminal")
        graph.add_conditional_edges("agent", self._after_agent)
        graph.add_conditional_edges("tools", self._after_tools)
        graph.add_conditional_edges("check", self._after_check)
        graph.add_conditional_edges("review", self._after_review)
        graph.add_conditional_edges("export", lambda s: "check" if s["status"] == "running" else "terminal")
        graph.add_edge("terminal", END)
        self.graph = graph.compile(checkpointer=checkpointer)

    @staticmethod
    def _writer(state: AgentState) -> ArtifactWriter:
        return ArtifactWriter(Path(state["run_dir"]))

    def _guarded(self, name: str, function: Any) -> Any:
        def node(state: AgentState) -> dict[str, Any]:
            if state["graph_steps"] >= state["limits"]["recursion_limit"]:
                return {"status": "budget_exhausted", "error": "total graph step budget exhausted"}
            working = {**state, "graph_steps": state["graph_steps"] + 1}
            self._writer(state).append_trace("node_entered", {"node": name, "step": working["graph_steps"]})
            try:
                workspace_snapshot(working)
                result = function(working)
            except SafetyError as error:
                result = {"status": "policy_violation", "error": str(error)}
            except SandboxError as error:
                # Sandbox configuration faults are not model-correctable; stop instead of
                # letting the agent retry until its budget is exhausted.
                result = {"status": "infrastructure_error", "error": str(error)}
            return {"graph_steps": working["graph_steps"], **result}
        return node

    def _call(self, state: AgentState, model: Any, instructions: str,
              messages: list[BaseMessage]) -> tuple[AIMessage | None, dict[str, Any]]:
        return invoke_model(self._writer(state), model, instructions, messages,
                            usage=state["usage"], model_calls=state["model_calls"],
                            max_tokens=state["limits"]["max_tokens"], agent="main")

    def _scope(self, state: AgentState) -> dict[str, Any]:
        initial = HumanMessage(content=json.dumps({"issue": state["issue"],
                                                  "files": list(state["baseline_snapshot"]),
                                                  "expected_behavior": state["expected_behavior"],
                                                  "public_test": state["public_test"]}))
        response, updates = self._call(state, self.scope_model, SCOPE_PROMPT, [initial])
        if response is None:
            return updates
        try:
            if len(response.tool_calls) != 1 or response.tool_calls[0]["name"] != "declare_scope":
                raise ModelFailure("scope planner must call declare_scope")
            plan = ScopePlan.model_validate(response.tool_calls[0]["args"])
            for path in plan.paths:
                if run_path(Path(state["run_dir"]), "workspace").joinpath(path).is_dir():
                    raise ToolDenied("scope must name exact files")
        except (ValidationError, ToolDenied, ModelFailure) as error:
            return {**updates, "status": "model_error", "error": str(error)}
        history = [{"paths": plan.paths, "reason": plan.plan}]
        self._writer(state).append_trace("scope_declared", history[0])
        return {**updates, "scope": plan.paths, "plan": plan.plan, "scope_history": history,
                "messages": [initial, response, ToolMessage(content="Scope declared.",
                                      tool_call_id=response.tool_calls[0]["id"])]}

    def _agent(self, state: AgentState) -> dict[str, Any]:
        if state["tool_calls"] >= state["limits"]["max_tool_calls"]:
            return {"status": "budget_exhausted", "error": "tool call budget exhausted"}
        response, updates = self._call(state, self.agent_model, self.agent_prompt, state["messages"])
        if response is None:
            return updates
        if response.tool_calls and state["tool_calls"] >= state["limits"]["max_tool_calls"]:
            return {**updates, "status": "budget_exhausted", "error": "tool call budget exhausted"}
        if not response.tool_calls:
            count = state.get("consecutive_no_tool_calls", 0) + 1
            if count >= 3:
                self._writer(state).append_trace("agent_stalled", {"consecutive_no_tool_calls": count})
                return {**updates, "consecutive_no_tool_calls": count, "messages": [response],
                        "status": "stalled", "error": "model returned 3 consecutive replies without tool calls"}
            return {**updates, "consecutive_no_tool_calls": count,
                    "messages": [response, HumanMessage(content=
                f"No tool call ({count}/3). Continue with a tool call, or call submit when the repair is ready.")]}
        call = response.tool_calls[0]
        if not call.get("id") or not isinstance(call.get("args"), dict):
            return {**updates, "status": "model_error", "error": "tool call is missing ID or arguments"}
        return {**updates, "messages": [response], "submitted": False, "consecutive_no_tool_calls": 0}

    def _tools(self, state: AgentState) -> dict[str, Any]:
        before = workspace_snapshot(state)
        output = self.tool_node.invoke(state)
        # One call per turn: ToolNode returns a dict or a single Command update.
        if isinstance(output, list):
            item = output[0]
            updates = dict(item.update) if isinstance(item, Command) else dict(item)
        else:
            updates = dict(output)
        message = updates["messages"][0]
        original = str(message.content).encode("utf-8")[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
        observation = (json.dumps(sanitize(updates["last_command"])) if "last_command" in updates
                       else redact_text(str(message.content)))
        bounded = observation.encode("utf-8")[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
        index = state["tool_calls"] + 1
        self._writer(state).write_text(f"observations/{index:04d}.txt", bounded)
        updates["messages"] = [message.model_copy(update={"content": truncate_output(original)})]
        try:
            after = workspace_snapshot(state)
        except SafetyError as error:
            self._writer(state).append_trace("tool_completed", {
                "index": index, "call": state["messages"][-1].tool_calls[0],
                "observation": truncate_output(bounded), "policy_violation": str(error),
            })
            return {"tool_calls": index, **updates, "status": "policy_violation", "error": str(error)}
        changes = changed_paths(state["baseline_snapshot"], after)
        call = state["messages"][-1].tool_calls[0]
        self._writer(state).append_trace("tool_completed", {
            "index": index, "call": call, "denied": message.status == "error",
            "observation": truncate_output(bounded),
            "changed_this_call": changed_paths(before, after), "changes_from_baseline": changes,
            "change_hashes": [{"path": path, "before": before.get(path), "after": after.get(path)}
                              for path in changed_paths(before, after)],
            "outside_scope": sorted(set(changes) - set(updates.get("scope", state["scope"]))),
        })
        return {"tool_calls": index, **updates}

    def _check(self, state: AgentState) -> dict[str, Any]:
        snapshot = workspace_snapshot(state)
        changes = changed_paths(state["baseline_snapshot"], snapshot)
        outside = sorted(set(changes) - set(state["scope"]))
        result = self.sandbox.run_tests(Path(state["run_dir"]),
                    CommandSpec.model_validate(state["public_test"]), state["limits"]["command_timeout"])
        check: dict[str, Any] = {"changes": changes, "outside_scope": outside,
                                  "public_tests": result.model_dump(mode="json")}
        if result.infrastructure_error:
            self._writer(state).append_trace("check_completed", check)
            self._writer(state).write_json("test-results.json", check)
            return {"status": "infrastructure_error", "error": result.stderr, "check_result": check}
        try:
            diff = build_diff(run_path(Path(state["run_dir"]), "baseline"),
                              run_path(Path(state["run_dir"]), "workspace"), scan_limits(state))
        except PatchError as error:
            check["patch_error"] = str(error)
            diff = ""
        self._writer(state).append_trace("check_completed", check)
        self._writer(state).write_json("test-results.json", check)
        if outside or not result.passed or check.get("patch_error"):
            return {"check_result": check, "submitted": False,
                    "messages": [HumanMessage(content="Submission checks failed:\n" +
                                    truncate_output(redact_text(json.dumps(check))))]}
        # Tests run read-only, but independently verify the checked tree after execution.
        current = workspace_snapshot(state)
        if current != snapshot:
            return {"submitted": False, "messages": [HumanMessage(content=
                "Workspace changed during checks; submit again for fresh tests.")]}
        return {"check_result": check, "review_diff": diff, "review_hash": diff_hash(diff),
                "review_tree_hash": tree_hash(current),
                "status": "ready_for_grade" if state["mode"] == "eval" else "running"}

    def _review(self, state: AgentState) -> dict[str, Any]:
        # Never perform editing, tests, or exporting before this interrupt.
        raw = interrupt(self._review_payload(state))
        try:
            decision = ApprovalDecision.model_validate(raw)
        except ValidationError as error:
            return {"status": "approval_error", "error": str(error)}
        self._writer(state).append_trace("approval_decided", decision.model_dump())
        if decision.action == "reject":
            return {"status": "rejected"}
        if decision.action == "revise":
            return {"submitted": False, "approved_hash": "", "messages": [HumanMessage(
                content="Human revision requested: " + (decision.feedback or "Revise the repair."))]}
        return {"approved_hash": state["review_hash"]}

    @staticmethod
    def _review_payload(state: AgentState) -> dict[str, Any]:
        return {**sanitize({"summary": state.get("summary", ""), "scope": state["scope"],
                "scope_history": state["scope_history"], "check": state["check_result"],
                "diff_hash": state["review_hash"], "decisions": ["approve", "revise", "reject"]}),
                "diff": state["review_diff"]}

    def decision_needs_model(self, run_id: str, action: str) -> bool:
        if action == "revise":
            return True
        if action != "approve":
            return False
        state = self.graph.get_state(self._config(run_id)).values
        try:
            return tree_hash(workspace_snapshot(state)) != state["review_tree_hash"]
        except SafetyError:
            return False

    def _export(self, state: AgentState) -> dict[str, Any]:
        if state["mode"] != "fix" or not state.get("approved_hash"):
            return {"status": "approval_error", "error": "export requires explicit human approval"}
        diff = build_diff(run_path(Path(state["run_dir"]), "baseline"),
                          run_path(Path(state["run_dir"]), "workspace"), scan_limits(state))
        if (diff_hash(diff) != state["approved_hash"]
                or tree_hash(workspace_snapshot(state)) != state["review_tree_hash"]):
            # The runner checks for drift before resuming; this guards the final boundary too.
            return {"approved_hash": "", "submitted": True, "messages": [HumanMessage(
                content="Workspace changed after approval; fresh checks and approval are required.")],
                "status": "running", "review_hash": diff_hash(diff), "review_diff": diff}
        self._writer(state).write_patch(diff)
        self._writer(state).append_trace("patch_exported", {"diff_hash": diff_hash(diff)})
        return {"status": "exported"}

    @staticmethod
    def _usage_summary(state: AgentState) -> dict[str, int]:
        delegations = state.get("delegations", [])
        # usage totals already include explorer spending; explorer_* shows its share.
        return {**state["usage"], "model_calls": state["model_calls"],
                "tool_calls": state["tool_calls"], "graph_steps": state["graph_steps"],
                "delegations": len(delegations),
                "explorer_tokens": sum(item["tokens"] for item in delegations),
                "explorer_tool_calls": sum(item["tool_calls"] for item in delegations)}

    def _terminal(self, state: AgentState) -> dict[str, Any]:
        usage = self._usage_summary(state)
        writer = self._writer(state)
        writer.write_json("usage.json", usage)
        writer.write_json("result.json", {"status": state["status"], "error": state.get("error", ""),
                                          "case_id": state["case_id"], "usage": usage})
        writer.write_text("final-report.md", f"# RepoMedic run {state['run_id']}\n\n"
                          f"- Case: `{state['case_id']}`\n- Status: `{state['status']}`\n"
                          f"- Error: {state.get('error', '')}\n")
        writer.append_trace("run_completed", {"status": state["status"]})
        return {}

    @staticmethod
    def _after_agent(state: AgentState) -> str:
        if state["status"] != "running":
            return "terminal"
        return "tools" if isinstance(state["messages"][-1], AIMessage) else "agent"

    @staticmethod
    def _after_tools(state: AgentState) -> str:
        if state["status"] != "running":
            return "terminal"
        return "check" if state.get("submitted") else "agent"

    @staticmethod
    def _after_check(state: AgentState) -> str:
        if state["status"] != "running":
            return "terminal"
        return "review" if state.get("submitted") else "agent"

    @staticmethod
    def _after_review(state: AgentState) -> str:
        if state["status"] != "running":
            return "terminal"
        return "export" if state.get("approved_hash") else "agent"

    @staticmethod
    def _config(run_id: str, limit: int = 10000) -> dict[str, Any]:
        return {"configurable": {"thread_id": run_id}, "recursion_limit": limit}

    def _invoke(self, value: Any, run_id: str, limit: int) -> None:
        try:
            with tracing_context(enabled=False):
                self.graph.invoke(value, self._config(run_id, limit))
        except GraphRecursionError:
            state = self.graph.get_state(self._config(run_id)).values
            updates = {"status": "budget_exhausted", "error": "LangGraph recursion limit exhausted"}
            self._terminal({**state, **updates})
            self.graph.update_state(self._config(run_id), updates, as_node="terminal")

    def start(self, run_dir: Path) -> RunResult:
        config = json.loads((run_dir / "config.json").read_text(encoding="utf-8"))
        run_id = config["run_id"]
        if config.get("agents", "single") != self.agents:
            raise ValueError(f"run was prepared for agents={config.get('agents', 'single')!r}, "
                             f"runner uses {self.agents!r}")
        if self.graph.get_state(self._config(run_id)).values:
            raise ValueError("run already has state; only approval-paused runs can resume")
        state: AgentState = {
            "messages": [], "run_dir": str(run_dir.resolve()), "run_id": run_id,
            "case_id": config["case_id"], "issue": config["task"]["issue"],
            "expected_behavior": config["task"].get("expected_behavior", []),
            "public_test": config["task"]["public_test"], "limits": config["limits"],
            "baseline_snapshot": config["baseline_snapshot"], "mode": config["mode"],
            "status": "running", "error": "", "scope": [], "scope_history": [],
            "submitted": False, "graph_steps": 0, "model_calls": 0, "tool_calls": 0,
            "consecutive_no_tool_calls": 0, "delegations": [], "usage": zero_usage(),
        }
        self._invoke(state, run_id, config["limits"]["recursion_limit"])
        return self.inspect(run_id)

    def resume(self, run_id: str, decision: ApprovalDecision) -> RunResult:
        snapshot = self.graph.get_state(self._config(run_id))
        state = snapshot.values
        if not any(task.interrupts for task in snapshot.tasks):
            raise ValueError("only a human-review interrupt can resume; restart interrupted tool execution")
        if decision.action == "approve":
            try:
                if tree_hash(workspace_snapshot(state)) != state["review_tree_hash"]:
                    decision = ApprovalDecision(action="revise", feedback=
                        "Workspace changed while awaiting approval. Submit for fresh checks and approval.")
            except SafetyError:
                # Resume to the guarded node, which records the policy violation.
                pass
        self._invoke(Command(resume=decision.model_dump()), run_id, state["limits"]["recursion_limit"])
        return self.inspect(run_id)

    def inspect(self, run_id: str) -> RunResult:
        snapshot = self.graph.get_state(self._config(run_id))
        state = snapshot.values
        if not state:
            raise ValueError("no checkpoint state exists for this run")
        awaiting = any(task.interrupts for task in snapshot.tasks)
        return RunResult(run_id=run_id, run_dir=state["run_dir"], case_id=state["case_id"],
                         status="awaiting_review" if awaiting else state["status"],
                         error=state.get("error", ""),
                         review=self._review_payload(state) if awaiting else None,
                         usage=self._usage_summary(state))
