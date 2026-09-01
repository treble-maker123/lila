"""Unit tests for lila.config. No network — nothing here opens a connection."""

from __future__ import annotations

import tomllib
from collections.abc import Callable
from pathlib import Path

import pytest

from lila import config as config_module
from lila.adapters import load
from lila.config import (
    ConfigError,
    InstallConfig,
    adapters_path,
    build_instances,
    build_models,
    build_resource,
    config_path,
    find_home,
    instantiation,
    load_config,
    parse_config,
    skill_bindings,
    skills_path,
)
from lila.model import OllamaModel
from lila.resources import Binding, Bound, Instance, Registry, ResourceError

# region fixtures

ConfigFactory = Callable[[str], InstallConfig]
FIXTURES = Path(__file__).parent / "fixtures" / "adapters"
MAILBOX = "test/fixture/mailbox"

FULL_CONFIG = """
[models.default]
model = "qwen3:8b"
host = "http://localhost:11434"

[resources.fake-inbox]
type = "test/fixture/mailbox"
host = "mail.example.com"
port = 993
secrets = { token = "LILA_INBOX_TOKEN" }

[[skill]]
name = "morning-digest"
source = "test/email-digest"
resources.inbox = { instance = "fake-inbox", tools = { read = "get_message" } }
"""


@pytest.fixture
def config() -> ConfigFactory:
    """Parse a TOML string into an InstallConfig."""

    def build(text: str) -> InstallConfig:
        return parse_config(tomllib.loads(text))

    return build


@pytest.fixture
def registry() -> Registry:
    """The fixture adapter, loaded — what a configured resource is built against."""
    return load(FIXTURES)


# endregion

# region parse_config


def test_parse_config__reads_models_resources_and_skills_when_all_sections_present(
    config: ConfigFactory,
) -> None:
    # prepare / act
    parsed = config(FULL_CONFIG)

    # verify
    assert parsed.models["default"].model == "qwen3:8b"
    assert parsed.resources["fake-inbox"].type == MAILBOX
    assert parsed.resources["fake-inbox"].settings["host"] == "mail.example.com"
    assert parsed.resources["fake-inbox"].secrets == {"token": "LILA_INBOX_TOKEN"}
    assert parsed.skills["morning-digest"].source == "test/email-digest"
    assert parsed.skills["morning-digest"].bindings == {
        "inbox": Binding(instance="fake-inbox", tools={"read": "get_message"})
    }


def test_parse_config__defaults_the_backend_to_ollama_when_unset(config: ConfigFactory) -> None:
    # prepare / act
    parsed = config('[models.default]\nmodel = "qwen3:8b"\n')

    # verify
    assert parsed.models["default"].backend == "ollama"


def test_parse_config__returns_empty_when_the_document_is_empty(config: ConfigFactory) -> None:
    # prepare / act
    parsed = config("")

    # verify
    assert parsed == InstallConfig()


def test_parse_config__defaults_a_skill_to_enabled_when_unset(config: ConfigFactory) -> None:
    # prepare / act
    parsed = config('[[skill]]\nname = "morning-digest"\nsource = "test/email-digest"\n')

    # verify
    assert parsed.skills["morning-digest"].enabled


def test_parse_config__raises_when_two_instantiations_claim_one_name(
    config: ConfigFactory,
) -> None:
    # act / verify
    with pytest.raises(ConfigError, match="two instantiations claim this name"):
        config('[[skill]]\nname = "d"\nsource = "a"\n[[skill]]\nname = "d"\nsource = "b"\n')


def test_parse_config__binds_a_resource_named_like_a_skill_field(config: ConfigFactory) -> None:
    # prepare / act — `resources` is its own table, so no name is reserved
    parsed = config(
        '[[skill]]\nname = "d"\nsource = "s"\nresources.enabled = { instance = "fake-inbox" }\n'
    )

    # verify
    assert parsed.skills["d"].bindings == {"enabled": Binding(instance="fake-inbox")}


