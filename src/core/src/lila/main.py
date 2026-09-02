"""CLI entry point: argument parsing only. The commands themselves live in lila.commands."""

from __future__ import annotations

import argparse
from pathlib import Path

from lila.commands import call_command, check_command, run_command

HOME_HELP = "install directory; defaults to $LILA_HOME, else the nearest .lila/ above this one"


def _parser() -> argparse.ArgumentParser:
    """The whole command line, declared in one place."""
    parser = argparse.ArgumentParser(prog="lila")
    commands = parser.add_subparsers(dest="command", required=True)

    check = commands.add_parser("check", help="statically check a graph file")
    check.add_argument("file", type=Path)

    run = commands.add_parser("run", help="check and run a graph, by path or installed ref")
    run.add_argument("target", help="a path to a graph file, or an installed skill ref")
    run.add_argument(
        "--input",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="a text field of the graph's input; repeatable",
    )
    run.add_argument(
        "--input-json",
        action="append",
        default=[],
        metavar="NAME=JSON",
        help="a field parsed as JSON, for numbers, booleans, and structure; repeatable",
    )
    run.add_argument("--home", type=Path, default=None, help=HOME_HELP)
    run.add_argument("--record", type=Path, default=None, help="write the run record here as JSON")

    call = commands.add_parser(
        "call", help="call one tool on a configured resource, outside any graph"
    )
    call.add_argument("target", metavar="INSTANCE.CALL", help="e.g. gmail-personal.get_message")
    call.add_argument(
        "--arg",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="an argument to the tool, as text; repeatable",
    )
    call.add_argument(
        "--arg-json",
        action="append",
        default=[],
        metavar="NAME=JSON",
        help="an argument parsed as JSON, for numbers, booleans, and structure; repeatable",
    )
    call.add_argument("--home", type=Path, default=None, help=HOME_HELP)

    return parser


def main() -> int:
    """Parse the command line and hand off to the command.

    Raises:
        SystemExit: argparse rejected the command line.
    """
    args = _parser().parse_args()
    match args.command:
        case "check":
            return check_command(args.file)
        case "run":
            return run_command(args.target, args.input, args.input_json, args.home, args.record)
        case _:
            instance, _, call = str(args.target).rpartition(".")
            return call_command(instance, call, args.arg, args.arg_json, args.home)


if __name__ == "__main__":
    raise SystemExit(main())
