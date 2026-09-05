from argparse import ArgumentParser
from pathlib import Path
import json

from langgraph.checkpoint.sqlite import SqliteSaver

from repomedic.agent_graph import AgentGraphRunner, AgentRunResult
from repomedic.agent_schemas import ApprovalDecision
from repomedic.benchmark import load_suite
from repomedic.benchmark_run import start_benchmark, summarize_benchmark
from repomedic.harness import DeterministicHarness
from repomedic.memory import EpisodicMemoryStore
from repomedic.model_clients import OpenAIResponsesModel, ScriptedModel
from repomedic.prompts import PROMPT_VERSION
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
    run_agent.add_argument(
        "--reasoning-effort",
        choices=("none", "low", "medium", "high", "xhigh", "max"),
    )
    run_agent.add_argument("--docker-image", default=DEFAULT_DOCKER_IMAGE)
    run_agent.add_argument("--memory-db", type=Path)
    run_agent.add_argument("--memory-limit", type=int, default=3)

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

    start_benchmark_parser = subparsers.add_parser(
        "start-benchmark", help="start every case in a suite and pause for approval"
    )
    start_benchmark_parser.add_argument("suite", type=Path)
    start_benchmark_parser.add_argument("--model", required=True)
    start_benchmark_parser.add_argument(
        "--reasoning-effort",
        choices=("none", "low", "medium", "high", "xhigh", "max"),
        default="low",
    )
    start_benchmark_parser.add_argument(
        "--runs-root", type=Path, default=Path("runs") / "benchmarks"
    )
    start_benchmark_parser.add_argument(
        "--case",
        dest="case_ids",
        action="append",
        help="run only this case ID; repeat to select multiple cases",
    )
    start_benchmark_parser.add_argument("--run-id")
    start_benchmark_parser.add_argument(
        "--docker-image", default=DEFAULT_DOCKER_IMAGE
    )
    start_benchmark_parser.add_argument("--memory-db", type=Path)
    start_benchmark_parser.add_argument("--memory-limit", type=int, default=3)

    benchmark_status = subparsers.add_parser(
        "benchmark-status", help="aggregate a checkpointed benchmark run"
    )
    benchmark_status.add_argument("run_dir", type=Path)

    memory_learn = subparsers.add_parser(
        "memory-learn", help="write one verified approved run to episodic memory"
    )
    memory_learn.add_argument("run_dir", type=Path)
    memory_learn.add_argument("--memory-db", type=Path, required=True)

    memory_search = subparsers.add_parser(
        "memory-search", help="search evidence-gated episodic memory"
    )
    memory_search.add_argument("query")
    memory_search.add_argument("--memory-db", type=Path, required=True)
    memory_search.add_argument("--fixture")
    memory_search.add_argument("--exclude-case")
    memory_search.add_argument("--limit", type=int, default=3)
    return parser


def _checkpoint_path(run_dir: Path) -> Path:
    resolved = run_dir.resolve()
    if not resolved.is_dir():
        raise ValueError(f"run directory does not exist: {resolved}")
    checkpoint = resolved / "checkpoint.sqlite"
    if not checkpoint.is_file():
        raise ValueError(f"checkpoint does not exist: {checkpoint}")
    return checkpoint


def _configured_agent(
    run_dir: Path,
) -> tuple[str, str | None, Path | None, int]:
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
    effort = config["agent_graph"].get("reasoning_effort")
    if effort is not None and effort not in {
        "none",
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    }:
        raise ValueError(f"run has an invalid reasoning effort: {effort!r}")
    configured_prompt = config["agent_graph"].get("prompt_version")
    if configured_prompt != PROMPT_VERSION:
        raise ValueError(
            f"run prompt version {configured_prompt!r} cannot resume under "
            f"{PROMPT_VERSION!r}"
        )
    memory = config.get("memory", {"enabled": False, "limit": 3})
    if not isinstance(memory, dict):
        raise ValueError(f"run has an invalid memory configuration: {config_path}")
    memory_limit = memory.get("limit", 3)
    if (
        not isinstance(memory_limit, int)
        or isinstance(memory_limit, bool)
        or not 1 <= memory_limit <= 10
    ):
        raise ValueError(f"run has an invalid memory limit: {memory_limit!r}")
    memory_path: Path | None = None
    if memory.get("enabled") is True:
        database = memory.get("database")
        if not isinstance(database, str) or not database.strip():
            raise ValueError(f"run has no valid memory database: {config_path}")
        memory_path = Path(database)
    return model, effort, memory_path, memory_limit


