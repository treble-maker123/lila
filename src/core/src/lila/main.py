"""CLI entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from lila.executor import GraphError, load_graph
from lila.verification import check


def main() -> int:
    """Run the CLI; returns 0 when the graph loads and checks clean, 1 otherwise.

    Raises:
        SystemExit: argparse rejected the command line.
        OSError: the graph file cannot be read.
    """
    parser = argparse.ArgumentParser(prog="lila")
    commands = parser.add_subparsers(dest="command", required=True)
    check_command = commands.add_parser("check", help="statically check a graph file")
    check_command.add_argument("file", type=Path)
    args = parser.parse_args()

    try:
        graph = load_graph(args.file)
    except GraphError as exc:
        print(f"{args.file}: {exc}", file=sys.stderr)
        return 1

    issues = check(graph)
    for issue in issues:
        print(str(issue), file=sys.stderr)
    if issues:
        return 1
    print(f"{graph.skill}@{graph.version}: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
