# Adapters and Skills

**Status**: on-going — problem stated, terminology settled, interface decided (explicit
resource + tool binding, structural check as a lint). Skills become install-instantiated,
and `extensions/` splits into two manifest-free trees, `adapters/` and `skills/`, with the
path as identity and versioning left to git. Open: how a `ref:` subgraph's binding reaches it.
Four tasks at the bottom, scoped to a thin slice, with what they defer under "Later".
**Tasks 1–3 are done**, including task 2's follow-up — bindings live inside the
`[[skill]]` block as `resources.<name>` dotted keys; 4 is not started.

## Goal

Let a skill use two integrations without either integration knowing about the other.

Concretely: `email-digest` should be able to post its digest to Discord, without
`test-email` acquiring a dependency on `test-discord`.

## Background

Today an `extension` fuses two things with opposite dependency shapes:

| | Depends on | Example |
|---|---|---|
| Resource type + its tools | core only — always a leaf | `Imap` + `list_messages` |
| Skill (a graph) | N integrations — cross-cutting by nature | "digest my inbox, post it to chat" |

Every skill must live inside exactly one integration's package. So the moment a skill
becomes useful, it drags an illegitimate dependency into a package that should be a leaf.

`requires` is the wrong tool for this. It gates on installed type refs, so using it here
makes an IMAP adapter unloadable without a chat integration.

## Constraints

- Adapters and skills ship separately — different repos, different release cadences.
  Cross-repo references are the expensive kind.
- Core ships no interface vocabulary. Blessing a set of ports makes core a gatekeeper.
- A skill must survive an adapter version bump it did not ask for.
- Skill requirements are not always known before the run. See "Skill types" below.

## Terminology

Settled:

| Term | Is | Note |
|---|---|---|
| **adapter** | a resource type + the tools over it | deliberately unglamorous — users shouldn't think about it |
| **skill** | one graph | unchanged |

Two nouns, no third. A shipped bundle of skills needs no name of its own — it is a repo
with `skills/` in it.

Considered and dropped: `provider` (Terraform — closest structural match, but has no word
for the interface), `driver`, `connector` (collides with MCP), `integration`, `playbook`,
`skillset`, `repertoire`.

`adapter` comes from hexagonal architecture, which supplies **port** for the interface.
Whether we adopt that half is the open question below.

## The split

### What an adapter is

Ships `code/`. Declares resource types and tools. Never declares skills that reference
another adapter's types. Stays a leaf.

An adapter repo may still carry skills — a single-adapter demo (`discord-notify`) is
useful documentation and adds no cross-dependency. It is shipping a skill that happens to
sit in the same repo, not a different kind of thing. (Where those skills get *installed*
changed: see "Where skills live".)

### What a skill is

Ships as a graph, with no `code/`. Declares slots and fills them from whatever is
installed. Skills are the only thing permitted to depend on more than one adapter.

Where a skill lives is a packaging detail, not a category: a repo of skills, a
`skills/` directory alongside an adapter's `code/`, or a loose file in the install. The
rule is about the dependency direction, not the container.

### Where a local, unpublished skill lives

Same tier as any other skill, just never published: graphs the user wrote for one install.

"Post my digest to my Discord" is a fact about one install, like a binding. It wants no
repo, no owner, no version.

Today there is nowhere to put it — `registry.skills` is populated exclusively from
extension manifests, so authoring a personal graph means inventing a fake publisher. A
local skills directory in the install closes this, and removes the pressure to publish
glue.

**Resolved by "Where skills live":** the skills tree is where every skill goes, published or
not, so a local one needs no separate mechanism. Folded into task 1.

### Skill types on the horizon

Not in scope to build, but they constrain the interface choice:

- **Ad-hoc** — an agent proposes a skill, tests it, asks the user to verify, runs it.
- **Dynamic** — a graph that adds nodes and edges while executing, Temporal-style.

Both mean a skill's resource requirements are not fully known at load time. Any interface
mechanism that requires the requirement to be registered ahead of time makes these a
special case.

## The interface between them

The question: how does a skill say what it needs, without naming a specific adapter?

Naming a concrete type ref (`test/discord@1/channel`) is what we have. Across separate
repos it is a hard reference to another repo at a pinned version. An adapter bumping to
`@2` breaks every skill that mentions it, and a skill that wants "somewhere to post" is
welded to Discord specifically.

### The shape

**Decided:** bind resources explicitly; name tools locally in the skill and map them
explicitly too. A skill file carries **no identifier owned by anyone else** — no type
refs, no adapter tool names. Everything foreign lives in one install-side block.

```yaml
# skill
resources: [inbox, notify]
nodes:
  - id: list
    resource: inbox
    call: fetch          # a name this skill owns
  - id: post
    resource: notify
    call: send
```

```toml
[skills.email-digest.inbox]
instance = "gmail-personal"
fetch    = "list_messages"

[skills.email-digest.notify]
instance = "discord-alerts"
send     = "post_message"
```

Today `digest.yaml` writes `call: list_messages` — the email adapter's real tool name,
spelled out in the skill. That is the cross-repo string this removes.

Explicit every time, with no "use the name verbatim if it happens to match" fallback. The
fallback would make some names foreign and some local with nothing marking which. Defaults
belong in the UI that writes this file, not in the format.