def _print_agent_result(result: AgentRunResult) -> None:
    print(f"status={result.status}")
    print(f"run_dir={result.run_dir}")
    if result.awaiting_approval:
        print(f"approve=repomedic decide-agent \"{result.run_dir}\" approve")
        print(f"ui=repomedic serve-agent \"{result.run_dir}\"")
    if result.error:
        print(f"error={result.error}")
    if result.memory and result.memory.get("write"):
        print(f"memory_entry={result.memory['write']['entry_id']}")


def _openai_runner(
    *,
    model: str,
    reasoning_effort: str | None,
    image: str,
    saver: SqliteSaver,
    memory_path: Path | None,
    memory_limit: int,
) -> AgentGraphRunner:
    return AgentGraphRunner(
        OpenAIResponsesModel(model, reasoning_effort=reasoning_effort),
        DeterministicHarness(sandbox=DockerSandbox(image=image)),
        saver,
        memory_store=EpisodicMemoryStore(memory_path) if memory_path else None,
        memory_limit=memory_limit,
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
                OpenAIResponsesModel(
                    args.model, reasoning_effort=args.reasoning_effort
                ),
                harness,
                saver,
                memory_store=(
                    EpisodicMemoryStore(args.memory_db)
                    if args.memory_db is not None
                    else None
                ),
                memory_limit=args.memory_limit,
            ).start(prepared)
        _print_agent_result(result)
        raise SystemExit(
            0 if result.awaiting_approval or result.status == "verified" else 1
        )
    if args.command == "decide-agent":
        database = _checkpoint_path(args.run_dir)
        model, effort, memory_path, memory_limit = _configured_agent(args.run_dir)
        with SqliteSaver.from_conn_string(str(database)) as saver:
            runner = _openai_runner(
                model=model,
                reasoning_effort=effort,
                image=args.docker_image,
                saver=saver,
                memory_path=memory_path,
                memory_limit=memory_limit,
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
        model, effort, memory_path, memory_limit = _configured_agent(args.run_dir)
        with SqliteSaver.from_conn_string(str(database)) as saver:
            runner = _openai_runner(
                model=model,
                reasoning_effort=effort,
                image=args.docker_image,
                saver=saver,
                memory_path=memory_path,
                memory_limit=memory_limit,
            )
            serve_control_panel(
                runner, run_id=args.run_dir.resolve().name, port=args.port
            )
        return
    if args.command == "start-benchmark":
        model = OpenAIResponsesModel(
            args.model, reasoning_effort=args.reasoning_effort
        )
        started = start_benchmark(
            load_suite(
                args.suite,
                case_ids=tuple(args.case_ids) if args.case_ids else None,
            ),
            model=model,
            harness=DeterministicHarness(
                sandbox=DockerSandbox(image=args.docker_image)
            ),
            runs_root=args.runs_root,
            run_id=args.run_id,
            memory_store=(
                EpisodicMemoryStore(args.memory_db)
                if args.memory_db is not None
                else None
            ),
            memory_limit=args.memory_limit,
        )
        print(f"run_dir={started.run_dir}")
        for result in started.case_results:
            print(f"{result.case_id}={result.status} {result.run_dir}")
        raise SystemExit(
            0 if all(item.awaiting_approval for item in started.case_results) else 1
        )
    if args.command == "benchmark-status":
        summary = summarize_benchmark(args.run_dir)
        print(f"complete={str(summary['complete']).lower()}")
        print(f"verified={summary['verified']}/{summary['case_count']}")
        print(f"summary={args.run_dir.resolve() / 'summary.md'}")
        return
    if args.command == "memory-learn":
        entry = EpisodicMemoryStore(args.memory_db).record_verified_run(args.run_dir)
        print(f"entry_id={entry.entry_id}")
        print(f"case_id={entry.case_id}")
        print(f"run_id={entry.run_id}")
        return
    if args.command == "memory-search":
        matches = EpisodicMemoryStore(args.memory_db).search(
            args.query,
            fixture_id=args.fixture,
            exclude_case_id=args.exclude_case,
            limit=args.limit,
        )
        print(
            json.dumps(
                [match.prompt_value() for match in matches],
                indent=2,
                ensure_ascii=False,
            )
        )
        return


if __name__ == "__main__":
    main()
