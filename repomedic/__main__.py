from argparse import ArgumentParser
from pathlib import Path
import json

from langgraph.checkpoint.sqlite import SqliteSaver

from repomedic.agent_graph import AgentGraphRunner, AgentRunResult
from repomedic.agent_schemas import ApprovalDecision
from repomedic.harness import DeterministicHarness
from repomedic.model_clients import OpenAIResponsesModel, ScriptedModel
from repomedic.sandbox import DEFAULT_DOCKER_IMAGE, DockerSandbox
from repomedic.web_ui import serve_control_panel


def _parser() -> ArgumentParser:
    parser = ArgumentParser(description="Run a RepoMedic benchmark case")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_case = subparsers.add_parser("run-case", help="run one case in Docker")
    run_case.add_argument("case_dir", type=Path)
    run_case.add_argument("--runs-root", type=Path, default=Path("runs"))
    run_case.add_argument("--run-id")
    run_case.add_argument("--docker-image", default=DEFAULT_DOCKER_IMAGE)

    run_agent = subparsers.add_parser(
        "run-agent", help="start an Agent graph and pause for patch approval"
    )
    run_agent.add_argument("case_dir", type=Path)
    run_agent.add_argument("--runs-root", type=Path, default=Path("runs"))
    run_agent.add_argument("--run-id")
    run_agent.add_argument("--model", required=True)
    run_agent.add_argument("--docker-image", default=DEFAULT_DOCKER_IMAGE)

    decide = subparsers.add_parser(
        "decide-agent", help="approve, revise, or reject a paused Agent run"
    )
    decide.add_argument("run_dir", type=Path)
    decide.add_argument("action", choices=("approve", "revise", "reject"))
    decide.add_argument("--feedback", default="")
    decide.add_argument("--docker-image", default=DEFAULT_DOCKER_IMAGE)

    status = subparsers.add_parser(
        "agent-status", help="inspect a checkpointed Agent run"
    )
    status.add_argument("run_dir", type=Path)

    serve = subparsers.add_parser(
        "serve-agent", help="serve the local approval control panel"
    )
    serve.add_argument("run_dir", type=Path)
    serve.add_argument("--docker-image", default=DEFAULT_DOCKER_IMAGE)
    serve.add_argument("--port", type=int, default=8765)
    return parser


def _checkpoint_path(run_dir: Path) -> Path:
    resolved = run_dir.resolve()
    if not resolved.is_dir():
        raise ValueError(f"run directory does not exist: {resolved}")
    checkpoint = resolved / "checkpoint.sqlite"
    if not checkpoint.is_file():
        raise ValueError(f"checkpoint does not exist: {checkpoint}")
    return checkpoint


def _configured_model(run_dir: Path) -> str:
    config_path = run_dir.resolve() / "config.json"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        model = config["agent_graph"]["model"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise ValueError(
            f"run has no valid Agent model configuration: {config_path}"
        ) from error
    if not isinstance(model, str) or not model.strip():
        raise ValueError(f"run has no valid Agent model configuration: {config_path}")
    return model


def _print_agent_result(result: AgentRunResult) -> None:
    print(f"status={result.status}")
    print(f"run_dir={result.run_dir}")
    if result.awaiting_approval:
        print(f"approve=repomedic decide-agent \"{result.run_dir}\" approve")
        print(f"ui=repomedic serve-agent \"{result.run_dir}\"")
    if result.error:
        print(f"error={result.error}")


def _openai_runner(
    *, model: str, image: str, saver: SqliteSaver
) -> AgentGraphRunner:
    return AgentGraphRunner(
        OpenAIResponsesModel(model),
        DeterministicHarness(sandbox=DockerSandbox(image=image)),
        saver,
    )


def main() -> None:
    args = _parser().parse_args()
    if args.command == "run-case":
        outcome = DeterministicHarness(
            sandbox=DockerSandbox(image=args.docker_image)
        ).run_case(args.case_dir, args.runs_root, run_id=args.run_id)
        print(f"status={outcome.status}")
        print(f"run_dir={outcome.run_dir}")
        raise SystemExit(0 if outcome.status == "verified" else 1)
    if args.command == "run-agent":
        harness = DeterministicHarness(
            sandbox=DockerSandbox(image=args.docker_image)
        )
        prepared = harness.prepare_case(
            args.case_dir, args.runs_root, run_id=args.run_id
        )
        database = prepared.layout.run_dir / "checkpoint.sqlite"
        with SqliteSaver.from_conn_string(str(database)) as saver:
            result = AgentGraphRunner(
                OpenAIResponsesModel(args.model), harness, saver
            ).start(prepared)
        _print_agent_result(result)
        raise SystemExit(
            0 if result.awaiting_approval or result.status == "verified" else 1
        )
    if args.command == "decide-agent":
        database = _checkpoint_path(args.run_dir)
        with SqliteSaver.from_conn_string(str(database)) as saver:
            runner = _openai_runner(
                model=_configured_model(args.run_dir),
                image=args.docker_image,
                saver=saver,
            )
            result = runner.resume(
                args.run_dir.resolve().name,
                ApprovalDecision(action=args.action, feedback=args.feedback),
            )
        _print_agent_result(result)
        raise SystemExit(
            0 if result.awaiting_approval or result.status == "verified" else 1
        )
    if args.command == "agent-status":
        database = _checkpoint_path(args.run_dir)
        with SqliteSaver.from_conn_string(str(database)) as saver:
            result = AgentGraphRunner(
                ScriptedModel({}), DeterministicHarness(), saver
            ).inspect(args.run_dir.resolve().name)
        _print_agent_result(result)
        return
    if args.command == "serve-agent":
        if not 1 <= args.port <= 65535:
            raise ValueError("port must be between 1 and 65535")
        database = _checkpoint_path(args.run_dir)
        with SqliteSaver.from_conn_string(str(database)) as saver:
            runner = _openai_runner(
                model=_configured_model(args.run_dir),
                image=args.docker_image,
                saver=saver,
            )
            serve_control_panel(
                runner, run_id=args.run_dir.resolve().name, port=args.port
            )
        return


if __name__ == "__main__":
    main()
