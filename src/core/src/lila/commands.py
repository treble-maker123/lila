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
    SkillConfig,
    adapters_path,
    build_instances,
    build_models,
    bundled_path,
    config_path,
    find_home,
    instantiation,
    load_config,
    skill_bindings,
    skills_path,
)
from lila.executor import Graph, GraphError, RunContext, RunError, load_graph, run
from lila.ext import ExtError, ToolError, ToolName
from lila.install import InstallError
from lila.model import ModelError, OllamaModel
from lila.resources import (
    ArgName,
    InstanceName,
    Registry,
    ResourceError,
    SkillName,
    SkillRef,
)
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
    """What a command was pointed at: the graph file, the ref it is known by, and the
    instantiation supplying its bindings. ``lila check`` takes a file and has none."""

    path: FilePath
    ref: SkillRef
    name: SkillName | None = None


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
    skill = config.skills.get(target.name) if config is not None and target.name else None
    issues = check(graph, skill.bindings if skill is not None else None, registry)
    for issue in issues:
        print(str(issue), file=sys.stderr)
    return None if issues else graph


def _source_path(skill: SkillConfig, registry: Registry) -> FilePath:
    """The graph one instantiation runs.

    Raises:
        ConfigError: its ``source`` names no installed skill — which is what an upstream
            rename looks like.
    """
    path = registry.skills.get(skill.source)
    if path is None:
        raise ConfigError(
            f"skill.{skill.name}: no installed skill {skill.source!r}; "
            f"installed: {sorted(registry.skills)}"
        )
    return path


def resolve_target(target: str, config: InstallConfig, registry: Registry) -> Target:
    """What to run: an instantiation name, a graph file, or an installed skill ref.

    Instantiation names win — that is the name this install chose, and the only spelling
    that can distinguish two copies of one skill. A file or a ref still runs, under the
    single instantiation naming it as ``source``, so ``lila run test/email-digest`` means
    the same thing as its full path.

    Raises:
        ConfigError: nothing here answers to the name, or the skill it names is
            installed but never instantiated.
    """
    named = config.skills.get(target)
    if named is not None:
        return Target(path=_source_path(named, registry), ref=named.source, name=target)
    known = registry.skills
    as_file = FilePath(target)
    if as_file.exists():
        resolved = as_file.resolve()
        ref = next((ref for ref, path in known.items() if path.resolve() == resolved), target)
        return Target(path=as_file, ref=ref, name=instantiation(config, ref).name)
    installed = known.get(target)
    if installed is None:
        raise ConfigError(
            f"{target!r} is neither an instantiated skill nor a file nor installed here; "
            f"instantiated: {sorted(config.skills)}; installed: {sorted(known)}"
        )
    return Target(path=installed, ref=target, name=instantiation(config, target).name)


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
    name: SkillName | None,
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
    resources = skill_bindings(config, name, registry)
    context = RunContext(
        handlers=default_handlers(),
        models=models,
        registry=registry,
        # A ``ref:`` resolves against the skills installed here, never a path.
        skills=resolve_skill(registry),
    )
    try:
        result = await run(graph, run_input, context, resources, name)
    finally:
        for model in models.values():
            if isinstance(model, OllamaModel):
                await model.aclose()

    print(json.dumps(result.output, indent=2))
    record = result.record
    steps = " -> ".join(entry.node_id for entry in record.nodes)
    print(f"{record.name} ({record.skill}): {steps}", file=sys.stderr)
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

    ``target`` is an instantiation's name, a path to a graph file, or an installed ref.
    """
    try:
        home = home_path.resolve() if home_path is not None else find_home()
        config = load_config(config_path(home))
        registry = build_registry(home, config)
        found = resolve_target(target, config, registry)
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
        asyncio.run(execute(graph, found.name, run_input, config, registry, record_path))
    except (ValueError, ConfigError, ResourceError, RunError, ModelError) as exc:
        print(f"{found.path}: {exc}", file=sys.stderr)
        return FAILED
    return OK


def call_command(
    instance: InstanceName,
    call: ToolName,
    pairs: list[str],
    json_pairs: list[str],
    home_path: FilePath | None = None,
) -> int:
    """``lila call`` — one tool call on a configured instance, outside any graph.

    The way to see a provider's real answers: message ids to feed a run, and the first
    thing to reach for when a provider misbehaves.

    ``pairs`` stay text and ``json_pairs`` are parsed, the same split ``lila run`` makes:
    a tool taking an int gets one only through ``--arg-json``.
    """
    try:
        home = home_path.resolve() if home_path is not None else find_home()
        config = load_config(config_path(home))
        registry = build_registry(home, config)
        found = registry.instance(instance)
        tool = registry.tool(found.type, call)
        output = tool.run(found.handle, **parse_input(pairs, json_pairs))
    except (ValueError, TypeError, ConfigError, ResourceError, InstallError, ToolError) as exc:
        print(f"{instance}.{call}: {exc}", file=sys.stderr)
        return FAILED
    print(json.dumps(output, indent=2))
    return OK
