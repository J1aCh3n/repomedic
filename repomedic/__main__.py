from argparse import ArgumentParser
from pathlib import Path

from repomedic.harness import DeterministicHarness
from repomedic.sandbox import DEFAULT_DOCKER_IMAGE, DockerSandbox


def _parser() -> ArgumentParser:
    parser = ArgumentParser(description="Run a RepoMedic benchmark case")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_case = subparsers.add_parser("run-case", help="run one case in Docker")
    run_case.add_argument("case_dir", type=Path)
    run_case.add_argument("--runs-root", type=Path, default=Path("runs"))
    run_case.add_argument("--run-id")
    run_case.add_argument("--docker-image", default=DEFAULT_DOCKER_IMAGE)
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.command == "run-case":
        outcome = DeterministicHarness(
            sandbox=DockerSandbox(image=args.docker_image)
        ).run_case(args.case_dir, args.runs_root, run_id=args.run_id)
        print(f"status={outcome.status}")
        print(f"run_dir={outcome.run_dir}")
        raise SystemExit(0 if outcome.status == "verified" else 1)


if __name__ == "__main__":
    main()
