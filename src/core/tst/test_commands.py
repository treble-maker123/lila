"""Unit tests for lila.commands. Only the model is faked; the install is real."""

from __future__ import annotations

import json
import shutil
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from lila import commands
from lila.commands import call_command, check_command, parse_input, run_command
from lila.config import InstallConfig
from lila.model import GenerateEvent, GenerateOptions, Message, Model, TextChunk, Usage
from lila.values import Json

# region fixtures

FIXTURES = Path(__file__).parent / "fixtures"

# The tests own their skill: the real one lives in an untracked install, so a checkout
# must not need it to run the suite.
DIGEST_SKILL = """
resources: { inbox: test/fixture/mailbox }
input: { type: object, properties: {} }
output:
  type: object
  properties: { digest: { type: string } }
  required: [digest]
entry: list
nodes:
  - id: list
    type: tool
    resource: inbox
    call: list_messages
    args: { limit: 2 }

  - id: summaries
    type: skill.run
    for_each: $.list.ids
    resources: { inbox: inbox }
    input: { message_id: $.each }
    graph:
      resources: { inbox: test/fixture/mailbox }
      input:
        type: object
        properties: { message_id: { type: string } }
        required: [message_id]
      output:
        type: object
        properties: { summary: { type: string } }
        required: [summary]
      entry: fetch
      nodes:
        - id: fetch
          type: tool
          resource: inbox
          call: get_message
          args: { id: $.input.message_id }
        - id: summarize
          type: llm
          prompt: "Summarize: {{ $.fetch.subject }}"
          out:
            type: object
            properties: { summary: { type: string } }
            required: [summary]
      edges:
        - { from: fetch, to: summarize }
        - { from: summarize, to: end }
      return: { summary: $.summarize.summary }

  - id: digest
    type: llm
    prompt: "Combine: {{ $.summaries }}"
    out:
      type: object
      properties: { digest: { type: string } }
      required: [digest]
edges:
  - { from: list, to: summaries }
  - { from: summaries, to: digest }
  - { from: digest, to: end }
return: { digest: $.digest.digest }
"""

CONFIG = """
[models.default]
model = "scripted"

[resources.fake-inbox]
type = "test/fixture/mailbox"
host = "mail.example.com"
token = "app-password"

[[skill]]
name = "morning-digest"
source = "test/email-digest"
resources.inbox = { instance = "fake-inbox" }
"""


class ScriptedModel(Model):
    """Backend replaying fixed completions in order, so an llm node needs no daemon."""

    def __init__(self, texts: list[str]) -> None:
        self.texts = texts
        self.prompts: list[str] = []

    @property
    def name(self) -> str:
        return "scripted"

    async def generate(
        self,
        messages: list[Message],
        options: GenerateOptions | None = None,
    ) -> AsyncIterator[GenerateEvent]:
        self.prompts.append(messages[-1].content)
        yield TextChunk(text=self.texts.pop(0))
        yield Usage(prompt_tokens=1, completion_tokens=2, done_reason="stop")


@pytest.fixture
def home(tmp_path: Path) -> Path:
    """A real install: the fixture adapter cloned in, plus the digest skill beside it."""
    home = tmp_path / ".lila"
    shutil.copytree(FIXTURES / "adapters", home / "adapters")
    digest = home / "skills" / "test" / "email-digest"
    digest.mkdir(parents=True)
    (digest / "skill.yaml").write_text(DIGEST_SKILL)
    (home / "config.toml").write_text(CONFIG)
    return home


@pytest.fixture
def model(monkeypatch: pytest.MonkeyPatch) -> ScriptedModel:
    """Bind the ``default`` alias to a scripted backend rather than a daemon."""
    # One completion per mapped summary, then one for the reduce.
    scripted = ScriptedModel(
        [
            json.dumps({"summary": "a statement is ready"}),
            json.dumps({"summary": "a friend asks about lunch"}),
            json.dumps({"digest": "one bill, one invitation"}),
        ]
    )
    monkeypatch.setattr(commands, "build_models", lambda config: {"default": scripted})
    return scripted


# endregion

# region parse_input


def test_parse_input__keeps_a_value_as_text_when_it_looks_like_a_number() -> None:
    # prepare / act — a message id is text, and JSON would silently make it an int
    parsed = parse_input(["message_id=7", "subject=hello there"], [])

    # verify
    assert parsed == {"message_id": "7", "subject": "hello there"}


def test_parse_input__parses_json_when_given_as_a_json_pair() -> None:
    # prepare / act
    parsed = parse_input([], ["count=3", "flag=true", 'tags=["a"]'])

    # verify
    assert parsed == {"count": 3, "flag": True, "tags": ["a"]}


def test_parse_input__raises_when_a_pair_has_no_equals() -> None:
    # act / verify
    with pytest.raises(ValueError, match="expects name=value"):
        parse_input(["message_id"], [])


