"""Read-only explorer subagent: its own context, tools and budget; returns a checked report."""

from pathlib import Path
from typing import Annotated, Any, Literal, TypedDict
import json

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool, InjectedToolCallId, tool
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import InjectedState, ToolNode
from langgraph.prebuilt.tool_node import ToolInvocationError
from langgraph.types import Command
from pydantic import BeforeValidator, Field, ValidationError, model_validator

from repomedic.artifacts import ArtifactWriter, redact_text
from repomedic.changes import SafetyError
from repomedic.model_call import invoke_model, zero_usage
from repomedic.prompt import EXPLORER_PROMPT
from repomedic.sandbox import DockerSandbox
from repomedic.task import Contract
from repomedic.tools import (
    ToolDenied, _command_observation, read_file, read_lines, truncate_output, workspace_snapshot,
)


# ---------------------------------------------------------------------------
# Handoff contract: the only thing that crosses from the explorer to the main agent.
# ---------------------------------------------------------------------------

class Finding(Contract):
    path: str = Field(min_length=1, max_length=500)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    claim: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def ordered_lines(self) -> "Finding":
        if self.end_line < self.start_line:
            raise ValueError("end_line must be at least start_line")
        return self


class ExplorerReport(Contract):
    answer: str = Field(min_length=1, max_length=3000)
    findings: list[Finding] = Field(default=[], max_length=15)
    root_cause_hypothesis: str = Field(default="", max_length=1500)
    confidence: Literal["low", "medium", "high"]
    open_questions: list[str] = Field(default=[], max_length=5)


def verify_findings(report: ExplorerReport, state: dict[str, Any]) -> list[dict[str, Any]]:
    """The harness, not the explorer, decides whether each cited location exists.

    A finding is verified only if the file is readable through the normal tool
    checks and its line range lies inside the file. This catches invented paths
    and line numbers; it does not prove the claim about the code is correct.
    """
    checked = []
    for finding in report.findings:
        item = {**finding.model_dump(), "verified": False, "reason": ""}
        try:
            count = len(read_lines(state, finding.path))
        except ToolDenied as error:
            item["reason"] = str(error)
        else:
            if finding.end_line > count:
                item["reason"] = f"file has only {count} lines"
            else:
                item["verified"] = True
        checked.append(item)
    return checked


def format_report(report: ExplorerReport | None, findings: list[dict[str, Any]],
                  status: str, error: str = "") -> str:
    """Plain text the main agent reads as the delegate_explore tool result."""
    if report is None:
        return (f"explorer_status: {status}\nNo report was produced."
                + (f"\nerror: {error}" if error else "")
                + "\nContinue with your own investigation or ask a narrower question.")
    lines = [f"explorer_status: {status}", f"confidence: {report.confidence}",
             "--- answer ---", report.answer]
    if report.root_cause_hypothesis:
        lines += ["--- root cause hypothesis ---", report.root_cause_hypothesis]
    if findings:
        lines.append("--- findings (locations checked by the harness) ---")
        for item in findings:
            mark = "verified" if item["verified"] else f"UNVERIFIED: {item['reason']}"
            lines.append(f"- {item['path']}:{item['start_line']}-{item['end_line']} "
                         f"[{mark}] {item['claim']}")
    if report.open_questions:
        lines.append("--- open questions ---")
        lines += [f"- {question}" for question in report.open_questions]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Explorer state: a separate TypedDict. The subgraph can only see what run() puts here,
# which is how the explorer's context stays isolated from the main agent's history.
# ---------------------------------------------------------------------------

class ExplorerState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    run_dir: str
    limits: dict[str, Any]          # command_timeout / file limits reused by shared tools
    scope: list[str]                # always empty: the explorer never edits
    delegation_id: str
    max_tool_calls: int
    max_tokens: int
    tool_calls: int
    model_calls: int
    usage: dict[str, int]
    consecutive_no_tool_calls: int
    final_notice: bool
    last_command: dict[str, Any]
    report: dict[str, Any] | None
    status: str
    error: str


def _json_list(value: Any) -> Any:
    """Some models send nested array arguments as a JSON string; decode, then validate strictly.

    The advertised schema is unchanged (still an array). Only the decoding is lenient;
    anything that does not parse, or parses to the wrong shape, is still rejected.
    """
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value            # let normal validation report the real type error
    return value


LenientFindings = Annotated[list[Finding] | None, BeforeValidator(_json_list)]
LenientStrings = Annotated[list[str] | None, BeforeValidator(_json_list)]


FINAL_NOTICE = ("Explorer budget is nearly exhausted. Call report now with what you have "
                "verified so far; list anything unresolved under open_questions.")
# Every call resends the whole history, so tokens usually run out before tool calls do.
# Ask for the report while a quarter of the token budget is left for that final call.
FINAL_NOTICE_TOKEN_FRACTION = 0.75