def test_parse_config__binds_a_tool_named_like_a_binding_field(config: ConfigFactory) -> None:
    # prepare / act — `tools` is nested, so `instance` is spellable as a call name
    parsed = config(
        '[[skill]]\nname = "d"\nsource = "s"\n'
        'resources.inbox = { instance = "fake-inbox", tools = { instance = "get_message" } }\n'
    )

    # verify
    assert parsed.skills["d"].bindings["inbox"].tools == {"instance": "get_message"}


def test_parse_config__raises_when_a_tool_mapping_is_not_a_string(config: ConfigFactory) -> None:
    # act / verify
    with pytest.raises(ConfigError, match=r"skill.d.resources.inbox.tools.read must be a string"):
        config(
            '[[skill]]\nname = "d"\nsource = "s"\n'
            'resources.inbox = { instance = "fake-inbox", tools = { read = 7 } }\n'
        )


def test_parse_config__raises_when_a_resource_binding_is_not_a_table(
    config: ConfigFactory,
) -> None:
    # act / verify
    with pytest.raises(ConfigError, match=r"skill.d.resources.inbox must be a table"):
        config('[[skill]]\nname = "d"\nsource = "s"\nresources.inbox = "fake-inbox"\n')


def test_parse_config__raises_when_a_skill_is_a_table_rather_than_an_array(
    config: ConfigFactory,
) -> None:
    # act / verify
    with pytest.raises(ConfigError, match=r"\[\[skill\]\], not \[skill\]"):
        config('[skill]\nname = "morning-digest"\n')


def test_parse_config__raises_when_a_required_field_is_missing(config: ConfigFactory) -> None:
    # act / verify
    with pytest.raises(ConfigError, match="missing 'type'"):
        config("[resources.inbox]\nhost = 'imap.example.com'\n")


def test_parse_config__raises_when_a_setting_is_not_a_scalar(config: ConfigFactory) -> None:
    # act / verify
    with pytest.raises(ConfigError, match="must be a string, integer, or boolean"):
        config(f"[resources.inbox]\ntype = '{MAILBOX}'\nhosts = ['a', 'b']\n")


# endregion

# region load_config


def test_load_config__reads_a_file_when_it_exists(tmp_path: Path) -> None:
    # prepare
    path = tmp_path / "config.toml"
    path.write_text(FULL_CONFIG)

    # act
    parsed = load_config(path)

    # verify
    assert parsed.skills["morning-digest"].bindings["inbox"].instance == "fake-inbox"


def test_find_home__walks_up_to_the_nearest_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # prepare — an install at the root, called from two directories down
    monkeypatch.delenv("LILA_HOME", raising=False)
    (tmp_path / ".lila").mkdir()
    nested = tmp_path / "src" / "core"
    nested.mkdir(parents=True)

    # act / verify
    assert find_home(nested) == (tmp_path / ".lila").resolve()


def test_find_home__prefers_the_environment_variable_over_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # prepare
    (tmp_path / ".lila").mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setenv("LILA_HOME", str(elsewhere))

    # act / verify
    assert find_home(tmp_path) == elsewhere.resolve()


def test_find_home__raises_when_the_variable_is_not_a_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # prepare
    monkeypatch.setenv("LILA_HOME", str(tmp_path / "missing"))

    # act / verify
    with pytest.raises(ConfigError, match="not a directory"):
        find_home(tmp_path)


def test_find_home__raises_when_no_install_is_above_the_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # prepare — the filesystem root is the ceiling, and it has no .lila/
    monkeypatch.delenv("LILA_HOME", raising=False)
    monkeypatch.setattr(config_module, "HOME_NAME", ".lila-nothing-uses-this")

    # act / verify
    with pytest.raises(ConfigError, match="create one or set"):
        find_home(tmp_path)


def test_config_path__sits_inside_the_install(tmp_path: Path) -> None:
    # act / verify
    assert config_path(tmp_path) == tmp_path / "config.toml"
    assert adapters_path(tmp_path) == tmp_path / "adapters"
    assert skills_path(tmp_path) == tmp_path / "skills"


def test_load_config__raises_when_the_file_is_absent(tmp_path: Path) -> None:
    # act / verify
    with pytest.raises(ConfigError, match="no config at"):
        load_config(tmp_path / "missing.toml")


