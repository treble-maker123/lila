# Binding Resource Instances

**Status**: on-going — problem stated, options sketched, nothing decided or implemented.

## Goal

Make "which mailbox does this skill read?" answerable in one place. Today it is spread
across a skill file and two sections of `config.toml`, and the answer is confusing to
write and confusing to read back.

Secondary, and possibly a different problem: keep the binding — and the credentials
behind it — out of what an agent editing this repo casually reads and rewrites.

In general, the resource declaration and binding is too confusing.

## The friction

One question, four names, three files:

| Name | Lives in | Example |
|---|---|---|
| Type ref | skill `resources:` | `test/email@1/imap` |
| Resource name (slot) | skill `resources:` | `inbox` |
| Instance name | `[resources.*]` in config | `gmail-personal` |
| Binding | `[skills.*.bindings]` in config | `inbox = "gmail-personal"` |

Reading `digest.yaml` alone tells you the skill needs *some* IMAP mailbox. To learn
*which*, you jump to `config.toml`, find the skill by its `skill:` field (not its
filename — a third naming axis), read the binding, then follow that to a `[resources]`
entry. Three hops for one fact.

Extra sharp edges noticed while writing this:

- The nested `email-summary` graph in `digest.yaml` re-declares
  `resources: inbox: test/email@1/imap`, but its binding comes from the parent's
  `resources: { inbox: inbox }` on the `skill.run` node. So the type ref there is pure
  redeclaration, and reads like a second thing to bind.
- `[skills.<name>.bindings]` is keyed by the skill's `skill:` field. Nothing in the file
  layout enforces that, so a rename silently unbinds.
- `.lila/config.toml` currently has the Gmail app password inline, even though
  `config.example.toml` documents `secrets = { password = "ENV_VAR" }`. Whatever we do
  with bindings, the live config is a file an agent reads and shouldn't.

Worth separating two complaints that got asked as one:

1. **Ergonomic** — too many hops to answer one question.
2. **Boundary** — the model shouldn't be reading or editing this.

A separate file fixes (2) cleanly. It does *not* fix (1) on its own; it just moves a hop.

## Options

### A — Status quo

Three hops, one file. Cheapest. Keep as the baseline to beat.

### B — Split `bindings.toml` out of `config.toml`

`.lila/config.toml` keeps models and resource instances; `.lila/bindings.toml` holds
only `[skills.*]`. Gives a file boundary to point permissions/`.gitignore`/deny-rules at.

Concern: the boundary the model shouldn't cross is really *credentials*, and those are in
`[resources]`, not in the bindings. Splitting bindings out puts the wall in the wrong
place. If the goal is (2), the split to make is **secrets out**, not **bindings out**.

### C — Convention: bind by type when unambiguous

A skill declaring `inbox: test/email@1/imap` binds automatically if exactly one configured
instance has that type. `[skills.*.bindings]` becomes an override, needed only when there
are two mailboxes or the intent isn't obvious.

Kills the binding table for the common single-account case entirely — the file that reads
today as ceremony disappears. Cost: a second instance of a type silently changes whether
a skill resolves, so the error at that moment has to be excellent ("two instances of
`test/email@1/imap`: gmail-personal, work — bind `inbox` explicitly").

### D — Bind at the call site

`lila run test/email@1/digest --bind inbox=gmail-personal`, with the config section as the
saved default for repeat runs. Makes the binding visible exactly where the run is
requested. Cost: the interesting case (scheduler, Discord trigger) has no human typing a
command, so it falls back to the config table anyway.

### E — Drop the type ref from nested/inline graphs

An inline `graph:` under `skill.run` inherits declared resources from the node's
`resources:` mapping; re-declaring the type is an error, not a requirement. Small, narrow,
independent of A–D. Probably do this regardless.

## Leaning

C + E for (1), and a secrets split rather than B for (2). C is the only option that
removes a hop instead of relocating one, and E removes the duplicate declaration that made
`digest.yaml` read as if it had two things to bind.

## Open questions

- Does C's implicit binding survive the multi-account case in practice, or does every real
  install end up writing the explicit table anyway — in which case it bought nothing?
- Is "the model shouldn't read this" a file-layout problem or a permissions/deny-rule
  problem? If the latter, no file moves at all.
- Should the binding key be the skill's ref (`test/email@1/digest`) rather than its bare
  `skill:` name, so renames and two skills sharing a name can't collide?