def make_explorer_tools(sandbox: DockerSandbox) -> list[BaseTool]:
    @tool
    def bash(command: Annotated[str, Field(min_length=1, max_length=20000)],
             state: Annotated[dict[str, Any], InjectedState],
             tool_call_id: Annotated[str, InjectedToolCallId]) -> Command:
        """Run a shell command in Docker with /workspace mounted read-only; /scratch is writable."""
        if not command.strip() or "\x00" in command:
            raise ToolDenied("command must be non-empty and contain no null bytes")
        # Read-only is enforced by the Docker mount, not by trusting the prompt.
        result = sandbox.exec_command(Path(state["run_dir"]), command,
                                      state["limits"]["command_timeout"], readonly=True)
        return _command_observation(state, tool_call_id, "bash", result.model_dump(mode="json"))

    @tool
    def report(answer: Annotated[str, Field(min_length=1, max_length=3000)],
               confidence: Literal["low", "medium", "high"],
               tool_call_id: Annotated[str, InjectedToolCallId],
               findings: LenientFindings = None,
               root_cause_hypothesis: str = "",
               open_questions: LenientStrings = None) -> Command:
        """Finish exploring. Cite findings as exact file paths and line ranges you actually read."""
        validated = ExplorerReport(answer=answer, confidence=confidence,
                                   findings=findings or [],
                                   root_cause_hypothesis=root_cause_hypothesis,
                                   open_questions=open_questions or [])
        return Command(update={"report": validated.model_dump(), "status": "done",
                               "messages": [ToolMessage(content="Report received.",
                                                        tool_call_id=tool_call_id)]})

    return [read_file, bash, report]


class Explorer:
    """A complete agent loop compiled as its own graph and invoked from a main-agent tool."""

    def __init__(self, model: Any, sandbox: DockerSandbox) -> None:
        self.tools = make_explorer_tools(sandbox)
        self.model = model.bind_tools(self.tools, parallel_tool_calls=False) if model else None
        self.tool_node = ToolNode(self.tools, handle_tool_errors=(
            ToolDenied, ToolInvocationError, ValidationError))
        graph = StateGraph(ExplorerState)
        graph.add_node("explore", self._explore)
        graph.add_node("tools", self._tools)
        graph.add_edge(START, "explore")
        graph.add_conditional_edges("explore", self._after_explore, ["tools", "explore", END])
        graph.add_conditional_edges(
            "tools", lambda s: "explore" if s["status"] == "running" else END, ["explore", END])
        # No checkpointer: the explorer cannot write the workspace, so rerunning a whole
        # delegation after a crash has no side effects to duplicate.
        self.graph = graph.compile()

    @staticmethod
    def _writer(state: ExplorerState) -> ArtifactWriter:
        return ArtifactWriter(Path(state["run_dir"]))

    def _explore(self, state: ExplorerState) -> dict[str, Any]:
        over_budget = (state["tool_calls"] >= state["max_tool_calls"]
                       or state["usage"]["total_tokens"]
                       >= FINAL_NOTICE_TOKEN_FRACTION * state["max_tokens"])
        if over_budget and state.get("final_notice"):
            return {"status": "budget_exhausted", "error": "explorer tool budget exhausted"}
        notice = [HumanMessage(content=FINAL_NOTICE)] if over_budget else []
        response, updates = invoke_model(
            self._writer(state), self.model, EXPLORER_PROMPT, [*state["messages"], *notice],
            usage=state["usage"], model_calls=state["model_calls"],
            max_tokens=state["max_tokens"], agent="explorer",
            trace={"delegation_id": state["delegation_id"]})
        if response is None:
            return updates
        if over_budget:
            # One last chance to hand over partial knowledge instead of discarding it.
            calls = response.tool_calls
            if len(calls) != 1 or calls[0]["name"] != "report":
                return {**updates, "status": "budget_exhausted",
                        "error": "explorer tool budget exhausted without a report"}
            return {**updates, "messages": [*notice, response], "final_notice": True}
        if not response.tool_calls:
            count = state.get("consecutive_no_tool_calls", 0) + 1
            if count >= 3:
                return {**updates, "messages": [response], "status": "stalled",
                        "error": "explorer returned 3 consecutive replies without tool calls"}
            return {**updates, "consecutive_no_tool_calls": count, "messages": [
                response, HumanMessage(content=f"No tool call ({count}/3). Investigate with a "
                                               "tool, or call report when you can answer.")]}
        return {**updates, "messages": [response], "consecutive_no_tool_calls": 0}

    @staticmethod
    def _after_explore(state: ExplorerState) -> str:
        if state["status"] != "running":
            return END
        return "tools" if isinstance(state["messages"][-1], AIMessage) else "explore"

    def _tools(self, state: ExplorerState) -> dict[str, Any]:
        output = self.tool_node.invoke(state)
        if isinstance(output, list):
            item = output[0]
            updates = dict(item.update) if isinstance(item, Command) else dict(item)
        else:
            updates = dict(output)
        message = updates["messages"][0]
        index = state["tool_calls"] + 1
        self._writer(state).append_trace("tool_completed", {
            "agent": "explorer", "delegation_id": state["delegation_id"], "index": index,
            "call": state["messages"][-1].tool_calls[0], "denied": message.status == "error",
            "observation": truncate_output(redact_text(str(message.content)), 2000),
        })
        # The explorer's context is bounded the same way as the main agent's.
        updates["messages"] = [message.model_copy(update={
            "content": truncate_output(str(message.content))})]
        return {"tool_calls": index, **updates}

    def run(self, *, question: str, issue: str, files: list[str], run_dir: str,
            limits: dict[str, Any], max_tool_calls: int, max_tokens: int,
            delegation_id: str) -> ExplorerState:
        """Run one delegation to completion. Only issue, question and file list go in."""
        state: ExplorerState = {
            "messages": [HumanMessage(content=json.dumps(
                {"issue": issue, "question": question, "files": files}))],
            "run_dir": run_dir, "limits": limits, "scope": [], "delegation_id": delegation_id,
            "max_tool_calls": max_tool_calls, "max_tokens": max_tokens,
            "tool_calls": 0, "model_calls": 0, "usage": zero_usage(),
            "consecutive_no_tool_calls": 0, "final_notice": False, "report": None,
            "status": "running", "error": "",
        }
        # Each tool call costs two supersteps, plus up to two nudges per call and the final notice.
        config = {"recursion_limit": 4 * max_tool_calls + 10}
        try:
            return self.graph.invoke(state, config)
        except GraphRecursionError:
            return {**state, "status": "budget_exhausted",
                    "error": "explorer step limit exhausted"}


