"""Small CLI; JSON output is suitable for an external experiment harness."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .models import VERSION, ShuffleError
from .output import apply_plan
from .plan import build_plan, inspect_project


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="reposhuffle",
        description="Create controlled original/shuffled Python repository pairs.",
    )
    root.add_argument("--version", action="version", version=VERSION)
    commands = root.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser(
        "inspect", help="Read-only discovery, or full feasibility with --mode."
    )
    inspect.add_argument("source", type=Path)
    inspect.add_argument(
        "--mode",
        choices=("flat", "random"),
        help="Also render and statically validate this layout in memory.",
    )
    inspect.add_argument("--seed", type=int)
    inspect.add_argument("--max-depth", type=int, default=3)
    shuffle = commands.add_parser("shuffle", help="Plan, validate and publish paired snapshots.")
    shuffle.add_argument("source", type=Path)
    shuffle.add_argument("--mode", choices=("flat", "random"), default="random")
    shuffle.add_argument(
        "--seed", type=int, help="Integer seed; omitted seeds are generated and reported."
    )
    shuffle.add_argument(
        "--max-depth", type=int, default=3, help="Random layout depth, 1–5 (default: 3)."
    )
    shuffle.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output container (default: sibling <project>-shuffled). Parent must exist.",
    )
    shuffle.add_argument(
        "--dry-run", action="store_true", help="Show the full plan without writing files."
    )
    for command in (inspect, shuffle):
        command.add_argument(
            "--include",
            action="append",
            help="Implementation path or glob to select; repeatable. Auxiliary files stay in place.",
        )
        command.add_argument(
            "--exclude",
            action="append",
            help="Path or glob to keep in place; repeatable (files are still copied).",
        )
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "inspect" and args.mode is None:
            result = inspect_project(args.source, include=args.include, exclude=args.exclude)
            payload = result.to_dict()
            payload["scope"] = "discovery-only; use --mode for full transformation feasibility"
        else:
            result = build_plan(
                args.source,
                mode=args.mode,
                seed=args.seed,
                max_depth=args.max_depth,
                include=args.include,
                exclude=args.exclude,
            )
            payload = result.to_dict()
            if args.command == "shuffle" and not args.dry_run and result.ok:
                payload["output"] = str(apply_plan(result, args.output))
        print(json.dumps(payload, ensure_ascii=True, indent=2))
        return 0 if result.ok else 2
    except (ShuffleError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
