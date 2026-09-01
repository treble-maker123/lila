"""Local install config: which resources exist, which model backs an alias, what a skill binds.

A graph names a resource and a model alias; this file is where those become a real
mailbox and a real backend. Secrets are named by environment variable, never written
inline, so the file itself is safe to read. A DB with encryption replaces `_resolved`
later — nothing above this module learns which it was.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path as FilePath

from lila.executor import ModelAlias
from lila.ext import ConfigField, TypeRef, config_fields
from lila.model import DEFAULT_OLLAMA_HOST, Model, OllamaModel
from lila.resources import (
    Instance,
    InstanceName,
    Registry,
    ResourceName,
    SkillName,
    SkillRef,
)

# region names

type EnvVarName = str  # the environment variable a secret is read from
type SettingName = str  # key in a resource's own settings
type Setting = str | int | bool  # what a setting may hold; secrets are not among them

HOME_NAME = ".lila"  # the install directory: config, and the two trees installed into it
HOME_VAR = "LILA_HOME"  # overrides discovery, for tests and containers
CONFIG_NAME = "config.toml"
ADAPTERS_DIR = "adapters"
SKILLS_DIR = "skills"

# endregion


class ConfigError(ValueError):
    """The config file is missing, malformed, or names something that does not exist."""


@dataclass(frozen=True, slots=True)
class ModelConfig:
    """One model alias and the backend that serves it."""

    alias: ModelAlias
    model: str  # the backend's own model id, e.g. ``qwen3:8b``
    backend: str = "ollama"  # only ollama today; the seam for a bundled runtime
    host: str = DEFAULT_OLLAMA_HOST
    # How much context this machine can afford — hardware, not workflow, so it is bound
    # to the alias rather than declared in a skill. None leaves the backend's default.
    context_length: int | None = None


@dataclass(frozen=True, slots=True)
class ResourceConfig:
    """One configured resource instance: a type ref plus that type's own settings."""

    name: InstanceName
    type: TypeRef  # e.g. ``test/email/imap``; the adapter defines its fields
    settings: dict[SettingName, Setting] = field(default_factory=dict)
    # Setting name -> env var holding its value, merged over ``settings`` at build time.
    secrets: dict[SettingName, EnvVarName] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SkillConfig:
    """One instantiation: a skill this install runs, and which instance fills each of
    its resource names.

    A skill is a template; the install stamps out copies. The key is a name this install
    owns, so renaming the skill upstream cannot silently unbind it — that shows up as an
    unresolvable ``source`` instead.
    """

    name: SkillName  # the run target
    source: SkillRef  # the skill it instantiates
    enabled: bool = True  # nothing runs skills on its own yet, so this gates nothing
    bindings: dict[ResourceName, InstanceName] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class InstallConfig:
    """The whole local install: models, resource instances, instantiated skills."""

    models: dict[ModelAlias, ModelConfig] = field(default_factory=dict)
    resources: dict[InstanceName, ResourceConfig] = field(default_factory=dict)
    skills: dict[SkillName, SkillConfig] = field(default_factory=dict)


# region loading


def _table(raw: object, what: str) -> dict[str, object]:
    """Narrow one tomllib value to a table.

    Raises:
        ConfigError: it is not a table.
    """
    if not isinstance(raw, dict):
        raise ConfigError(f"{what} must be a table")
    return {str(key): value for key, value in raw.items()}


def _optional_int(raw: dict[str, object], key: str, what: str) -> int | None:
    """One integer field, or None when it is absent.

    Raises:
        ConfigError: it is present but not an integer.
    """
    value = raw.get(key)
    if value is None:
        return None
    # bool is an int in Python, and a context length is never True.
    if not isinstance(value, int) or isinstance(value, bool):
        raise ConfigError(f"{what}.{key} must be an integer")
    return value


def _text(raw: dict[str, object], key: str, what: str, default: str | None = None) -> str:
    """One string field.

    Raises:
        ConfigError: it is missing with no default, or is not a string.
    """
    value = raw.get(key, default)
    if value is None:
        raise ConfigError(f"{what} is missing {key!r}")
    if not isinstance(value, str):
        raise ConfigError(f"{what}.{key} must be a string")
    return value


def _settings(raw: dict[str, object], what: str) -> dict[SettingName, Setting]:
    """Provider settings — the scalar keys left after the reserved ones."""
    settings: dict[SettingName, Setting] = {}
    for key, value in raw.items():
        if key in {"type", "secrets"}:
            continue
        if not isinstance(value, (str, int, bool)):
            raise ConfigError(f"{what}.{key} must be a string, integer, or boolean")
        settings[key] = value
    return settings


def _flag(raw: dict[str, object], key: str, what: str, default: bool) -> bool:
    """One boolean field.

    Raises:
        ConfigError: it is present but not a boolean.
    """
    value = raw.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError(f"{what}.{key} must be a boolean")
    return value


