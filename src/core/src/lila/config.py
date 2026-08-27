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

from lila.executor import ModelAlias, SkillName
from lila.ext import ConfigField, TypeRef, config_fields
from lila.model import DEFAULT_OLLAMA_HOST, Model, OllamaModel
from lila.resources import Instance, InstanceName, Registry, ResourceName

# region names

type EnvVarName = str  # the environment variable a secret is read from
type SettingName = str  # key in a resource's own settings
type Setting = str | int | bool  # what a setting may hold; secrets are not among them

HOME_NAME = ".lila"  # the install directory: config and installed extensions
HOME_VAR = "LILA_HOME"  # overrides discovery, for tests and containers
CONFIG_NAME = "config.toml"
EXTENSIONS_DIR = "extensions"

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
    type: TypeRef  # e.g. ``test/email@1/imap``; the extension defines its fields
    settings: dict[SettingName, Setting] = field(default_factory=dict)
    # Setting name -> env var holding its value, merged over ``settings`` at build time.
    secrets: dict[SettingName, EnvVarName] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SkillConfig:
    """Which instance fills each resource name a skill declares."""

    name: SkillName
    bindings: dict[ResourceName, InstanceName] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class InstallConfig:
    """The whole local install: models, resource instances, per-skill bindings."""

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
    for name, value in _table(raw.get("skills", {}), "skills").items():
        table = _table(value, f"skills.{name}")
        declared = _table(table.get("bindings", {}), f"skills.{name}.bindings")
        skills[name] = SkillConfig(
            name=name,
            bindings={key: _text(declared, key, f"skills.{name}.bindings") for key in declared},
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


def extensions_path(home: FilePath) -> FilePath:
    """Where an install keeps the extensions cloned into it."""
    return home / EXTENSIONS_DIR


def bundled_path() -> FilePath:
    """Where the extensions that ship with the harness live, searched after the install."""
    return FilePath(__file__).resolve().parents[3] / EXTENSIONS_DIR


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
    """Instantiate one configured resource from its extension's own dataclass.

    The extension declares the fields; this only reads them and checks the settings fit.

    Raises:
        ConfigError: the type is not installed, a secret is unset, or a setting is
            missing, unknown, or of the wrong kind.
    """
    resource_type = registry.types.get(config.type)
    if resource_type is None:
        raise ConfigError(
            f"resources.{config.name}: no installed extension defines {config.type!r}"
        )
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


def skill_bindings(
    config: InstallConfig,
    skill: SkillName,
    registry: Registry,
) -> dict[ResourceName, Instance]:
    """Resolve one skill's declared resource names to instances.

    Raises:
        ConfigError: the skill has no config section.
        ResourceError: a name is bound to an instance that is not configured.
    """
    skill_config = config.skills.get(skill)
    if skill_config is None:
        raise ConfigError(f"no [skills.{skill}] section; nothing to bind its resources to")
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
    "bundled_path",
    "config_path",
    "extensions_path",
    "find_home",
    "load_config",
    "parse_config",
    "skill_bindings",
]