# ---------------------------------------------------------------------------
# The single connection point between the two agents: a main-agent tool.
# ---------------------------------------------------------------------------

def merge_usage(parent: dict[str, int], child: dict[str, int]) -> dict[str, int]:
    return {key: parent.get(key, 0) + child.get(key, 0) for key in parent.keys() | child.keys()}


def make_delegate_tool(explorer: Explorer) -> BaseTool:
    @tool
    def delegate_explore(question: Annotated[str, Field(min_length=1, max_length=2000)],
                         state: Annotated[dict[str, Any], InjectedState],
                         tool_call_id: Annotated[str, InjectedToolCallId]) -> Command:
        """Ask a read-only explorer agent one focused question about the repository.
        It works in its own fresh context and returns a report with checked file citations."""
        limits, delegations = state["limits"], state.get("delegations", [])
        if len(delegations) >= limits["max_delegations"]:
            raise ToolDenied("delegation limit reached; continue with your own tools")
        writer = ArtifactWriter(Path(state["run_dir"]))
        delegation_id = f"d{len(delegations) + 1}"
        # Sub-budget: never more than what the whole run still has left.
        token_budget = min(limits["explorer_max_tokens"],
                           limits["max_tokens"] - state["usage"]["total_tokens"])
        before = workspace_snapshot(state)
        writer.append_trace("delegation_started", {
            "delegation_id": delegation_id, "question": question, "token_budget": token_budget,
            "tool_budget": limits["explorer_max_tool_calls"]})
        # Isolation: only issue, question and file list cross over, never state["messages"].
        sub = explorer.run(question=question, issue=state["issue"], files=list(before),
                           run_dir=state["run_dir"], limits=limits,
                           max_tool_calls=limits["explorer_max_tool_calls"],
                           max_tokens=token_budget, delegation_id=delegation_id)
        # Defence in depth: the mount is read-only, and the harness also checks independently.
        if workspace_snapshot(state) != before:
            raise SafetyError("explorer changed the workspace despite its read-only mount")
        report = ExplorerReport.model_validate(sub["report"]) if sub.get("report") else None
        findings = verify_findings(report, state) if report else []
        record = {"delegation_id": delegation_id, "question": question, "status": sub["status"],
                  "error": sub.get("error", ""), "tool_calls": sub["tool_calls"],
                  "model_calls": sub["model_calls"], "tokens": sub["usage"]["total_tokens"],
                  "verified_findings": sum(item["verified"] for item in findings),
                  "unverified_findings": sum(not item["verified"] for item in findings)}
        writer.append_trace("delegation_finished", {**record, "report": sub.get("report"),
                                                    "findings": findings})
        return Command(update={
            "messages": [ToolMessage(content=format_report(report, findings, sub["status"],
                                                           sub.get("error", "")),
                                     tool_call_id=tool_call_id, name="delegate_explore")],
            # Explorer spending counts against the run budget, so it cannot bypass it.
            "usage": merge_usage(state["usage"], sub["usage"]),
            "model_calls": state["model_calls"] + sub["model_calls"],
            "delegations": [*delegations, record],
        })

    return delegate_explore
