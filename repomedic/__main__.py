"""CLI for fixing disposable repository copies and development evaluation."""

from argparse import ArgumentParser, BooleanOptionalAction, Namespace
from pathlib import Path
from typing import Any
import json
import shlex

from langgraph.checkpoint.sqlite import SqliteSaver
from pydantic import ValidationError

from repomedic.artifacts import sanitize
from repomedic.changes import SafetyError, run_path
from repomedic.graph import (
    AGENT_MODES, AgentRunner, ApprovalDecision, ModelSettings, RunLimits, openai_model, prepare_run,
)
from repomedic.grader import run_eval
from repomedic.prompt import PROMPT_VERSION
from repomedic.sandbox import DEFAULT_DOCKER_IMAGE, DockerSandbox, SandboxError
from repomedic.task import CommandSpec, Task, load_task, load_taskset


def _execution_options(parser: ArgumentParser) -> None:
    parser.add_argument("--model", required=True, help="Explicit model identifier")
    parser.add_argument("--provider", choices=["openai", "qwen"], default="openai")
    parser.add_argument("--base-url", help="HTTPS API endpoint; defaults to OpenAI or DashScope international")
    parser.add_argument("--thinking", dest="enable_thinking", action=BooleanOptionalAction,
                        default=None, help="Qwen agent thinking mode (default on); scope always disables thinking")
    parser.add_argument("--reasoning-effort", choices=["none", "low", "medium", "high", "xhigh", "max"])
    parser.add_argument("--image", default=DEFAULT_DOCKER_IMAGE)
    parser.add_argument("--agents", choices=AGENT_MODES, default="single",
                        help="single: one agent; explorer: main agent may delegate read-only exploration")
    defaults = RunLimits()
    for name in RunLimits.model_fields:
        parser.add_argument("--" + name.replace("_", "-"), type=int, default=getattr(defaults, name))