Three properties fall out:

- **The block is a capability grant.** It is the complete list of what this skill may do to
  that resource; nothing else on the instance is reachable. A better boundary than the file
  split in [20260831](20260831-binding-resource-instances.md) B, and enforced rather than
  conventional. Also answers "every tool becomes public API" — an adapter's surface is
  opt-in per install. (Later: this is true of any explicit binding, not just this shape.
  See "An allow list buys nothing" below.)
- **Evolution is loud.** A skill update that adds a call fails until the mapping is added;
  an adapter rename shows up as a bind-time diff naming both sides. That is the answer to
  "renames go quiet", not a mitigation of it.
- **Nothing is declared, so nothing versions.** Core derives the expected shape from the
  graph's tool nodes and `$.` references. There is no requirement artifact for an adapter
  bump to break.

### The check

Structural, at bind time, against the one instance named — a lint, not a gate. A mismatch
explains itself and never makes a skill unloadable, the same instinct that rejected
`requires`. This keeps the variance rules off the critical path: args contravariant,
results covariant, sloppiness costs a worse diagnostic rather than a broken install.

```
email-digest.notify → discord-alerts
  send → post_message   ✓
  fetch → list_messages ✗ no such tool on test/discord/channel
```

**E survives, demoted.** A free-form trait tag becomes purely a search-and-suggest
affordance for a UI — "these three instances look postable" — with no role in
verification. Worth keeping in mind, not worth building yet.

## How the ecosystem splits this

All of these have the same two tiers. They differ only in where the foreign name is
written.

| System | Adapter | Skill | Foreign name is in | Cost |
|---|---|---|---|---|
| Terraform | provider | module | the module block: `source`, `providers` | provider schema is a hard contract; bumps break modules |
| Home Assistant | integration | blueprint | the automation, as `!input` slots | core owns the vocabulary — the gatekeeper we rejected, and it works |
| Ansible | collection module | playbook, role | the play, as FQCN | no abstraction; every play welded to a vendor |
| Node-RED | node type | flow | config nodes, re-bound on import | unbound config node = loud prompt on import |
| dbt | dbt-postgres etc. | model, package | `sources.yml`: local name → real table | blessed interface, `adapter.dispatch` to escape it; drift is the pain |
| Steampipe | plugin (tables) | mod (queries) | the queries | our status quo; mods are unportable |
| MCP | server | — | client config, per server | no port vocabulary; matching punted to the model |
| Import maps, flake inputs | module, flake | page, flake | the map | closest thing to the shape above |
| Android | — | app | manifest, now runtime grants | the industry moved off declare-ahead, for our reason |

Two clusters:

- **Nominal contract** — Terraform, HA, dbt, LSP, JDBC. Someone owns the vocabulary. You
  get substitution by search. You get a gatekeeper and endless drift.
- **Local alias, bound per install** — import maps, flake inputs, dbt sources, Node-RED. No
  vocabulary, no gatekeeper, no search. Retarget by editing one map.

The second is what "search is not needed" argues for, so we are in company.

One gap. Cluster 2 aliases whole **instances**. Nobody aliases every **method**. dbt
sources is the closest and it is per-object, not per-verb. See the last open question.

## Open questions

### Multi-instance slots — resolved

The old question bundled two cases:

- **Mixed slot** — `fetch` from gmail, `send` from discord, one slot. Banned: a slot *is* a
  resource. A skill wanting two sends declares two slots.
- **Fan-out** — one slot, N instances of the same type, same call run over each. Fine. Same
  type means the same tool names, so one mapping covers all of them. HA's `target:` does
  this.

Rule: **one slot = one type, one or many instances of it.** The mapping is unchanged.

### One skill has three names — resolved

Today `digest.yaml` is called three things, and nothing ties them together:

| Name | Where | Value | Used for |
|---|---|---|---|
| filename stem | `skills/digest.yaml` | `digest` | builds the ref, `extensions.py:129` `member(path.stem)` |
| `skill:` field | in the yaml | `email-digest` | keys `[skills.*.bindings]`, `config.py:172` |
| `version:` | in the yaml | `1` | nothing; the extension carries its own |

You run `test/email@1/digest` and bind `[skills.email-digest]`. Renaming either one unbinds
the skill quietly. 20260831 spots this and leaves it open.