def _bindings(raw: dict[str, object], what: str) -> dict[ResourceName, InstanceName]:
    """The ``resources`` sub-table of one instantiation: local name -> instance.

    Dotted keys inside the ``[[skill]]`` block, so a binding cannot drift onto a
    neighboring array element the way a ``[skill.<name>]`` header can.

    Raises:
        ConfigError: ``resources`` or one of its entries is not a table, or an entry
            names no instance.
    """
    bindings: dict[ResourceName, InstanceName] = {}
    for key, value in _table(raw.get("resources", {}), f"{what}.resources").items():
        table = _table(value, f"{what}.resources.{key}")
        bindings[key] = _text(table, "instance", f"{what}.resources.{key}")
    return bindings


def _secrets(raw: dict[str, object], what: str) -> dict[SettingName, EnvVarName]:
    """The ``secrets`` sub-table: setting name -> env var name."""
    if "secrets" not in raw:
        return {}
    table = _table(raw["secrets"], f"{what}.secrets")
    return {key: _text(table, key, f"{what}.secrets") for key in table}


def parse_config(raw: dict[str, object]) -> InstallConfig:
    """Build an InstallConfig from a parsed TOML document.

    Raises:
        ConfigError: a section is malformed or a required field is missing.
    """
    models: dict[ModelAlias, ModelConfig] = {}
    for alias, value in _table(raw.get("models", {}), "models").items():
        table = _table(value, f"models.{alias}")
        models[alias] = ModelConfig(
            alias=alias,
            model=_text(table, "model", f"models.{alias}"),
            backend=_text(table, "backend", f"models.{alias}", "ollama"),
            host=_text(table, "host", f"models.{alias}", DEFAULT_OLLAMA_HOST),
            context_length=_optional_int(table, "context_length", f"models.{alias}"),
        )

    resources: dict[InstanceName, ResourceConfig] = {}
    for name, value in _table(raw.get("resources", {}), "resources").items():
        table = _table(value, f"resources.{name}")
        resources[name] = ResourceConfig(
            name=name,
            type=_text(table, "type", f"resources.{name}"),
            settings=_settings(table, f"resources.{name}"),
            secrets=_secrets(table, f"resources.{name}"),
        )

    skills: dict[SkillName, SkillConfig] = {}
    declared = raw.get("skill", [])
    if not isinstance(declared, list):
        raise ConfigError("skill must be an array of tables — [[skill]], not [skill]")
    for index, value in enumerate(declared):
        table = _table(value, f"skill[{index}]")
        name = _text(table, "name", f"skill[{index}]")
        what = f"skill.{name}"
        if name in skills:
            raise ConfigError(f"{what}: two instantiations claim this name")
        skills[name] = SkillConfig(
            name=name,
            source=_text(table, "source", what),
            enabled=_flag(table, "enabled", what, True),
            bindings=_bindings(table, what),
        )

    return InstallConfig(models=models, resources=resources, skills=skills)


def find_home(start: FilePath | None = None) -> FilePath:
    """The install directory: ``$LILA_HOME``, else the nearest ``.lila/`` at or above ``start``.

    Walking up means a command works from anywhere in the tree, not only its root. There
    is no fallback: an install is somewhere explicit, never somewhere assumed.

    Raises:
        ConfigError: no ``.lila/`` above the starting directory, or ``$LILA_HOME`` is
            not a directory.
    """
    override = os.environ.get(HOME_VAR)
    if override is not None:
        home = FilePath(override)
        if not home.is_dir():
            raise ConfigError(f"${HOME_VAR} is {override!r}, which is not a directory")
        return home.resolve()
    origin = (start or FilePath.cwd()).resolve()
    for directory in (origin, *origin.parents):
        candidate = directory / HOME_NAME
        if candidate.is_dir():
            return candidate
    raise ConfigError(f"no {HOME_NAME}/ at or above {origin}; create one or set ${HOME_VAR}")


def config_path(home: FilePath) -> FilePath:
    """Where an install keeps its config."""
    return home / CONFIG_NAME


def adapters_path(home: FilePath) -> FilePath:
    """Where an install keeps the adapters cloned into it."""
    return home / ADAPTERS_DIR


def skills_path(home: FilePath) -> FilePath:
    """Where an install keeps its skills — published, cloned, or written by hand."""
    return home / SKILLS_DIR


def bundled_path(tree: str) -> FilePath:
    """Where one of the trees that ships with the harness lives, searched after the install."""
    return FilePath(__file__).resolve().parents[3] / tree


def load_config(path: FilePath | str | None = None) -> InstallConfig:
    """Read and parse a config file, defaulting to the discovered install's own.

    Raises:
        ConfigError: no install was found, the file does not exist, or it is not
            valid TOML.
    """
    file_path = FilePath(path) if path is not None else config_path(find_home())
    try:
        raw = tomllib.loads(file_path.read_text())
    except FileNotFoundError as exc:
        raise ConfigError(f"no config at {file_path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{file_path}: {exc}") from exc
    return parse_config(raw)