def test_load_config__raises_when_the_file_is_not_valid_toml(tmp_path: Path) -> None:
    # prepare
    path = tmp_path / "config.toml"
    path.write_text("[bindings\n")

    # act / verify
    with pytest.raises(ConfigError):
        load_config(path)


# endregion

# region build


def test_build_models__instantiates_an_ollama_backend_per_alias(config: ConfigFactory) -> None:
    # prepare
    parsed = config(FULL_CONFIG)

    # act
    models = build_models(parsed)

    # verify
    assert isinstance(models["default"], OllamaModel)
    assert models["default"].name == "qwen3:8b"


def test_parse_config__reads_the_context_length_when_given(config: ConfigFactory) -> None:
    # prepare / act
    parsed = config('[models.default]\nmodel = "m"\ncontext_length = 32768\n')

    # verify
    assert parsed.models["default"].context_length == 32768


def test_parse_config__leaves_the_context_length_unset_when_absent(config: ConfigFactory) -> None:
    # prepare / act
    parsed = config('[models.default]\nmodel = "m"\n')

    # verify — None means the backend's own default
    assert parsed.models["default"].context_length is None


def test_parse_config__raises_when_the_context_length_is_not_an_integer(
    config: ConfigFactory,
) -> None:
    # act / verify
    with pytest.raises(ConfigError, match="context_length must be an integer"):
        config('[models.default]\nmodel = "m"\ncontext_length = "big"\n')


def test_build_models__raises_when_the_backend_is_unknown(config: ConfigFactory) -> None:
    # prepare
    parsed = config('[models.default]\nmodel = "m"\nbackend = "vllm"\n')

    # act / verify
    with pytest.raises(ConfigError, match="unknown backend"):
        build_models(parsed)


