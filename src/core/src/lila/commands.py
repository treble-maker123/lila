"""What each CLI command does, independent of how it was spelled on a command line.

Every command takes plain values, returns a process exit code, and writes results to
stdout and diagnostics to stderr. ``lila.main`` only parses arguments and calls these,
which is what keeps it the one module with no tests of its own.
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path as FilePath

from lila.adapters import load as load_adapters
from lila.config import (
    ADAPTERS_DIR,
    SKILLS_DIR,
    ConfigError,
    InstallConfig,
    adapters_path,
    build_instances,
    build_models,
    bundled_path,
    config_path,
    find_home,
    load_config,
    skill_bindings,
    skills_path,
)
from lila.executor import Graph, GraphError, RunContext, RunError, load_graph, run
from lila.ext import ExtError, ToolError, ToolName
from lila.install import InstallError
from lila.model import ModelError, OllamaModel
from lila.resources import ArgName, InstanceName, Registry, ResourceError, SkillRef
from lila.skills import discover as discover_skills
from lila.skills import resolve_skill
from lila.tools import default_handlers
from lila.values import Json
from lila.verification import check

OK = 0
FAILED = 1


def parse_input(pairs: list[str], json_pairs: list[str]) -> dict[ArgName, Json]:
    """``k=v`` pairs into a graph's ``$.input``.

    Text pairs stay text — a message id is ``7``, not the number 7 — and JSON pairs are
    the opt-in for numbers, booleans, and structure.

    Raises:
        ValueError: a pair has no ``=``, or a JSON value does not parse.
    """
    parsed: dict[ArgName, Json] = {}
    for pair in pairs:
        name, separator, raw = pair.partition("=")
        if not separator:
            raise ValueError(f"--input expects name=value, got {pair!r}")
        parsed[name] = raw
    for pair in json_pairs:
        name, separator, raw = pair.partition("=")
        if not separator:
            raise ValueError(f"--input-json expects name=value, got {pair!r}")
        try:
            parsed[name] = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"--input-json {name}: {exc}") from exc
    return parsed


def build_registry(home: FilePath, config: InstallConfig) -> Registry:
    """Everything installed here: adapters loaded, skills indexed, instances built.

    Each tree is searched in the install first, then among what ships with the harness.

    Raises:
        ConfigError: a configured resource names a type nothing defines.
        InstallError, ExtError: an adapter or a skill directory is malformed.
    """
    registry = load_adapters(adapters_path(home), bundled_path(ADAPTERS_DIR))
    registry.skills.update(discover_skills(skills_path(home), bundled_path(SKILLS_DIR)))
    return build_instances(config, registry)


@dataclass(frozen=True, slots=True)
class Target:
    """What a command was pointed at: the graph file, and the ref it is known by."""

    path: FilePath
    ref: SkillRef


def load_checked(
    target: Target,
    config: InstallConfig | None = None,
    registry: Registry | None = None,
) -> Graph | None:
    """Load a graph and static-check it before anything runs, reporting to stderr.

    Returns None when the file does not parse or the check found issues.
    """
    try:
        graph = load_graph(target.path, target.ref)
    except GraphError as exc:
        print(f"{target.path}: {exc}", file=sys.stderr)
        return None
    skill = config.skills.get(graph.ref) if config is not None else None
    issues = check(graph, skill.bindings if skill is not None else None, registry)
    for issue in issues:
        print(str(issue), file=sys.stderr)
    return None if issues else graph


def resolve_target(target: str, registry: Registry | None = None) -> Target:
    """A graph file: a path when one exists, otherwise a skill ref installed here.

    Lets ``lila run test/email-digest`` mean the same thing as its full path: a file
    that is an installed skill keeps that skill's ref, so both spellings bind alike. A
    graph nothing installed points at is identified by its path.

    Raises:
        ConfigError: the ref is not installed, and no such file exists.
    """
    known = (registry or Registry()).skills
    as_file = FilePath(target)
    if as_file.exists():
        resolved = as_file.resolve()
        ref = next((ref for ref, path in known.items() if path.resolve() == resolved), target)
        return Target(path=as_file, ref=ref)
    installed = known.get(target)
    if installed is None:
        raise ConfigError(
            f"{target!r} is neither a file nor an installed skill; here: {sorted(known)}"
        )
    return Target(path=installed, ref=target)


def check_command(path: FilePath) -> int:
    """``lila check`` — load and statically check one graph file."""
    graph = load_checked(Target(path=path, ref=str(path)))
    if graph is None:
        return FAILED
    summary = f" — {graph.description}" if graph.description else ""
    print(f"{graph.ref}: ok{summary}")
    return OK


async def execute(
    graph: Graph,
    run_input: dict[ArgName, Json],
    config: InstallConfig,
    registry: Registry,
    record_path: FilePath | None,
) -> None:
    """Bind resources and models from config, run the graph, print its output.

    Raises:
        ConfigError, ResourceError, RunError, ModelError: as raised by the run.
    """
    models = build_models(config)
    resources = skill_bindings(config, graph.ref, registry)
    context = RunContext(
        handlers=default_handlers(),
        models=models,
        registry=registry,
        # A ``ref:`` resolves against the skills installed here, never a path.
        skills=resolve_skill(registry),
    )
    try:
        result = await run(graph, run_input, context, resources)
    finally:
        for model in models.values():
            if isinstance(model, OllamaModel):
                await model.aclose()

    print(json.dumps(result.output, indent=2))
    record = result.record
    steps = " -> ".join(entry.node_id for entry in record.nodes)
    print(f"{record.skill}: {steps}", file=sys.stderr)
    if record_path is not None:
        record_path.write_text(json.dumps(asdict(record), indent=2, default=str))


def run_command(
    target: str,
    pairs: list[str],
    json_pairs: list[str],
    home_path: FilePath | None = None,
    record_path: FilePath | None = None,
) -> int:
    """``lila run`` — check a graph, then run it against the install it belongs to.

    ``target`` is a path to a graph file or the ref of a skill installed in home.
    """
    try:
        home = home_path.resolve() if home_path is not None else find_home()
        config = load_config(config_path(home))
        registry = build_registry(home, config)
        found = resolve_target(target, registry)
    except (ConfigError, InstallError, ExtError) as exc:
        print(str(exc), file=sys.stderr)
        return FAILED
    # Which install answered matters when more than one exists.
    print(f"home: {home}", file=sys.stderr)

    graph = load_checked(found, config, registry)
    if graph is None:
        return FAILED
    try:
        run_input = parse_input(pairs, json_pairs)
        asyncio.run(execute(graph, run_input, config, registry, record_path))
    except (ValueError, ConfigError, ResourceError, RunError, ModelError) as exc:
        print(f"{found.path}: {exc}", file=sys.stderr)
        return FAILED
    return OK


def call_command(
    instance: InstanceName,
    call: ToolName,
    pairs: list[str],
    home_path: FilePath | None = None,
) -> int:
    """``lila call`` — one tool call on a configured instance, outside any graph.

    The way to see a provider's real answers: message ids to feed a run, and the first
    thing to reach for when a provider misbehaves.
    """
    try:
        home = home_path.resolve() if home_path is not None else find_home()
        config = load_config(config_path(home))
        registry = build_registry(home, config)
        found = registry.instance(instance)
        tool = registry.tool(found.type, call)
        output = tool.run(found.handle, **parse_input(pairs, []))
    except (ValueError, TypeError, ConfigError, ResourceError, InstallError, ToolError) as exc:
        print(f"{instance}.{call}: {exc}", file=sys.stderr)
        return FAILED
    print(json.dumps(output, indent=2))
    return OK