# endregion

# region building


def build_model(config: ModelConfig) -> Model:
    """Instantiate one alias's backend.

    Raises:
        ConfigError: the backend is unknown.
    """
    if config.backend != "ollama":
        raise ConfigError(f"models.{config.alias}: unknown backend {config.backend!r}")
    return OllamaModel(config.model, host=config.host, context_length=config.context_length)


def build_models(config: InstallConfig) -> dict[ModelAlias, Model]:
    """Every declared alias, instantiated."""
    return {alias: build_model(model) for alias, model in config.models.items()}


def _resolved(config: ResourceConfig) -> dict[SettingName, Setting]:
    """Settings with each secret read from its environment variable.

    Raises:
        ConfigError: a named variable is unset.
    """
    resolved = dict(config.settings)
    for setting, variable in config.secrets.items():
        value = os.environ.get(variable)
        if value is None:
            raise ConfigError(f"resources.{config.name}.secrets.{setting}: ${variable} is not set")
        resolved[setting] = value
    return resolved


def build_resource(config: ResourceConfig, registry: Registry) -> Instance:
    """Instantiate one configured resource from its adapter's own dataclass.

    The adapter declares the fields; this only reads them and checks the settings fit.

    Raises:
        ConfigError: the type is not installed, a secret is unset, or a setting is
            missing, unknown, or of the wrong kind.
    """
    resource_type = registry.types.get(config.type)
    if resource_type is None:
        raise ConfigError(f"resources.{config.name}: no installed adapter defines {config.type!r}")
    settings = _resolved(config)
    fields = {declared.name: declared for declared in config_fields(resource_type)}
    unknown = sorted(set(settings) - set(fields))
    if unknown:
        raise ConfigError(f"resources.{config.name}: {config.type} has no setting {unknown[0]!r}")
    arguments = {
        name: _setting(config, declared, settings[name])
        for name, declared in fields.items()
        if name in settings
    }
    missing = sorted(
        name for name, declared in fields.items() if declared.required and name not in settings
    )
    if missing:
        raise ConfigError(f"resources.{config.name}: {config.type} needs {missing[0]!r}")
    return Instance(name=config.name, type=config.type, handle=resource_type(**arguments))


def _setting(config: ResourceConfig, declared: ConfigField, value: Setting) -> Setting:
    """One setting, checked against the field's derived schema.

    Raises:
        ConfigError: the value is not the kind the field declares.
    """
    kinds: dict[str, type | tuple[type, ...]] = {
        "string": str,
        "integer": int,
        "boolean": bool,
        "number": (int, float),
    }
    expected = kinds.get(str(declared.schema.get("type", "")))
    # bool is an int in Python, and a port is never True.
    mistyped = expected is not None and (
        not isinstance(value, expected) or (expected is not bool and isinstance(value, bool))
    )
    if mistyped:
        raise ConfigError(
            f"resources.{config.name}.{declared.name} must be a {declared.schema.get('type')}"
        )
    return value


def build_instances(config: InstallConfig, registry: Registry) -> Registry:
    """Instantiate every configured resource into the registry, and return it."""
    for resource in config.resources.values():
        registry.register(build_resource(resource, registry))
    return registry


def instantiation(config: InstallConfig, source: SkillRef) -> SkillConfig:
    """The instantiation of one skill, for a run that named the skill and not a copy.

    Raises:
        ConfigError: nothing instantiates it, or several do and the run must say which.
    """
    found = [skill for skill in config.skills.values() if skill.source == source]
    if not found:
        raise ConfigError(f"{source!r} is installed but not instantiated; add a [[skill]] for it")
    if len(found) > 1:
        raise ConfigError(
            f"{source!r} is instantiated more than once; run one by name: "
            f"{sorted(skill.name for skill in found)}"
        )
    return found[0]


def skill_bindings(
    config: InstallConfig,
    skill: SkillName | None,
    registry: Registry,
) -> dict[ResourceName, Instance]:
    """Resolve one instantiation's declared resource names to instances.

    Raises:
        ConfigError: no instantiation goes by that name, or the run named none at all.
        ResourceError: a name is bound to an instance that is not configured.
    """
    skill_config = config.skills.get(skill) if skill is not None else None
    if skill_config is None:
        raise ConfigError(f"no [[skill]] named {skill!r}; nothing to bind its resources to")
    return {name: registry.instance(binding) for name, binding in skill_config.bindings.items()}


# endregion

__all__ = [
    "ConfigError",
    "InstallConfig",
    "ModelConfig",
    "ResourceConfig",
    "SkillConfig",
    "build_instances",
    "build_models",
    "build_resource",
    "adapters_path",
    "bundled_path",
    "config_path",
    "find_home",
    "instantiation",
    "load_config",
    "parse_config",
    "skill_bindings",
    "skills_path",
]
