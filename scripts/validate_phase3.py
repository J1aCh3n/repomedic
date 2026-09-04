"""Run the deterministic harness gate with the maintainer-known repair."""

from argparse import ArgumentParser
from pathlib import Path

from repomedic.harness import DeterministicHarness


FAULT = "subtotal > BULK_DISCOUNT_THRESHOLD"
REPAIR = "subtotal >= BULK_DISCOUNT_THRESHOLD"


def main() -> None:
    parser = ArgumentParser(description=__doc__)
    parser.add_argument(
        "--case-dir",
        type=Path,
        default=Path("benchmarks/cases/order_service_001"),
    )
    parser.add_argument("--runs-root", type=Path, default=Path("runs"))
    parser.add_argument("--run-id")
    args = parser.parse_args()

    harness = DeterministicHarness()
    prepared = harness.prepare_case(
        args.case_dir,
        args.runs_root,
        run_id=args.run_id,
    )
    pricing_file = prepared.layout.workspace / "order_service" / "pricing.py"
    source = pricing_file.read_text(encoding="utf-8")
    if source.count(FAULT) != 1:
        raise RuntimeError("fixture no longer contains the expected single fault")
    pricing_file.write_text(source.replace(FAULT, REPAIR), encoding="utf-8")

    outcome = harness.evaluate(prepared)
    print(f"status={outcome.status}")
    print(f"run_dir={outcome.run_dir}")
    if outcome.status != "verified":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