**Decided: both fields are deleted.** Not deprecated, not parsed-and-ignored — removed from
the loader and from the three yaml files in the repo. This is an MVP with no installed base;
compatibility with our own week-old documents is not worth a code path. Identity is where the
file sits (the ref) plus what the install calls this copy (`name`, under "Skill
instantiation"). A name the artifact claims about itself that nothing looks it up by is the
spare one.

Versioning goes to git — a skill repo has tags and commits, which is a better system than a
hand-maintained integer, and one we get for free.

**Nothing needs a version at invoke time.** One copy of a skill is installed; updating it is a
`git pull`, not a second ref. Nothing else in the thin slice reads a version either: the
record just names the ref. Versioning the run properly is a real question and a later one —
parked in "Later" at the bottom.

Filename-as-identity is only safe *because* instantiation exists. On its own, renaming a file
silently unbinds. With `[[skill]]`, the install owns the run target and `source` carries the
ref, so an upstream rename surfaces as an unresolvable `source` — loud, which was the point.
The two are one change, not two.

The one field that survives is `description:`. With `skill:` gone it is the only human-facing
label, and install listings and a model choosing among skills both read it.

### Where things live — decided

`extensions/` goes, and nothing replaces it as an umbrella. Two peers under the install root,
one per noun, both `<root>/<namespace>/<name>/`:

```
.lila/
  config.toml
  adapters/
    test/email/
      code/mailbox.py        # not an adapter without code/
  skills/
    test/                    # namespace — one repo, one clone
      email-digest/
        skill.yaml
        prompts/digest.md
      shared/
        style.md             # not a skill: no skill.yaml in it
```

`skills/test/email-digest/skill.yaml` is `test/email-digest`; `adapters/test/email/` publishes
`test/email/imap`. **A directory is a skill iff it holds `skill.yaml`, an adapter iff it holds
`code/`.** That one rule per tree makes `shared/` unambiguous with no reserved name, and lets
a skill carry attachments (prompts, fixtures, a README) instead of inlining everything.

`skill.yaml` is canonical and `skill.yml` an alias; both in one directory is an error. The old
`digest.yaml`/`digest.yml` collision was only dangerous because the *stem* was the identity and
the two overwrote each other in a dict — with the directory as identity it is local and
detectable.

**Not grouped under a resurrected `extensions/`.** Adapters and skills really are both
installed things, so an umbrella is defensible; the objection is only that the word is retired
("two nouns, no third") and a path segment is the worst place to keep a dead noun alive, since
every user sees it. At two entries the grouping level buys nothing to pay for that. Thin
grounds — if the umbrella is wanted later it is one directory, and the name should be a fresh
word rather than `extensions` with new semantics.

**No manifest, either tree.** `lila.toml` had three fields and all three are gone: `requires`
was rejected above, `version` went to git, and `name` is the path. Its remaining job — being
the marker `discover()` finds (`extensions.py:91`) — is what `code/` and `skill.yaml` do now.
What a manifest costs us is the install record: today `.lila/extensions/test-email/` is one
clone you can update or remove as a unit. The fix is the install recording the clone (source
URL, commit), not a manifest; that is strictly better provenance than an integer somebody
maintains by hand. npm and Nix both landed there.

The likely reason a manifest comes back is an adapter's **Python dependencies** — see "Later".

**Every ref loses `@n`, not just skills.** The version in `test/email@1/digest` was the
extension's, and under this layout there is no extension. A *type* ref keeps its third segment
and drops the integer for a separate reason: nothing reads it. It appears in the registry key
and in `type =` on a `[resources.*]` block, and `Registry.bind` (`resources.py:96`) compares
type refs for **equality**, which behaves identically without a version. No constraint is ever
declared against it — `requires` is gone, and skills stop naming types at all under the mapping
decision. The one thing it enables is two versions of one adapter installed side by side, which
we want no more than we want it for skills.

| Ref | Shape | Versioned by |
|---|---|---|
| type | `test/email/imap` | git |
| skill | `test/email-digest` | git |

One grammar: `<namespace>/<name>` for a skill, `<namespace>/<adapter>/<type>` for a type.
Nothing carries `@`.

**Shared assets resolve within the namespace, never above it.** `test/email-digest` may read
`../shared/style.md`; nothing may escape `test/`. This makes the namespace, not the skill
directory, the self-contained unit — which agrees with git versioning, since the thing a repo
clones to *is* the namespace directory.

Reach for `../shared/` for fragments — a house style block, a format spec. When several skills
share a whole prompt, that is usually a shared subroutine, and `skill.run` by `ref:` already
covers it.

**Local unpublished skills need no mechanism.** They are a directory in the same tree under
whatever namespace the user likes (`local/`). This is what the "Where a local, unpublished
skill lives" section above was asking for; it falls out.

**An adapter's demo skill moves into the tree** under its own namespace: `test/discord-notify`,
not `test/discord@1/notify`. If adapter manifests kept registering skills too, there would be
two ref shapes for one kind of thing and `@1` would come straight back.

### Skill instantiation — resolved, and it replaces the binding key

Terraform, HA and Node-RED all key the binding on a name the *install* owns, never one the
shipped artifact owns. So invert the block. The config instantiates a skill instead of
annotating one.

```toml
[[skill]]
name   = "morning-digest"        # install owns this — the run target
source = "test/email-digest"     # the ref; no @version, see "Where skills live"
enabled = true

resources.inbox  = { instance = "gmail-personal", tools = { fetch = "list_messages" } }
resources.notify = { instance = "discord-alerts", tools = { send = "post_message" } }
```

The bindings sit *inside* the array element as dotted keys, under a `resources` table. One
resource per line is the default; a resource with enough tool mappings to overrun the line
expands to one dotted key per mapping, which parses the same.
This landed as `[skill.<resource>]` sub-tables and is being changed — see the follow-up
under task 2 for why.

What it buys:

- Renames stop unbinding ([20260831](20260831-binding-resource-instances.md)'s open
  question). The key is ours; `source` carries the ref.
- Two instantiations of one skill, bound differently. Every system above allows this. Today
  we cannot say it.
- Somewhere to put install-level facts that belong to neither side. `enabled` is the first —
  turning a skill off by hand has no home today.
- The block growing where skills are useful stops being tedium. Skills are templates, the
  install stamps out copies. HA made the same move with blueprints.

### Nested graphs — mostly resolved

First, two things we call one:

| | Is | Gets |
|---|---|---|
| inline `graph:` under `skill.run` | a private subroutine | the parent's instance *and* mapping; declares names, not type refs ([20260831](20260831-binding-resource-instances.md) E, and task 3) |
| `skill.run` by `ref:` | a separate file, own author, own names | its own block |

`digest.yaml`'s `summaries` node is row one. Its re-declared
`resources: inbox: test/email@1/imap` is the line E deletes. Row two does not exist in the
repo yet.

Inheriting in row two would quietly make the child's local names not local, which is the one
thing the design rests on. So the child needs its own block.

**Decided: a child skill is a call, not a slot.** The parent writes
`ref: test/email-summarize`, and the child's block hangs off the parent's, addressed by
node. The alternative — the child as a slot the install binds, like a resource — is more
consistent and makes children swappable, but every one-off child then needs its own
top-level entry. Too much for now, and swapping a subgraph is a want nobody has stated. The
coupling is real: the parent's author picked that child, unlike an adapter, which the parent
must never name. Revisit if a case turns up.

**Still open: how the adapter binding reaches a subgraph.** The parent binds `inbox` to
`gmail-personal`. The child uses `inbox` too. Two readings:

| | Child's block holds | Means |
|---|---|---|
| Instance flows down | call names only | the node's `resources: {inbox: inbox}` passes the instance; the child cannot read a different mailbox |
| Child binds its own | `instance` and call names | a child can be pointed somewhere else; more to write, and two places name a mailbox |

Inline subgraphs are settled either way — they inherit both. This only bites for the
`ref:` case. Unresolved.

### Dynamic skills and bind time — resolved

**Ask, and record.** The block is a capability grant, so an unmapped call is just a request
for one: "the graph wants `send` on `notify`; `discord-alerts` offers `post_message` —
allow?" The answer is written back. Trust on first use.

Ad-hoc and dynamic skills then use the same path as static ones, which was the goal.
Android and iOS moved from install-time manifests to runtime grants for the same reason;
MCP has elicitation.

### An allow list buys nothing for static graphs — resolved

Earlier framing: the block does two jobs, renaming and authorizing, and everyone else keeps
them apart. Wrong here. **Naming a call is granting it.** A static graph lists every call it
makes, so whichever shape we pick — `send = "post_message"` or a bare
`call: post_message` — the grant is exactly what got named. An `allow` list beside either
one is a restatement.

So the grant argument is neutral between the two shapes. It does not favor the mapping, and
"the block is a capability grant" (above) is a property of *any* explicit binding, not of
this one. The fork is only ever: **who owns the name in the skill file.**

An allow list earns its place in exactly one case — when the caller is not the graph file:

- **dynamic and ad-hoc skills**, where nodes appear mid-run
- an **agent node**, if we add one, picking tools off the instance itself

Those have nothing named ahead of time, so the list is the only grant there is. That is the
same mechanism as "ask, and record" above: the answer to a prompt is a line appended to the
list. Not needed until dynamic skills are.

### Who owns the name in the skill file — closed: mapping

What was left of the fork, with the grant argument removed:

| | Skill file says | Block says |
|---|---|---|
| **Mapping** (decided) | `call: send` | `send = "post_message"` |
| **Verbatim** | `call: post_message` | `instance` only |

We rejected verbatim for leaving some names foreign and some local with nothing marking
which. Fair against a *fallback*; this is not one — every name is the adapter's, always.
Equally consistent, just the other way, and the way everyone else went. Its cost is that
foreign names come back into the skill file; mapping's is a line per call, growing fastest
where skills are most useful.

**What closes it is who can fix a rename.** Both shapes catch an adapter renaming a tool — the
bind-time lint diffs against the instance either way. They differ in what the fix touches.
Under verbatim the wrong string is in the *skill file*, which belongs to the skill's publisher:
the user edits someone else's repo, or forks it, or waits. Under mapping it is one line in
their own config. With skills and adapters on separate git repos updating independently, that
is the difference between a `git pull` you can absorb and one you cannot.

Mapping. The per-call line is the price.

## Relation to other work

[20260831-binding-resource-instances.md](20260831-binding-resource-instances.md) is **not**
made obsolete by this. Checked option by option:

- Its **E** (nested graphs re-declaring type refs) — **confirmed, narrowed**: right for
  inline subgraphs. The referenced-skill case it quietly covered is a different thing. See
  "Nested graphs" above.
- Its **C** ("bind by type when unambiguous") — now **rejected**, on the explicit-binding
  decision above. Inference of any kind belongs in the UI that writes the file, not in the
  format that reads it.
- Its **B** and the secrets split — independent of extension organization; still stand.
- Its open question on the binding key (`[skills.*.bindings]` is keyed by the bare
  `skill:` field, `config.py:172`) — **answered**: neither the bare name nor the ref. The
  install owns the key; the ref moves into `source`. See "Skill instantiation" above.
- Its **D** (`--bind` at the call site) — untouched. Still the saved-default-vs-typed-flag
  question, now against `[[skill]]` instead of `[skills.*]`.
- Every `test/email@1/digest` it writes — **respell**. Skill refs lose `@version` and skills
  leave extension packages; see "Where skills live". Its third open question, whether the
  binding key should be the ref, is answered twice over: not the ref, and not that ref.

Keep both docs; cross-link.

## Tasks

In order. Nothing below is started. Old tasks 1 and 3 merged into task 1 — the local skills
directory is the same tree.

### 1. Two trees, no manifest — done

Landed as designed. Three notes on what the implementation settled:

- `extensions.py` split into `adapters.py` (loading) and `skills.py` (the index, the
  resolver, `asset_path`), over a shared `install.py` holding the two-level scan, segment
  validation, and `InstallError` — which `AdapterError` and `SkillError` both subclass, so
  the CLI catches one thing.
- `Graph.skill`/`.version` became one `Graph.ref`, stamped by `load_graph(path, ref)`.
- Running a skill by **path** resolves back to its ref when that file is an installed
  skill, so both spellings bind alike. A file nothing installed points at keeps the path
  as its identity, which is what `lila check` and the record then show.

What moved, before and after:

```
                                        BEFORE                                   AFTER
install       .lila/extensions/test-email/lila.toml            (deleted — the path is the identity)
              .lila/extensions/test-email/code/imap.py         .lila/adapters/test/email/code/imap.py
              .lila/extensions/test-email/skills/digest.yaml   .lila/skills/test/email-digest/skill.yaml
              .lila/extensions/test-discord/code/discord.py    .lila/adapters/test/discord/code/discord.py
              .lila/extensions/test-discord/skills/notify.yaml .lila/skills/test/discord-notify/skill.yaml

fixtures      tst/fixtures/lila-fixture/lila.toml              (deleted)
              tst/fixtures/lila-fixture/code/mailbox.py        tst/fixtures/adapters/test/fixture/code/mailbox.py
              tst/fixtures/lila-fixture/skills/fetch.yaml      tst/fixtures/skills/test/fetching/skill.yaml

core          lila/extensions.py                               lila/adapters.py + lila/skills.py + lila/install.py
              tst/test_extensions.py                           tst/test_adapters.py + tst/test_skills.py
```

And the refs and keys that respell with it:

| | Before | After |
|---|---|---|
| type ref | `test/email@1/imap` | `test/email/imap` |
| skill ref | `test/email@1/digest` | `test/email-digest` |
| binding key | `[skills.email-digest]` (the `skill:` field) | `[skills."test/email-digest"]` (the ref) |
| inline subgraph | `email-summary` (its own `skill:`) | `test/email-digest#summaries` |

`.lila/adapters/` and `.lila/skills/` are now tracked (`.gitignore`), so the repo carries a
working example of the layout; `config.toml` and `records/` stay local.

`extensions/` splits into `adapters/` and `skills/`, both `<root>/<namespace>/<name>/`, and
`lila.toml` goes. The path becomes the identity in both. This is the load-bearing task:
everything after it writes config keyed by names that do not exist yet.

**Discovery, twice.** Two roots under the install plus their bundled counterparts, scanned the
way `extensions.discover` scans today but one level deeper. Depth is fixed at two; a directory
is an adapter iff it holds `code/`, a skill iff it holds `skill.yaml`.
`registry.skills[f"{ns}/{name}"] = path` — `Registry.skills: dict[SkillRef, FilePath]` is
unchanged, only its keys are.

**`Manifest` dissolves.** `load_manifest` and the whole `lila.toml` parse go
(`extensions.py:57-83`). What survives is `member()`, which becomes a function of the path:
`adapters/test/email/` + `imap` → `test/email/imap`. `install(manifest, registry)` takes a
root and a ref instead. `_import`'s module name (`lila_ext_test_email_mailbox`) derives from
the same two segments.

**`skill.yaml` canonical, `skill.yml` alias.** Try one, fall back to the other; both present
in one directory is an error. `SKILL_SUFFIXES` still goes — the fallback is a two-name lookup,
not a suffix list, and the old last-wins collision cannot arise once the directory is the
identity.

**What the registry is for changes.** It stops being a table of run targets and becomes a
discovery index: it resolves a `source =`, resolves a `ref:` on a subgraph node, and lists
what is available to instantiate. Config says what *runs*; the index says what *exists*.
Collapsing those two is what today's `graph_path` does, and what task 2 undoes.

**Adapters stop registering skills.** Delete the `SKILLS_DIR` branch of `extensions.install`
(`extensions.py:127-131`) and both constants. An adapter registers types, tools and pure
tools; nothing else. `requires` and its check (`extensions.py:120-122`) go with the manifest.

**Naming.** `extensions.py` is now two things — adapter loading and skill discovery — and
`ExtensionError`/`ExtensionName` name a noun this doc retired. Split and rename with the move;
`ext.py` (the `@resource`/`@tool` decorators) is the authoring API and can keep its name.

**Validate loudly.** Segments are `[a-z0-9-]+` — they end up in a ref that gets parsed, so
`My Digest/` must not get in. Two directories claiming one ref is an error, not last-wins.
A directory at depth two holding neither marker is an error too, not a silent skip.

**Graph identity comes from outside the file.** Delete `skill:` and `version:` from
`parse_graph` (`executor.py:569-572`) and the `Graph` dataclass (`365-366`), and from the
three documents in the repo. `load_graph` takes the ref it was found under and stamps it.
An inline subgraph has no file, so its identity is `<parent-ref>#<node-id>` —
`digest.yaml`'s child becomes `test/email-digest#summaries` instead of `email-summary`. That
identity does exactly two jobs: the nested run record, and naming the subgraph in an error.

**Run record.** `RunRecord.skill`/`.version` (`executor.py:815`, set at `973`) become the
source ref, and nothing more for now; task 2 adds the instantiation name. `commands.py:157`
and `check_command`'s `{graph.skill}@{graph.version}` print the ref. Hashes and the rest of
the environment are deferred — see "Later".

**Ref grammar.** No ref carries `@`. Skills are `<ns>/<name>`, types `<ns>/<adapter>/<type>`.
`SkillRef`'s docstring (`resources.py:22`) says `publisher/extension@version/member` and is
wrong on both counts; `TypeRef` in `ext.py` likewise. `_load_skill_run`'s "must be a
name@version or a path" (`executor.py:483`) updates with them. Every `type =` in
`config.example.toml` and the fixtures loses its `@1`. `resolve_skill_path` stays as the
path-based fallback for tests.

**Namespace-scoped asset paths.** A skill referencing `../shared/style.md` resolves relative to
its own directory and must not escape the namespace root. Nothing reads external assets yet, so
this can land as the resolver plus a check, with the first consumer (a `prompt_file:` on an llm
node?) deferred.

**Refactor, not migrate.** No installed base, so nothing is kept for compatibility. What
moved is recorded above, under "done".

Drop `skill:` and `version:` from all three documents, including `digest.yaml`'s inline child,
and `@1` from every ref in them. Tests asserting `test/fixture@1/fetch` or loading a manifest
change shape — `test_extensions.py` most of all.

### 2. Skill instantiation — done

Landed as designed, and the follow-up below has since landed too: `SKILL_FIELDS` is gone,
`_bindings` reads `raw["resources"]`, and `config.example.toml`, `.lila/config.toml` and the
test fixtures spell bindings as dotted keys. Three notes on what the implementation settled:

- **Bindings are sub-tables, not a `bindings` table.** `[skill.inbox]` with `instance =` in
  it, which is the shape task 4 needs anyway; `SKILL_FIELDS` (`name`, `source`, `enabled`)
  reserves the block's own keys and every other key is a resource. A scalar where a
  sub-table belongs fails as "must be a table". The sub-table *shape* survives the
  follow-up; where it hangs and how it is spelled do not.
- **A ref resolves to its instantiation, not around it.** `config.instantiation(config, ref)`
  finds the single `[[skill]]` naming it as `source`, so `lila run test/email-digest` still
  works. None is the loud error the open question leaned to; two is ambiguous and names both.
  A `source` nothing installed is the same error, which is what an upstream rename looks like.
- **`RunRecord.name`** holds the instantiation, passed as `run(..., name=)`. A nested run has
  none — only the top of a run is something the install named.

**Follow-up: bindings move inside the block as `resources.<name>` dotted keys.** Landed.
`[skill.inbox]` has two problems, and only the first was noticed at first.

*It does not read as a resource.* Nothing in `[skill.inbox]` marks `inbox` as the skill's
own name for a resource rather than another field of the `[[skill]]` block — it sits at the
same depth as `name` and `source`, and only the reserved-key list separates them. The reader
has to already know the rule to parse the file, which is the same failure as a name that
means one thing to the writer and another to the reader. It is also load-bearing in the
wrong direction: an author who writes `[skill.enabled]` meaning a resource called `enabled`
gets a confusing error, and any field added to a `[[skill]]` block later silently steals a
resource name. Task 4 makes this worse — those tables grow call mappings, so they get longer
and look less like fields.

*It attaches positionally.* This is the worse one. A sub-table after `[[skill]]` binds to
whichever array element came last, and nothing in the block says which. Move a resource
table, or paste a new `[[skill]]` above it, and it silently rebinds to a different skill —
the exact quiet unbinding that `[[skill]]` was introduced to kill
([20260831](20260831-binding-resource-instances.md)'s open question). Every spelling that
keeps a separate `[skill.…]` header inherits this, including `[skill.resources.inbox]`.

Dotted keys inside the array element fix both, because there is no second header to
misplace. One line per resource, an inline table each — this is the default:

```toml
[[skill]]
name    = "morning-digest"
source  = "test/email-digest"
enabled = true
resources.inbox  = { instance = "gmail-personal", tools = { fetch = "list_messages" } }
resources.notify = { instance = "discord-alerts", tools = { send = "post_message" } }
```

A TOML inline table cannot span lines, so a resource with three or four mappings runs past
100 characters with nowhere to break. Expand that one — same keys, one per line:

```toml
[[skill]]
name    = "morning-digest"
source  = "test/email-digest"
enabled = true
resources.inbox.instance      = "gmail-personal"
resources.inbox.tools.fetch   = "list_messages"
resources.inbox.tools.archive = "move_message"
resources.inbox.tools.flag    = "set_flag"
resources.notify = { instance = "discord-alerts", tools = { send = "post_message" } }
```

Both parse to the same dict, and the two forms mix freely *across* resources — but never
within one; see the caveats below. Inline is the default because it makes the resource the
unit you scan for, and because `config.example.toml` already spells small sub-tables that
way (`secrets = { password = "LILA_INBOX_PASSWORD" }`).

Either way it parses to `{"name": …, "resources": {"inbox": {"instance": …, "tools": {…}}}}`.
The whole
instantiation is one contiguous block that cannot drift from its owner, `resources` mirrors
the skill file's own `resources:` block where the local names come from, and `name` and
`source` can never collide with a resource name. `SKILL_FIELDS` (`config.py:40`) is deleted
and `_bindings` (`config.py:165`) stops scanning siblings — it reads `raw["resources"]`.

`tools` is a nested table rather than bare pairs beside `instance` for the same reason one
level down: with `instance = "…"` and `fetch = "list_messages"` in one table, a tool named
`instance` is unspellable and the reserved-key problem is simply recreated inside the
resource. Task 4 writes `resources.<name>.tools.<local> = "<tool>"`.

Options that lost:

| Spelling | Reads as | Why not |
|---|---|---|
| `[skill.resources.inbox]` | a resource, unambiguously | still positional; one more segment |
| `[skill.bind.inbox]` | the verb, and matches "bind time" | still positional; invents a word |
| `[skills.<name>.resources.inbox]` | a resource, owner named in the header | fixes both, but abandons `[[skill]]` for a name-keyed table and the headers get long |
| `bindings = { inbox = { instance = … } }` | inline, one place | unreadable once task 4 adds call pairs |

Two things to write down where users see them:

- **Do not mix the spellings for one resource.** `resources.inbox = { … }` followed by
  `resources.inbox.tools.fetch = …` is a TOML redefinition error, and the message will not
  mention skills. This is the likelier mistake precisely because both forms are expected —
  the natural way to add a fourth mapping to an inline resource is to append a dotted line.
  One line in `config.example.toml`'s skills comment.
- **Dotted keys are the less-travelled corner of TOML.** Someone who has only read
  `[section]` headers may not recognize the form. That file is heavily commented already, so
  it is cheap to cover; while there, show both spellings and a two-resource skill, which is
  the motivating case and is currently not shown.

**Edited:** `SKILL_FIELDS` (deleted) and `_bindings` in `config.py`, the `[[skill]]` blocks in
`config.example.toml` and `.lila/config.toml`, and the `test_config`/`test_commands` fixtures.
`config.example.toml` carries both caveats and a commented two-resource instantiation; `tools`
is not parsed yet — task 4 adds it.

What landed, for the record: `[[skill]]` with an install-owned `name`, replacing
`[skills.*.bindings]` keyed by the graph's own `skill:` field. Shape is under "Skill
instantiation" above.

**Config.** `SkillConfig` (`config.py:63`) becomes an instantiation: `name`, `source` (the
ref), `enabled`, `bindings`. `InstallConfig.skills` stays a dict keyed by `name`, but `name` is
now the install's. `parse_config`'s skills loop (`config.py:171-179`) reads an array of tables
from `raw.get("skill", [])` rather than a `[skills.*]` table, and errors on a duplicate `name`.

**Binding lookup stops going through the graph.** `skill_bindings(config, graph.skill, ...)`
(`config.py:337`, called from `commands.py:141`) takes the instantiation name. Same at
`commands.py:93`, where `load_checked` looks up `config.skills.get(graph.skill)` to find
bindings for the check.

**Run target resolution.** `graph_path` (`commands.py:100`) tries a file, then an installed
ref. It gains a first step: an instantiation name. Order matters — instantiation names win,
since that is what the user named.

*Resolved:* `lila run test/email-digest` on a ref that is installed but never instantiated
errors — loud, as leaned. A file path is the same case: it resolves to its ref, then to the
instantiation of that ref.

**`enabled` is inert on arrival.** Nothing runs skills on its own yet — the scheduler is
punted — so it parses, prints in listings, and gates nothing. Worth landing anyway as the
place install-level facts go.

### 3. Drop re-declared resources in inline subgraphs — done

20260831 E. Small and narrow, independent of 1 and 2.

An inline `graph:` gets the parent's instances through the node's `resources:` mapping, so its
own `resources:` block re-declares a type ref that is already settled — `digest.yaml`'s child
saying `inbox: test/email@1/imap` is the line to delete. Make `parse_graph` reject `resources:`
on an inline graph (it is reachable from `_load_skill_run`, `executor.py:477`, so the parse
knows which case it is in), and confirm `skill_run_handler` (`executor.py:1082`) passes the
mapped instances down without consulting the child's declarations.

Doing this before 5 matters: a re-declared type ref in a child is exactly the foreign string
the tool mapping is meant to remove.

**What landed.** The child keeps saying what it needs, and stops saying what type it is:

```yaml
- id: summaries
  type: skill.run
  resources: { inbox: inbox }   # child's name : parent's name — the instance, and the grant
  graph:
    resources: [inbox]          # what the child needs, in its own names
```

`parse_graph` takes an `inline: bool`, set only where `_load_skill_run` parses a `graph:`. It
picks the form: a mapping to type refs at the top level, a list of bare names inline, and each
form is an error where the other belongs. `Graph.resources` becomes
`dict[ResourceName, TypeRef | None]`, `None` being "named here, typed by the parent".

The list is checked against the node: `_load_skill_run` errors unless the child's names are
exactly the ones the node maps. So a resource the child needs and the node forgot is a load
error naming both sides, not an unbound-resource failure mid-run.

Two readers of the type ref had to tolerate a `None`. `bind_resources` skips the type check
for an inline child — the parent already made it against the same instance — but still requires
every declared name to arrive, which is what the load-time check guarantees. `verification`'s
`_check_tools` splits "not declared" (still an issue) from "declared without a type" (nothing
to check without the parent's context), and `_check_resources` needs nothing: it runs against
bindings only at the top level. `skill_run_handler` needed no change at all; it never read the
child's declarations.

**What did *not* become implicit.** The child reaches nothing the node did not map, by name,
one line per resource — a child handed `inbox` and not `outbox` cannot touch the outbox its
parent holds. Only the type ref went away, and the node's mapping had already settled that. An
"inherit everything" default, or a `resources: *`, would buy back a line at the cost of the
isolation and of [TENETS.md](../docs/TENETS.md)'s explicit-over-implicit — not a trade to make.

### 4. Tool mapping and the bind-time check

The `send = "post_message"` block, and the lint that diffs it against the instance.

**Config shape.** An instantiation's per-resource table holds `instance` plus call-name pairs,
so `bindings: dict[ResourceName, InstanceName]` becomes
`dict[ResourceName, tuple[InstanceName, dict[LocalName, ToolName]]]` — probably its own small
dataclass rather than a tuple.

**Resolution.** `Registry.bind` (`resources.py:96`) returns instances today; it grows the call
map, and tool-node resolution translates `call:` through it before `registry.tool(type_ref,
call)`. An unmapped call is an error naming both sides, and later the prompt from "Dynamic
skills and bind time".

**The lint.** In `verification.check`, structural against the one instance named: every call a
graph makes is mapped, every mapping names a real tool on that type, args contravariant,
results covariant. Diagnostics, never a load failure — same instinct that rejected `requires`.
Format is under "The check" above.

*Open before this lands:* how a `ref:` subgraph's binding reaches it — see "Nested graphs".
Mapping vs verbatim is closed (mapping).

## Later

Out of scope for the thin slice. Recorded so the reasoning is not re-derived.

### Versioning what a run actually used

Not needed now — the record names the ref and stops there. The question when it comes up:

A hash of `skill.yaml` versions the graph and nothing it calls. The generalization is **one
identity per thing that can change independently**, and a tool is not one of those — it ships
inside an adapter's `code/`, so the adapter is the smallest unit that moves on its own.

| What varies | Identity | Cost when we do it |
|---|---|---|
| the graph | sha256 of `skill.yaml` | free — already read |
| each bound adapter | content hash of its `code/`, commit later | stamp once at load |
| **the binding** | the resolved block, copied in | free — already parsed |
| the model | alias, model id, backend, `context_length` | free — `ModelConfig` |

The binding is the one worth noticing. It is not a hash, and it is the change most likely to
happen: remap `send` to a different tool and the same graph on the same adapter behaves
differently. `ModelConfig` is the same — a `context_length` change moves outputs and nothing
else would show it.

Adapters would get the two-tier treatment skills get: content hash, because it catches a
locally edited adapter, and the commit later from the install record, because it says where
the thing came from. Computed once at load, so a run pays nothing. Deliberately not
`git rev-parse` per run — a `local/` skill has no commit and a dirty tree has one that lies.

Nothing in a ref helps here. The `@n` that used to sit in `test/discord@1/channel` was a
compatibility declaration nobody declared against, which is why it is gone (see "Where things
live"). A build identity is a different question from a contract, and the record is the only
thing that ever asked it.

**Cap it there.** This is not replay and should not grow into one: the IMAP server on the
other end has no version, the model is nondeterministic, and hashing `code/` says nothing
about an adapter's Python dependencies. It answers *what differed between two runs, among the
things we control*. `RunRecord.stub_set` (`executor.py:822`) is the replay mechanism and
already exists. No dependency capture, no lockfile.

**Where it would go.** `RunRecord.backend_version` (`executor.py:819`, "what produced the
outputs, for replay") is already this idea, stamped from `RunContext` and never populated.
Widen that into an environment block rather than adding four fields to `RunRecord`.

### Adapter Python dependencies

An adapter's `code/*.py` is imported straight into the harness process (`_import`,
`exec_module`) and nothing declares what it imports. `httpx` or `imapclient` works only if the
harness already has it. Nothing regresses by deleting `lila.toml` — there was no mechanism
before — but this is the thing most likely to want a per-adapter file back.

When it does, the artifact is a **`pyproject.toml`**, not a revived `lila.toml`: it is a real
Python distribution question (a resolver, a lock, probably a venv or an install step per
adapter), and inventing our own dependency syntax beside a standard one would be the worst of
both. Note that a `pyproject.toml` would also become the adapter's marker, replacing "holds
`code/`" — worth remembering so the marker rule is cheap to change.

### Also parked

- **Trait tags** as a search-and-suggest affordance — see "The check". No role in verification.
- **Allow lists** for dynamic and ad-hoc skills, and an agent node — see "An allow list buys
  nothing for static graphs". Not needed until those exist.
- **Namespace-scoped asset paths** have no consumer yet; the first is probably a `prompt_file:`
  on an llm node. Task 1 lands the resolver, not a user of it.