def test_build_resource__builds_the_extensions_own_dataclass(
    config: ConfigFactory,
    registry: Registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # prepare
    monkeypatch.setenv("LILA_INBOX_TOKEN", "app-password")
    parsed = config(FULL_CONFIG)

    # act
    instance = build_resource(parsed.resources["fake-inbox"], registry)

    # verify
    assert instance.name == "fake-inbox"
    assert instance.type == MAILBOX
    assert isinstance(instance.handle, registry.types[MAILBOX])


def test_build_resource__reads_a_secret_from_its_environment_variable(
    config: ConfigFactory,
    registry: Registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # prepare
    monkeypatch.setenv("LILA_INBOX_TOKEN", "app-password")
    parsed = config(FULL_CONFIG)

    # act
    instance = build_resource(parsed.resources["fake-inbox"], registry)

    # verify
    assert getattr(instance.handle, "token") == "app-password"


def test_build_resource__raises_when_the_secret_variable_is_unset(
    config: ConfigFactory,
    registry: Registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # prepare
    monkeypatch.delenv("LILA_INBOX_TOKEN", raising=False)
    parsed = config(FULL_CONFIG)

    # act / verify
    with pytest.raises(ConfigError, match=r"\$LILA_INBOX_TOKEN is not set"):
        build_resource(parsed.resources["fake-inbox"], registry)


def test_build_resource__raises_when_no_adapter_defines_the_type(
    config: ConfigFactory, registry: Registry
) -> None:
    # prepare
    parsed = config("[resources.inbox]\ntype = 'acme/pop3/pop3'\n")

    # act / verify
    with pytest.raises(ConfigError, match="no installed adapter defines"):
        build_resource(parsed.resources["inbox"], registry)


def test_build_resource__raises_when_a_required_setting_is_missing(
    config: ConfigFactory, registry: Registry
) -> None:
    # prepare — the mailbox declares host with no default
    parsed = config("[resources.inbox]\ntype = 'test/fixture/mailbox'\ntoken = 'x'\n")

    # act / verify
    with pytest.raises(ConfigError, match="needs 'host'"):
        build_resource(parsed.resources["inbox"], registry)


def test_build_resource__raises_when_a_setting_is_not_declared(
    config: ConfigFactory, registry: Registry
) -> None:
    # prepare
    parsed = config(
        "[resources.inbox]\ntype = 'test/fixture/mailbox'\n"
        "host = 'a'\ntoken = 'x'\nfolder = 'INBOX'\n"
    )

    # act / verify
    with pytest.raises(ConfigError, match="has no setting 'folder'"):
        build_resource(parsed.resources["inbox"], registry)


def test_build_resource__raises_when_a_setting_is_the_wrong_kind(
    config: ConfigFactory, registry: Registry
) -> None:
    # prepare
    parsed = config(
        "[resources.inbox]\ntype = 'test/fixture/mailbox'\nhost = 'a'\ntoken = 'x'\nport = 'x'\n"
    )

    # act / verify
    with pytest.raises(ConfigError, match="port must be a integer"):
        build_resource(parsed.resources["inbox"], registry)


# endregion

# region skill_bindings


def test_skill_bindings__maps_a_skills_resources_to_configured_instances(
    config: ConfigFactory,
    registry: Registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # prepare
    monkeypatch.setenv("LILA_INBOX_TOKEN", "app-password")
    parsed = config(FULL_CONFIG)
    built = build_instances(parsed, registry)

    # act
    bound = skill_bindings(parsed, "morning-digest", built)

    # verify
    assert bound["inbox"].instance.name == "fake-inbox"
    assert bound["inbox"].tools == {"read": "get_message"}


def test_skill_bindings__raises_when_nothing_goes_by_that_name(
    config: ConfigFactory,
    registry: Registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # prepare
    monkeypatch.setenv("LILA_INBOX_TOKEN", "app-password")
    parsed = config(FULL_CONFIG)
    built = build_instances(parsed, registry)

    # act / verify
    with pytest.raises(ConfigError, match=r"no \[\[skill\]\] named 'other'"):
        skill_bindings(parsed, "other", built)


def test_skill_bindings__raises_when_a_name_is_bound_to_nothing(
    config: ConfigFactory, registry: Registry
) -> None:
    # prepare
    parsed = config(
        '[[skill]]\nname = "morning-digest"\nsource = "test/email-digest"\n'
        "resources.inbox = { instance = 'nowhere' }\n"
    )

    # act / verify
    with pytest.raises(ResourceError, match="no resource configured as 'nowhere'"):
        skill_bindings(parsed, "morning-digest", build_instances(parsed, registry))


def test_bound_tool__raises_naming_what_the_install_did_map(
    config: ConfigFactory,
    registry: Registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # prepare — the grant is the map, so an unmapped call reaches nothing
    monkeypatch.setenv("LILA_INBOX_TOKEN", "app-password")
    parsed = config(FULL_CONFIG)
    bound = skill_bindings(parsed, "morning-digest", build_instances(parsed, registry))

    # act / verify
    with pytest.raises(ResourceError, match=r"not bound to a tool for 'archive'.*\['read'\]"):
        bound["inbox"].tool("archive")


def test_bound_tool__returns_the_tool_one_local_name_was_mapped_to() -> None:
    # prepare
    bound = Bound(instance=Instance(name="i", type=MAILBOX, handle=None), tools={"read": "get"})

    # act / verify
    assert bound.tool("read") == "get"


# endregion

# region instantiation


def test_instantiation__finds_the_copy_a_ref_was_stamped_into(config: ConfigFactory) -> None:
    # prepare
    parsed = config(FULL_CONFIG)

    # act / verify
    assert instantiation(parsed, "test/email-digest").name == "morning-digest"


def test_instantiation__raises_when_the_skill_was_never_instantiated(
    config: ConfigFactory,
) -> None:
    # prepare
    parsed = config(FULL_CONFIG)

    # act / verify
    with pytest.raises(ConfigError, match="installed but not instantiated"):
        instantiation(parsed, "test/other")


def test_instantiation__raises_when_two_copies_share_one_source(config: ConfigFactory) -> None:
    # prepare — the ref no longer says which copy to run
    parsed = config(
        '[[skill]]\nname = "morning"\nsource = "test/email-digest"\n'
        '[[skill]]\nname = "evening"\nsource = "test/email-digest"\n'
    )

    # act / verify
    with pytest.raises(ConfigError, match="instantiated more than once"):
        instantiation(parsed, "test/email-digest")


# endregion