def _parser() -> ArgumentParser:
    parser = ArgumentParser(prog="repomedic", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    fix = sub.add_parser("fix", help="Repair a copy, check it, then pause for human review")
    fix.add_argument("repository", type=Path, help="Python repo or development task directory")
    issue = fix.add_mutually_exclusive_group()
    issue.add_argument("--issue")
    issue.add_argument("--issue-file", type=Path)
    fix.add_argument("--test-command", default="python -m unittest discover -s tests -v")
    fix.add_argument("--runs-root", type=Path, default=Path("runs/fixes"))
    _execution_options(fix)
    decide = sub.add_parser("decide", help="Continue a paused human review")
    decide.add_argument("run_dir", type=Path)
    decide.add_argument("action", choices=["approve", "revise", "reject"])
    decide.add_argument("--feedback", default="")
    status = sub.add_parser("status", help="Inspect saved state without a model call")
    status.add_argument("run_dir", type=Path)
    evaluate = sub.add_parser("eval", help="Grade every development task; skip review and never export")
    evaluate.add_argument("taskset", type=Path)
    evaluate.add_argument("--runs-root", type=Path, default=Path("runs/evals"))
    _execution_options(evaluate)
    return parser


def _limits(args: Namespace) -> RunLimits:
    return RunLimits.model_validate({key: getattr(args, key) for key in RunLimits.model_fields})


def _model_settings(args: Namespace) -> ModelSettings:
    return ModelSettings(provider=args.provider, base_url=args.base_url, enable_thinking=args.enable_thinking)


def _config(run_dir: Path) -> dict[str, Any]:
    path = run_path(run_dir, "config.json")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("run has no readable configuration") from error
    if not isinstance(value, dict) or value.get("protocol_version") != "tool-loop-v3":
        raise ValueError("run uses an incompatible protocol")
    if value.get("prompt_version") != PROMPT_VERSION:
        raise ValueError("run uses an incompatible prompt version")
    if value.get("run_id") != run_dir.name or value.get("mode") not in {"fix", "eval"}:
        raise ValueError("run identity or mode is invalid")
    RunLimits.model_validate(value["limits"])
    ModelSettings.model_validate(value.get("model_settings", {}))
    if value.get("agents", "single") not in AGENT_MODES:
        raise ValueError("run uses an unknown agents mode")
    if not run_path(run_dir, "checkpoint.sqlite").is_file():
        raise ValueError("run has no checkpoint")
    return value


def _print(value: Any) -> None:
    sanitized = sanitize(value)
    if isinstance(value, dict) and isinstance(value.get("review"), dict):
        # Local review must show the same bytes whose hash binds patch export.
        sanitized["review"]["diff"] = value["review"]["diff"]
    print(json.dumps(sanitized, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "fix":
            limits = _limits(args)
            settings = _model_settings(args)
            if (args.repository / "manifest.yaml").is_file():
                task = load_task(args.repository)
                if args.issue or args.issue_file:
                    task = task.model_copy(update={"issue": args.issue or args.issue_file.read_text(encoding="utf-8")})
            else:
                issue = args.issue or (args.issue_file.read_text(encoding="utf-8") if args.issue_file else None)
                if not issue:
                    raise ValueError("fix requires --issue or --issue-file for a repository")
                task = Task(case_id="repair", repo=args.repository.resolve(), issue=issue,
                            public_test=CommandSpec(argv=tuple(shlex.split(args.test_command))))
            sandbox = DockerSandbox(args.image)
            model = openai_model(args.model, limits, args.reasoning_effort, settings=settings)
            run_dir = prepare_run(task, args.runs_root, limits=limits, model_id=args.model,
                                  reasoning_effort=args.reasoning_effort, image=args.image, model_settings=settings,
                                  agents=args.agents)
            print(f"run_dir={run_dir}")
            with SqliteSaver.from_conn_string(str(run_dir / "checkpoint.sqlite")) as saver:
                result = AgentRunner(model, sandbox, saver, agents=args.agents).start(run_dir)
            _print(result.model_dump())
            return 0 if result.status in {"awaiting_review", "exported"} else 1
        if args.command in {"status", "decide"}:
            run_dir = args.run_dir.resolve()
            config = _config(run_dir)
            sandbox = DockerSandbox(config["sandbox"]["image"])
            with SqliteSaver.from_conn_string(str(run_dir / "checkpoint.sqlite")) as saver:
                # Resume with the frozen configuration, never with current CLI flags.
                agents = config.get("agents", "single")
                runner = AgentRunner(None, sandbox, saver, agents=agents)
                if args.command == "status":
                    result = runner.inspect(config["run_id"])
                else:
                    decision = ApprovalDecision(action=args.action, feedback=args.feedback)
                    # Approve/reject need no model. A revision (including detected drift)
                    # continues with exactly the frozen model and budget configuration.
                    result = runner.inspect(config["run_id"])
                    if result.status != "awaiting_review":
                        raise ValueError("only a human-review interrupt can resume; start a new run after other interruptions")
                    if runner.decision_needs_model(config["run_id"], args.action):
                        model = openai_model(config["model"], RunLimits.model_validate(config["limits"]),
                                             config.get("reasoning_effort"),
                                             settings=ModelSettings.model_validate(config.get("model_settings", {})))
                        runner = AgentRunner(model, sandbox, saver, agents=agents)
                    result = runner.resume(config["run_id"], decision)
            _print(result.model_dump())
            return 0 if result.status in {"awaiting_review", "exported", "rejected", "ready_for_grade"} else 1
        if args.command == "eval":
            limits = _limits(args)
            settings = _model_settings(args)
            summary = run_eval(load_taskset(args.taskset),
                               model=openai_model(args.model, limits, args.reasoning_effort, settings=settings),
                               sandbox=DockerSandbox(args.image), runs_root=args.runs_root,
                               limits=limits, reasoning_effort=args.reasoning_effort, model_settings=settings,
                               agents=args.agents)
            _print(summary)
            return 0 if summary["complete"] and summary["success_count"] == summary["task_count"] else 1
    except (ValueError, SafetyError, SandboxError, ValidationError) as error:
        parser.exit(2, "error: " + str(sanitize(str(error))) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