def test_parse_input__raises_when_a_json_value_does_not_parse() -> None:
    # act / verify
    with pytest.raises(ValueError, match="--input-json count"):
        parse_input([], ["count=three"])


# endregion

# region check_command


def test_check_command__succeeds_when_the_graph_checks_clean(home: Path) -> None:
    # act / verify
    assert check_command(home / "skills" / "test" / "email-digest" / "skill.yaml") == 0


def test_check_command__fails_when_the_graph_does_not_parse(tmp_path: Path) -> None:
    # prepare
    broken = tmp_path / "broken.yaml"
    broken.write_text("entry: gone\nnodes: []\nedges: []\n")

    # act / verify
    assert check_command(broken) == 1


# endregion

# region run_command


def test_run_command__runs_an_instantiated_skill_by_the_name_the_install_gave_it(
    capsys: pytest.CaptureFixture[str],
    model: ScriptedModel,
    home: Path,
) -> None:
    # prepare / act — the [[skill]] name, not the skill's own ref
    code = run_command("morning-digest", [], [], home)

    # verify
    assert code == 0
    assert json.loads(capsys.readouterr().out) == {"digest": "one bill, one invitation"}


def test_run_command__runs_an_installed_skill_by_ref(
    capsys: pytest.CaptureFixture[str],
    model: ScriptedModel,
    home: Path,
) -> None:
    # prepare / act — a ref, not a path; one instantiation names it, so it is unambiguous
    code = run_command("test/email-digest", [], [], home)

    # verify
    assert code == 0
    assert json.loads(capsys.readouterr().out) == {"digest": "one bill, one invitation"}
    # One child run per id the mapped node fanned out over.
    assert len(model.prompts) == 3


def test_run_command__writes_the_record_when_given_a_path(
    tmp_path: Path,
    model: ScriptedModel,
    home: Path,
) -> None:
    # prepare
    record = tmp_path / "record.json"
    skill = home / "skills" / "test" / "email-digest" / "skill.yaml"

    # act — a path this time, rather than an installed ref
    code = run_command(str(skill), [], [], home, record)

    # verify
    assert code == 0
    written: dict[str, Json] = json.loads(record.read_text())
    # A path that is an installed skill is still recorded under its ref, and under the
    # instantiation it ran as.
    assert written["skill"] == "test/email-digest"
    assert written["name"] == "morning-digest"
    nodes = written["nodes"]
    assert isinstance(nodes, list)
    entries = [entry for entry in nodes if isinstance(entry, dict)]
    assert [entry["node_id"] for entry in entries] == ["list", "summaries", "digest"]
    # The map node keeps one child record per item, rather than flattening them.
    children = entries[1]["children"]
    assert isinstance(children, list)
    assert len(children) == 2


def test_run_command__records_the_resource_name_not_the_instance(
    tmp_path: Path,
    model: ScriptedModel,
    home: Path,
) -> None:
    # prepare
    record = tmp_path / "record.json"

    # act
    run_command("test/email-digest", [], [], home, record)

    # verify — the name reaches the record; the instance and its credentials do not
    written: dict[str, Json] = json.loads(record.read_text())
    nodes = written["nodes"]
    assert isinstance(nodes, list) and isinstance(nodes[0], dict)
    assert nodes[0]["resources"] == ["inbox"]
    assert "app-password" not in record.read_text()


def test_run_command__fails_when_the_ref_is_not_installed(model: ScriptedModel, home: Path) -> None:
    # act / verify
    assert run_command("nothing-like-this", [], [], home) == 1


def test_run_command__fails_when_the_skill_is_installed_but_not_instantiated(
    model: ScriptedModel, home: Path
) -> None:
    # prepare — the config no longer stamps out a copy of the skill
    (home / "config.toml").write_text(CONFIG.split("[[skill]]")[0])

    # act / verify
    assert run_command("test/email-digest", [], [], home) == 1


def test_run_command__fails_when_a_resource_is_unbound(model: ScriptedModel, home: Path) -> None:
    # prepare — instantiated, but nothing says which instance fills ``inbox``
    (home / "config.toml").write_text(CONFIG.split("resources.inbox")[0])

    # act / verify
    assert run_command("morning-digest", [], [], home) == 1


# endregion

# region call_command


def test_call_command__prints_what_the_tool_returned(
    capsys: pytest.CaptureFixture[str],
    home: Path,
) -> None:
    # prepare / act
    code = call_command("fake-inbox", "get_message", ["id=1"], home)

    # verify
    assert code == 0
    assert json.loads(capsys.readouterr().out) == {"id": "1", "subject": "subject 1"}


def test_call_command__fails_when_the_instance_is_not_configured(home: Path) -> None:
    # act / verify
    assert call_command("nowhere", "list_messages", [], home) == 1


def test_call_command__fails_when_the_type_has_no_such_tool(home: Path) -> None:
    # act / verify
    assert call_command("fake-inbox", "burn_inbox", [], home) == 1


# endregion
