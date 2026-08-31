# Workflow Executor Thin-Slice

**Status**: on-going — T1–T10 implemented and unit-tested. Remaining: proof against a real
inbox, a real Discord post (the config holds a placeholder token), and T11 (scheduler). P10 failure semantics is designed
and deferred to the TODOs, as is stubs/replay. P1–P7 unreviewed; P8/P9 agreed but unimplemented, so
the code still uses the pre-P8 shape (`mailbox@1`, `uses:`/`call:`).

## Goal

A thin-slice executor that runs a workflow expressed as a graph, proven by an e-mail
organization workflow as its first consumer.

The executor is the deliverable. The e-mail workflow is the forcing function: it has to be
re-shapeable without touching the executor, so the workflow itself stays something to
experiment with.

**In**: node types, edge routing, typed state, resources, run record, reading a mailbox and
moving a message between folders.
**Out**: sending mail, conversation/`wait`, retries, parallel, persistence.
Proof is a real (isolated) Gmail inbox, organized correctly, repeatably.

## Terminology

| Term | Means |
|---|---|
| Graph | the one structural noun: nodes + edges + metadata in a flat file |
| Skill | a graph that is published — identity, version, declared resources, invocable by name |
| Node | one unit of work: a model call, a tool call, or a nested run |
| Edge | a directed link carrying an optional `when:` predicate |
| Run | one execution of a graph, with its own memory and record |
| Run memory | the working-memory tier for a run, addressed `$.path`, append-only per node |
| Resource type | a kind of credentialed handle (mailbox, DB, browser) — config shape + session, no operations |
| Instance | one configured resource in `config.toml` (`gmail-personal`) |
| Resource name | the skill's own name for a resource it needs (`inbox`), declared in `resources:` |
| Binding | install-time mapping of a resource name to an instance |
| Tool | one named operation over a resource type: arg/result schema + impl |
| Extension | the unit of distribution: resource types, skills, or both, versioned as one |
| Record | the per-run log of nodes, edges, and resources used |

**Addressing.** `publisher/extension@version/member` — `lila/email@1/imap`,
`lila/email@1/digest`. The pin sits on the extension because an extension versions as one
unit; members move together.

**`$.` is memory and only memory** (`$.input`, node outputs). A resource is never a value,
so it is named in `resource:`, never after a `$.`.

`skill.run` and `graph.run` are two spellings of one node type: `ref:` takes a published
ref or a local path and the executor treats both the same. `skill.run` is canonical in
normalized output and error messages.

## Proposals

### P1 — Node types

| Type | Does | Config |
|---|---|---|
| `llm` | one model call, constrained output | prompt template, output schema, optional tool grants |
| `tool` | one tool call on a declared resource | `resource:`, `call:`, `args:` |
| `skill.run` | runs another graph | `ref:` or inline `graph:`, `input:`, `resources:` |

**One tool type, not a family.** Transport lives inside the tool's implementation, so the
executor's work is identical every time: resolve `resource:` to an instance, look up
`call:` in that resource type's tools, validate args, call, validate the result. An HTTP
call is a tool over an `http` resource; an MCP call is a tool over an MCP server resource,
whose `tools/list` snapshot registers as its tools; a pure transform is a tool over a
stateless resource (P8). Deferred: `wait`, `parallel`, `code.execute`.

### P2 — Routing on edges

No condition node. Each edge carries a predicate over memory; the executor takes the first
match and records the edge with the evaluated inputs. Branch history is reconstructable
from edges alone.

**Edges are evaluated top-down, so file order is semantics.** An unguarded edge acts as the
fallback; a `when: true` edge listed early makes every edge under it dead. Static check
flags unreachable edges.

### P3 — Run memory and schemas

Run memory is one JSON-serializable bag per run, addressed `$.path.to.value` — the
working-memory tier of the hierarchy below, not a separate mechanism. It differs from the
longer-lived tiers in *access discipline*: written only as validated node output, read only
by exact path. Nothing is model-initiated or semantic, which is what keeps a run replayable.

Every node has an input and output JSON Schema — declared for `llm`, derived from the tool
for `tool` — and outputs validate on write, so a bad node fails at its own boundary. An
`llm` node's output schema is the same artifact that constrains decoding. The graph declares
its own input/output schema, which is what makes it invocable by name and composable as a
sub-skill.

Author-facing shorthand that compiles to JSON Schema is a later convenience.

### P4 — Resources

A resource is a credentialed handle resolved at run start and injected into a node's
execution context. It never enters graph state, so credentials never reach a prompt, the
run record, or `$.…`. Operations live in tools, not on the resource (P8).

A skill names the resources it needs in a one-line-each `resources:` header, bound to
instances at install. That declaration is load-bearing for three audiences at once, which is
why it stays explicit rather than inferred from call sites:

| Audience | Gets |
|---|---|
| Non-technical user | the skill states which accounts it touches; install is a form |
| Author | a dependency declaration with a type, checked before the run |
| Executor | authority scope — a node reaches only a declared resource, and the record logs which instance |

### P5 — Nested skills

No third noun. Everything is a skill — a graph with identity, version, and declared
resources — and a skill can run another via a `skill.run` node. The stdlib `tool-call` skill
(`llm(choice) → edges → tool → project`) is a skill like any other; it just isn't one a user
would invoke directly.

Nesting is what keeps nodes atomic without losing the pattern. Merging `llm` + `tool` into
one node would hide a bounded mini-loop inside a node the executor cannot see; as a nested
skill it stays visible.

**Recursion** is the executor's property, not a kind of skill (nothing calls itself). The
run unit is recursive from day one — a node handler may itself be a skill run with its own
memory scope and nested record. Free now, a rewrite to retrofit. `parallel` then needs no
new machinery: map over a list, one child run per item, results collected at `$.<node_id>`.

**Isolation rule**: an inner graph cannot reach outer context. Everything crossing the
boundary is explicit — `input:` for memory, `resources:` for resource names — and nothing is
inherited ambiently. The parent reads the child's result at `$.<node_id>` after it returns;
the child never reads up. That makes inputs a real contract rather than an ambient read of
whatever the parent happened to have, and keeps a skill file honest about what it touches.

Paths are uniquely indexed across nesting — node ids unique within a graph, qualified by the
instance (`$.triage.gather.notes`).

If a nested skill needs the trained transcript shape (`assistant(tool_call) → tool →
assistant`), the message list lives in its memory at a declared path, so it stays
inspectable instead of hiding inside a node.

**Open — skill proliferation.** Fine-grained nesting means many small skills, and a large
flat set is hard for a model to select from. Most selection is static, done by the graph at
authoring time, so this only bites where a model chooses — the interaction graph's router.
Directions: exposure (internal skills never enter a selection set), routing in stages, or
retrieval over skill descriptions.

### P6 — Composability of vended skills

A vended skill can't know local instance names, so it declares its own name for each
resource plus a type ref (`inbox: lila/email@1/imap`) and the user binds that name at
install. Two names of the same type is the ordinary case — read from `inbox`, file into
`archive` — so the indirection is not ceremony.

Installing a skill lists its unbound resources and refuses to run until each is bound.
Open: whether a skill may ship a default binding.

### P7 — Run record

Per node: inputs read, output written, resources used (by name), timing, model usage. Per
edge: predicate, evaluated inputs, taken or not. Enough to localize a failure to a node and
replay the run.

### P8 — Tools, resources, and what an extension author writes

Two nouns. A resource holds config, credentials, and session lifecycle and has **no
operations**; a tool is one operation over a resource type.

| Noun | Is | Example |
|---|---|---|
| Resource | config shape + credentials + session, no operations | `gmail-personal : lila/email@1/imap` |
| Tool | name + arg/result schema + impl + the resource type it needs | `get_message` |

The tool handler is four steps with no per-provider branching: `resource:` → the skill's
binding → the instance; `(type, call)` → tool; validate args; call and validate the result.
Arg errors land in one place with a node id attached instead of surfacing as a `KeyError`
from inside a provider, and tool logic tests against a fake resource with no network.

**Everything is derived from Python, nothing declared twice.** An extension author writes a
`@resource` dataclass and `@tool` functions over it; the harness reads the rest off them.

| Derived | From |
|---|---|
| Resource type ref | the extension's `lila.toml` + class name |
| Config/credential shape | the `@resource` dataclass fields; a `Secret` field stays out of graphs and records |
| Call name | the function name |
| Arg schema | the signature — types, defaults, required-ness |
| Result schema | the return annotation |
| Description (for `llm` tool grants) | the docstring |
| Required resource type | the first parameter's annotation |

Cost accepted: a Python signature becomes a compatibility surface — renaming a parameter
breaks graphs — which is what extension versioning is for.

**A stateless extension is a resource with no fields.** That is the transform escape hatch,
and it is why there is no separate local-callable node type: one node shape, one lookup
path, one static check, and `resources:` stays a complete statement of what a skill draws
on. Cost: a first parameter that is always an empty object.

**Type refs are namespaced, not portable.** `lila/email@1/imap` and `acme/gmail-api@1/gmail`
are different types with no coordination between publishers; the ref is a compatibility
check (refuse a browser resource to a mailbox tool), not a capability contract. Swapping
IMAP for the Gmail API means editing the graphs that call those tools, not just rebinding.
If that swap is ever wanted for free it returns as an opt-in adapter — an extension
implementing the same tool names over a different resource — designed then, against a real
second provider.

`lila.ext` is the only module an extension imports from core. Enforcement is convention for
now; a real boundary is the MCP one below.

### P9 — Extensions and dependencies

An extension is the unit of distribution: resource types, skills, or both, versioned as one.
Core owns the decorators, the two registries, and the loader — it never learns what a
mailbox is. Layout is a `lila.toml` manifest (name, version, requires) beside `resources/`,
`skills/`, and `evals/`.

| Shape | Holds | `requires` |
|---|---|---|
| Toolkit | `resources/` + `skills/` | — |
| Standalone skill | `skills/` only | the extensions whose resource types it names |
| Standalone resource | `resources/` only | — |

**Install is `git clone` into `.lila/extensions/`.** The resolver searches there first, then
the bundled set in `src/extensions/`, so a skill file cannot tell which it got. Discovery is
"a directory with a `lila.toml`", not a naming convention.

*One owning extension per resource type, and only it defines tools over that type.*
Extension B declaring tools over A's resource type is the edge that grows into a dependency
graph, inheriting A's initialization, credential format, and versioning as a compatibility
surface. Want extra IMAP tools? Fork, or ship your own imap-shaped resource. Duplication is
right here — a resource type is ~30 lines; a cross-extension contract is forever.

The pypi/npm failure mode needs transitive edges, version *ranges*, and a solver. Drop any
one and it collapses; keeping the two axes apart drops all three.

| Axis | Rule |
|---|---|
| lila refs (skill → resource type, skill → skill) | exact pins, no ranges, resolved at install, fails closed listing what's missing |
| language deps (`imaplib`, Google clients) | in-process extensions are stdlib-only or vendored; anything heavier runs out-of-process as MCP |

No ranges means no solver, ever. The second row is where npm hell actually lives, and is the
real reason the MCP boundary earns its keep: the harness never resolves a Python dependency
graph, it opens a subprocess or a socket. An extension wanting `google-api-python-client` is
an MCP server, full stop. That leaves exactly one cross-extension edge — declared in
`lila.toml`, pinned, checked at install beside the unbound-resource rule (T7).

**Trust.** Loading an extension executes third-party Python in-process, and nothing
sandboxes it. Install is the trust boundary; MCP is the out-of-process answer for code that
shouldn't be trusted in-process.

**Evals live in the extension** (`evals/`), reserved now and designed later — entry points
want standardizing. The shape falls out of work already planned: a run record *is* a stub
set, so a case is stubs plus expected output. A publisher ships a report; the user re-runs it
after install, which is the half that matters, since a report shipped inside an untrusted
repo is a claim, not evidence.

Open: extension versions don't track content, so a mutable clone can drift under a pin. A
content hash written to a lockfile at install would close it; deferred as one more moving
part than the slice needs.

**MCP and selective exposure.** MCP advertises a server's whole tool list, but the model
never speaks MCP here — MCP is a transport the *executor* invokes, and `tools/list` is a
client-side call whose results we choose what to do with.

- *Tool-as-node* (default): the model sees no tool metadata at all. An `llm` node emits a
  choice, edges route to a `tool` node with a fixed `call:`. The subset falls out of the
  graph for free.
- *Tool grants* (the `tools:` list): the executor projects — fetch `tools/list`, filter to
  the node's grants, render only those. A server advertising 40 tools is irrelevant when the
  request carries 3. MCP has no per-caller view and doesn't need one.

Three rules load-bearing in the grant case:

- **Snapshot schemas at install; don't trust live `tools/list`.** Tool names and descriptions
  land in the prompt, so a server changing a description between runs is a prompt-injection
  vector with no user-visible diff. Pin at install, compare on connect, fail closed on drift.
- **The grant list is also the call ACL.** Filter at render *and* reject at call time — a
  model can name a tool that was never rendered.
- **Namespace tool names by resource**, since two servers will collide on names like
  `search`. Falls out for free: a tool is addressed `(resource type, call)` already.

**Untrusted content.** A resource handle never entering memory stops credential leaks, not
injection: `get_message` returns an attacker-controlled body straight into `$.<node>.body`,
headed for an `llm` node. Irreducible for message bodies — any tool reading the outside world
has this property, so treat it as a standing property to track, not a bug to close. Later
work: provenance-tagging untrusted values in memory so the record shows what reached a
prompt, and sanitization at the tool boundary. Out of scope for the slice.

### P10 — Failure, and how it reaches a user

**Every message addressed to a user is authored by a graph.** The harness transports it. It
never writes it. That is the transparency goal applied to failure.

Two things follow. A failure that needs a user's attention must be caught by a graph. And
there is no harness-side notifier — one would post to a channel no `resources:` block
declared, which is the ambient authority this design exists to remove.

The inverse does not hold. A graph is not only user interaction.

`lila run` printing to a terminal is scaffolding. It gets discarded, and nothing is designed
around it.

**Catch is skill-level, not node-level.** Per-node catch edges were dropped. Error handling is
targeted, so per-node predicates smear it across the file.

A graph declares one `catch:` node beside `entry:`. Any uncaught node error routes there.

Discrimination does not disappear, it relocates. Inside the handler, ordinary `when:` edges
route on `$.error.type`, top-down like every other edge. Same expressiveness, one concept, one
place to read.

`$.error` binds only inside the catch subgraph. Same scoping rule as `$.each` in a map node;
the static check rejects it elsewhere.

**The error value is core's vocabulary.** `type` is a closed enum, so `when:` stays
typecheckable and a handler routes on a value rather than a string match.

| Field | |
|---|---|
| `type` | `resource_unavailable` \| `invalid_output` \| `tool_error` \| `model_error` \| `timeout` \| `internal` |
| `node` | failing node id, qualified through nesting |
| `message` | one human-readable line |

The stacktrace goes to the record, never to memory. Memory is what reaches prompts.

Open: whether the failing node's inputs join `$.error` for a handler to reason over. They are
the best diagnostic and the likeliest home for attacker-controlled content. The standing
untrusted-content problem, in a new place.

**`end` carries a state: success or failed.** Success validates `return:` against the output
schema. Failed emits the error value.

That makes swallowing visible. A skill that catches, reports, then exits failed shows both
facts in the file.

Propagation needs no second mechanism. The caller's `skill.run` node sees a uniform error
either way, and the caller's own `catch:` applies.

It also gives the harness a real terminal signal. Record state, exit code, and "fire the error
skill" all read one field, instead of inferring from an escaping exception.

Static check gains a cheap rule. A catch handler whose only reachable exit is success `end` is
a swallowed failure.

Open: whether a failed `end` may also project memory. Error-only is simpler, and is the start.

**Two tiers, then a floor.** The interaction graph is not one of the tiers. It is not a skill
like any other — it is the core, the way V8's event loop is the core. It is built on the same
graph machinery, and later hardened against the edge cases a skill is allowed to fail on. If
it fails, the runtime has failed. That is a system error, not a case to design catch semantics
for.

| Scope | Handled by |
|---|---|
| Node error | the graph's `catch:` |
| Skill error | the caller's `skill.run` node error → the caller's `catch:` |
| Everything left | the floor: persist the record, harness moves on |

Two guards. An error inside a catch path does not re-enter catch. A failing error skill is not
itself handled or retried. One level, then the floor.

**Resources resolve lazily, at first use.** Eager resolution at run start puts the most common
real failure — an expired credential — before the entry node, where no graph can catch it.
Lazy resolution makes it an ordinary `resource_unavailable` node error.

Assume the path fails. No expiry pre-checks in the slice: a credential valid at check time can
still be dead at call time.

**Reaching a user is a routing problem, not a notification one.** An expired credential needs
a human to re-authenticate. The failing skill cannot ask; it may have no user attached. So the
error has to reach the interaction graph, the only graph that can.

| | Shape | For |
|---|---|---|
| Push | the schedule entry names an error skill; the harness starts a second run of it with the error as input | urgent. Content graph-authored, channel declared in that skill's `resources:` |
| Pull | uncaught errors append to a tier the interaction graph reads on the next user turn | everything else. Needs no channel resource, degrades when a channel is down |

Pull is the more interesting half. Re-auth becomes a conversation rather than an alert, and it
lands in the session and long-term tiers already sketched.

**Repair needs no self-recursion.** A wrapper skill runs the target, catches, reads `$.error`
in an `llm` node, runs a repair skill, then runs the target again.

The wrapper calls the target twice. Nothing calls itself, so P5's claim holds. Attempts are
bounded by the wrapper's own execution history, not a retry counter.

## File format

Highest user touch point and the hardest thing to change later. A graph is a flat file —
metadata, `resources`, `input`/`output`, `entry`, `catch`, `nodes`, `edges`, `return` — in YAML (JSON
is the same document).

| Choice | Consequence |
|---|---|
| A node's output appends to `$.<node_id>` | unique node ids give unique memory indexing for free; no separate `store:` field, and re-execution never overwrites |
| `out:` is both schema and decoding constraint on an `llm` node | one artifact, no drift between what is asked for and what is validated |
| A `tool` node has no `out:` | the tool declares its result schema; the graph can't disagree with it |
| Edges hold `when:`, evaluated top-down, `true` as the fallback | routing reads as a list; no condition nodes — but file order is semantics (P2) |
| `end` is a reserved target, `return:` projects memory to output | a graph's contract is visible without reading its nodes |
| `end` carries success or failed | a run has one terminal signal; a swallowed failure is visible in the file (P10) |
| `catch:` names one node; uncaught node errors route there | error handling is one place, not a predicate on every node |
| A tool node has exactly three coordinates — type, resource, call | one shape to learn, and the record logs the same triple |
| `resource:` names a declared resource; `model:` names an alias | no endpoint, credential, or weight path in a shared file |

Statically checkable before a run: node ids unique; edges reference real ids; every `$.` path
resolves against a declared schema; every declared resource bound; every `resource:`/`call:`
pair resolving to a real tool with args matching its schema; entry reachable and `end`
reachable from every node. That check is most of what a dry-run means.

**Repeated execution.** A node does not run once. A ReAct-style loop revisits the same `llm`
and `tool` nodes until it exits, so `$.<node_id>` is a history, not a cell: `$.classify` is
sugar for `$.classify[-1]`, `[-2]` the one before, `[0]` the first, `[*]` every execution in
order. Nothing is overwritten, so a loop's whole trajectory stays addressable while it runs
and inspectable after — the record and run memory are the same structure rather than two that
can disagree. Defaulting to `[-1]` keeps every `when:` in a loop reading "as of now", which
is what routing wants.

Two things follow. Indices are execution order **within the current scope**, so a nested run
keeps its own history (`$.triage[-1].gather[-2]`) and an inner loop never renumbers an outer
node. And a map node executes **once** and returns a list, so `$.map[-1]` is the whole list —
"ran N times" and "returned N items" stay distinguishable.

Open: retention. A long loop's history is unbounded. Everything lands in the record; whether
run memory keeps all of it or keeps a window is a memory-pressure call, not a semantics one.

**Stubbing nodes.** Testing a graph means pinning what a node returns without calling a model
or a network. Stubs live in a separate file keyed by node id, never in the skill — a skill
file should not carry test scaffolding to whoever installs it. Semantics borrowed from pytest
fixtures: a scalar answers every invocation, a list is consumed in order, and running past the
end of a list is an **error** by default — precisely the signal that a loop iterated more than
the test expected — with an explicit opt-in to repeat the last value. Each value validates
against the node's schema on the way in, so a stale fixture fails at load instead of
propagating a wrong shape downstream. A run record already holds every node's output in
order, which is the same structure, so replay is not a second mechanism.

**Nested skills — reference vs inline.** Both are supported and both land output at
`$.<node_id>`, so memory addressing does not depend on the choice. `ref` is the default — it
versions, it is vendable, and it keeps a skill one page; inline is for a one-off that reuse
would not justify. Open: whether `ref` resolves by path, by registry name, or both.

**One expression language.** Inside `{{ }}` only a `$.` path is legal. No filters, no
arithmetic, no `#if`/`#each`, no conditionals. Prose needs a boundary marker that a YAML
scalar does not provide, so the braces stay in `prompt:`; every structured field (`args:`,
`input:`, `when:`, `return:`) takes a bare `$.` path with no braces. One grammar, one
resolver, one static check over both.

What that bans is real, and graph structure already covers most of it: `#if` is an edge with
a `when:`, `#each` is a map node. What is left over is value shaping — joining a list into a
bulleted string, reformatting a date — and that home already exists: a stateless extension
(P8), so a transform is an ordinary `tool` node and users add their own without touching
core. Inline Python needs real thought (sandboxing, determinism, reproducibility) and stays
later work.

Unresolved:

- `$.classify.route` reads redundantly when a node is named after what it produces. Node
  naming convention matters more than expected.
- Whether `when:` is a real expression language or a small fixed predicate set. A full
  expression language is a large surface to keep deterministic and typecheckable.

Later: define the interaction graph in the same format, so conversation is authored the same
way a skill is.

## How tools reach the prompt

The backend does two things and neither is execution: it **renders** tool schemas into the
prompt where the model was trained to see them, and **parses** the generated call text back
into structured `tool_calls`. The caller executes and appends the result as a `tool` message.
Same split as LangGraph's `bind_tools` (render/parse) vs `ToolNode` (execute).

For `qwen3.5:9b` the rendering is not a Go template — its modelfile declares `RENDERER
qwen3.5` / `PARSER qwen3.5`, compiled into ollama (`model/renderers/qwen35.go`), so `ollama
show --template` is useless. That renderer puts tools in a leading system turn, appends any
user system prompt *after* it, emits calls as
`<tool_call><function=NAME><parameter=…></function></tool_call>`, and returns results as
`<tool_response>`.

Consequences: pass `tools` through the API and never hand-format them (`Model` today sends
messages only); an `llm` node does not fully own its prompt layout, since the renderer decides
where system content sits; renderers are per-model and have known bugs (Qwen3.5 tool
serialization, unclosed `</think>`), so pin the backend version and record it.

### How a graph grants tools

An `llm` node **never executes anything** — that stays the executor's invariant. It emits
calls into `$.<node_id>.tool_calls`, a `tool` node consumes them, and edges route on whether
a call was emitted at all. That is the stdlib `tool-call` skill, and it is why merging the
two into one node was rejected in P5.

The model still has to know which tools exist, which means the API's `tools` parameter, never
prose in the prompt. So an `llm` node takes a `tools:` list of grants written the way a tool
node addresses one — `<declared resource>.<call>` — with the argument schema and description
derived from the extension's Python rather than inlined at the call site. One addressing
scheme for tool nodes, grants, the record, and `lila call`. Grants being per-node gives the
record something exact to log: which tools were exposed to which call, not which tools the
graph could have used.

**One-tool variant.** When the tool is known at authoring time, skip `tools:` entirely: the
`llm` node's `out:` *is* the argument schema, and the tool node reads `$.<node_id>` and calls
with it. Same pipeline, one candidate tool, no dependence on the model's native tool-calling —
the model is only doing constrained decoding, which it already does everywhere else. Worth
supporting alongside the general form, because the bundled model set is small and fixed and
its tool-calling quality is a known risk.

## Runtime — short vs long term

Ollama is a thin-slice convenience, not the plan. The shipped product is 100% self-contained:
LILA bundles the inference runtime and one or two fixed models that the maintainer updates.

- Render/parse becomes LILA's job. Ollama's value is knowing each model's exact trained wire
  format; dropping it means owning that per bundled model — tractable precisely because the
  model set is fixed and pinned.
- Model + renderer + parser version together, so a run is reproducible.
- The `Model` protocol is the seam that keeps the swap cheap — nothing above it should learn
  what a backend is.
- Open: bundling and weight distribution (size, licensing, update channel), and whether an
  embedded runtime or a supervised local daemon.

## Sketch — goal #2, conversation as a graph

Postponed, recorded so the design doesn't paint it out. The argument for it is memory: graph
state *is* working memory, so context becomes constructed rather than accumulated, and a
retrieval node states what it fetched and why. A transcript-shaped loop can't be diffed; this
can — and that is what keeps a small model viable. Shape: `wait(user message) → assemble
context → route intent → {invoke skill | llm reply} → write session memory → wait`.

| Tier | Lifetime | Written by | Read by |
|---|---|---|---|
| Node I/O | one node | — | declared schema |
| Run | one run | node output, validated | exact path `$.…` |
| Session | one conversation | explicit write node | exact key or scan |
| Long-term | across sessions | explicit write node | retrieval with declared query |

**Interrupts**: only the interaction graph has this problem — skill graphs run async on
workers. No true mid-node interrupt is needed; it only has to *look* like one. Other node
types are short enough to finish, so the single case is aborting an `llm` node's stream:
discard the partial output, record the node as interrupted, cancel the run. A mid-run message
means one of three things — abort, amend, or an unrelated request — and a loop conflates them;
here an interrupt-handler node classifies with constrained output, which is visible and
testable. The aborted run's memory summarizes into session memory so the restart is not
amnesiac.

**Sessions**: keyed by channel + peer + thread, so Slack/Telegram/SMS don't collapse into one
unbounded conversation. Two ways to bound it — close on idle or turn cap, or run unbounded and
lean on hierarchical memory to summarize upward. The second is more interesting and is the
reason to build the memory hierarchy first.

## Plan

Proof-of-concept shape: **few modules, not one per proposal**. The file split the proposals
imply is deferred until the slice actually runs — splitting `executor.py` later is mechanical,
splitting it now costs import churn while the interfaces are still moving.

Everything lands in `src/core/src/lila/` unless noted.

| File | Holds |
|---|---|
| `executor.py` | graph model + loader, `$.` paths, run memory + record, the run loop, `llm` handler |
| `verification.py` | static check — the "compiled before it runs" half of the thesis |
| `resources.py` | resource + tool registries, instance construction, binding + typecheck |
| `ext.py` | the extension surface — `@resource`, `@tool`, `Secret`, schema derivation |
| `extensions.py` | discovery and loading of `.lila/extensions/` then `src/extensions/` |
| `tools.py` | the one `tool` handler — resolve, look up, validate, call |
| `model.py` | `Model` protocol + ollama backend |
| `values.py` | `Json` / `Yaml` / `JsonSchema` vocabulary |
| `config.py` | local install config — instances, bindings, secrets by env var, model alias |
| `schedule.py` | schedule table, cron expressions, due-time arithmetic, the tick loop |
| `commands.py` | what each command does, in plain values → exit code |
| `main.py` | argparse only — the one module with no tests, so it holds no logic |
| `.lila/` | the local install: `config.toml` + `extensions/`, untracked, read at run time |

| # | Task | Delivers | Depends on | State |
|---|---|---|---|---|
| T1 | Graph model + loader | typed graph from YAML | — | done |
| T2 | Path expressions | `$.` resolve/render over run memory | T1 | done |
| T3 | Run memory + record | append-only history, per-run log | T2 | done |
| T4 | Run loop | nodes + edge routing, recursive run unit | T1–T3 | done |
| T5 | `llm` node | constrained decoding via `Model` | T4 | done |
| T6 | Resources + tool node | declared resources, bindings, IMAP mailbox read | T4 | done |
| T7 | Static check | dry-run validation of a graph | T1, T2, T6 | done |
| T8 | E-mail skill + `lila run` | the proof | T5–T7 | built, unproven against a real inbox |
| T9 | Extensions (P8, P9) | `ext.py`, loader, e-mail moved out of core | T6 | done |
| T10 | Discord channel | an extension — resource + `post_message` tool | T9 | built, unproven against a real channel |
| T11 | Scheduler | `schedule.py`, cron-triggered runs, `lila schedule` | T8, T10 | new |
| — | *TODO* stubs + replay | run a graph with no backend | T3, T4 | punted, TODO in-code |
| — | *TODO* failure semantics | P10 in code | T4 | designed, deferred |

P10 is designed but not scheduled. Until it lands, a scheduled run that fails is silent past
the record, and T11 has no terminal state to read.

### T1 — Graph model and loader → `executor.py`

Frozen dataclasses `Graph`, `Node`, `Edge`, plus a per-type config dataclass selected on
`type:` through a registry dict, so adding a node type is one entry. Loader is
`load_graph(path) -> Graph` over `yaml.safe_load`, raising a `GraphError` carrying the
offending node id. Inline `graph:` recurses through the same loader.

### T2 — Path expressions → `executor.py`

Parse `$.a.b[0].c` / `[-1]` / `[*]` into a tuple of segments once, at load time, not per read.
Two entry points: `resolve(memory, path)` and `render(template, memory)` for `{{ }}` in
`prompt:` — the same parser both times, since only a `$.` path is legal inside braces. `[*]`
returns a list; a bare node id means `[-1]`. Structured fields hold bare paths, parsed at load
and stored as `Path` objects on the config dataclass.

### T3 — Run memory and record → `executor.py`

`RunMemory` wrapping `dict[str, list[Any]]` — append-only, one list per node id, `$.input`
seeded at start, resources resolved to handles and never stored. Writes validate against the
node's schema. `RunRecord` with `NodeEntry` (inputs read, output, resources by name, timing,
usage, backend version) and `EdgeEntry` (predicate, evaluated inputs, taken). Record and
memory share the append-order structure, so serializing a record yields a stub set. A nested
run gets its own `RunMemory` and a child `RunRecord` hung off the parent's node entry — the
isolation rule falls out of not passing the parent's objects down.

### T4 — Run loop → `executor.py`

`async def run(graph, input, resources, handlers) -> RunResult`. Loop: start at entry; call
the handler; append its output; take the first edge whose `when:` passes; stop at `end`.
Handlers are a dict keyed by node type, so T5/T6 register into it rather than editing the
loop — that registry is also the seam a stub set will wrap. `skill.run` calls `run` again with
a fresh memory built only from its `input:`/`resources:`. `when:` starts as a fixed predicate
set (`==`, `!=`, `in`, truthiness, `true`) rather than an expression language; the open
question stays open and the parser is one place. `return:` projects memory to the graph's
output and validates it.

### T5 — `llm` node → `executor.py`

Handler renders `prompt:` via T2, calls `Model.complete` constrained by the node's `out:`
schema, parses the JSON, appends. Model aliases (`default`) resolve through a small registry
so no file path or endpoint enters a graph. Tool grants land here in the one-tool variant
only; native `tools:` needs `tools`/`tool_calls` on the `Model` protocol and is deferred.

### T6 — Resources and tools → `resources.py`, `tools.py`

`resources.py`: a registry mapping binding name → instance plus type checking. Resolution
happens once at run start; handles are passed in the execution context, never in memory.
`tools.py`: the `tool` handler — resolve `resource:` to an instance, look up `call:` in that
type's tools, render and validate `args:`, call, validate the result.

**Mailbox: IMAP only.** The first mailbox resource type talks IMAP with an app password — for
Gmail that means 2FA plus a 16-char app password, stored 0600 outside the graph. No OAuth, no
Google Cloud project, no consent screen. Pluggable SASL is provider-portability work that buys
the slice nothing, and a different auth protocol is a different resource anyway (P8). Cost
accepted: IMAP flattens Gmail's model — labels appear as folders, so multi-label messages do
not round-trip, and thread ids and Gmail search syntax are out of reach without `X-GM-EXT-1`.
The skill is written to that grain, one folder per message.

**T9 splits it.** As built, `ImapMailbox` holds credentials, the IMAP session, a `match`
dispatch table, *and* the operations, with a bare `mailbox@1` interface string as its type.
T9 makes it a resource (connect/select/credentials) plus `@tool` functions over it, in an
extension outside core.

### T7 — Static check → `verification.py`

`check(graph) -> list[Issue]`, pure, no I/O, over the rules listed under File format. This is
`lila check <file>` and the first half of a dry run.

Sequenced before the skill rather than after it, because it *is* the differentiator — "a
workflow is checked before it runs, and a node can only touch the resources it declared" is
the whole answer to the ambient-authority failures that agent-shaped assistants hit (a skill
deleting drafts, duplicate sends). Shipping the proof without it would demo the wrong thing.

### T8 — E-mail organization skill

`.lila/extensions/lila-email/skills/digest.yaml` — installed, not shipped, and loaded from
disk rather than built in code, so reshaping the workflow is a file edit. Deliberately the
smallest thing that exercises fan-out: `list` (unread ids) → `summaries` (map, one child run
per id: fetch + summarize) → `digest` (llm, reduce). Success is: reshape the workflow (add a
node, reorder edges) and the executor is untouched. Anything that forces an executor change
here is a design bug worth folding back into the proposals. Read-only summarize for the first
real-inbox pass; classification and `move_message` come back once the digest reads right.

**Map on `skill.run`** — the fan-out P5 promised: a `for_each:` path to a list, one child run
per item, `$.each` bound only inside that node's `input:`, and the node's output is the list
of child outputs. The run loop stays linear — fan-out lives inside one node, so `_next_node`
never picks more than one edge. Sequential today; concurrent is `asyncio.gather` over the same
loop with no file-format change. Record keeps `children: [record, …]` rather than flattening.
`each` joins `end` as a reserved node id; the static check rejects `$.each` outside a node
that binds it.

Running it needs a `lila run` command and a way to build resources from local config, so T8
also covers:

| Piece | Is |
|---|---|
| `lila run <ref-or-path> --input k=v` | checks, runs, prints the output; `--record` writes the run record |
| `lila call <instance>.<call> --arg k=v` | one tool call on a configured instance, outside any graph — how you get message ids, and the first thing to reach for when a provider misbehaves |
| `config.py` | install discovery, config parsing, resource + model construction |
| `config.example.toml` | repo root, copied to `.lila/config.toml` |

**The install is a directory, not a file.** `.lila/` holds `config.toml` and `extensions/`,
found by walking up from the working directory (`$LILA_HOME` or `--home` overrides), with no
implicit fallback — an install is somewhere explicit, never somewhere assumed.

Three things follow. The product/install boundary becomes physical instead of a gitignore
rule: `.lila/` is untracked wholesale, so anything there arrived from outside — which is what
the T8 extension is emulating. `extensions/` is the root `resolve_skill_path` wants, so a
`ref:` resolves against the install rather than a path in a graph file, leaving only the
registry half of the ref question open. And a skill can be named instead of pathed, with a
path still accepted.

Because `.lila/` is untracked, the tests carry their own fixture extension — a fake resource,
two tools, one graph — rather than reading the real one from disk; a checkout must be able to
run the suite, and that exercises the loader without a network. The real IMAP path gets an
integration test that skips when unconfigured. `lila run` prints the home it resolved, since a
stray `cd` would otherwise silently pick a different install.

`--input` values stay text; `--input-json` is the opt-in for numbers and structure. Parsing
every value as JSON silently turned message id `7` into an integer and failed the input schema
— IMAP ids are numeric strings, so text is the right default.

Proof against a real (isolated) inbox is the remaining step, and it is where the design gets
its first real feedback — what the record misses, where the schema fights the model, whether
IMAP's grain hurts.

### T10 — Discord channel → extension

A channel is a resource. Posting is a tool over it. `resource: alerts, call: post_message`,
and the executor never learns what Discord is.

No `notify` node type. A node type per transport is the family P1 rejected.

The authority story is unchanged. A skill that can post to a channel says so in `resources:`,
bound at install, logged in the record. Nothing in core knows a channel exists.

**Outbound only.** There is already a bot app and token (Hermes), so posting is
`POST /channels/{id}/messages` with `Authorization: Bot …`. That is `urllib.request` and
nothing else, so it stays stdlib-only and in-process under P9.

Receiving is where that stops. The gateway is a websocket, not request/response, and not
stdlib. Inbound Discord is the MCP boundary's first real customer, not a bigger version of
this extension. Outbound-only is the seam, not a shortcut.

The first consumer is a small notification skill: `llm` shapes the message, `tool` posts it.
A skill wrapping one tool call is ceremony. One that *shapes* the message is not, and it is
what the scheduler composes instead of hardcoding.

Open: message shape. Discord's 2000-character cap is the one constraint worth designing to.

**Built** as `.lila/extensions/test-discord/`: `test/discord@1/channel` (token, channel_id,
api_base) plus `post_message`, and the `discord-notify` skill — `llm` shapes the note,
`tool` posts it. Over-length content is truncated with an ellipsis and the result says so,
so a notification always lands; splitting into N posts is noted in the code as later work,
since it turns one call into N with partial-failure semantics. `lila call` proves the path
end to end (a placeholder token gets a clean `401` as a `ToolError`, not a traceback).

### T11 — Scheduler → `schedule.py`

Cron is the vocabulary. Five fields, everyone knows them, and the expression is what a user
edits.

Own the tick loop rather than write crontab lines. System cron cannot express *don't start if
the last run is still going*. It has no catch-up policy after a laptop sleeps. It gives no run
record. And it wants the install path (`.lila/`, `$LILA_HOME`) baked into a line that silently
rots.

A schedule entry is a saved invocation: skill ref, `input:`, the cron expression, and the
error skill to run if the run ends failed. It is config, not a graph —
`.lila/schedules.toml`, beside `config.toml`, so scheduling never becomes a node type either.

The scheduler is the first caller with no user attached. That is what makes P10 load-bearing
here, and P10 is deferred — so the error skill is the one part of an entry that does nothing
yet.

Once P10 lands, the scheduler reads one field: the run's terminal state. It does one thing
with a failure — start the entry's error skill with the error as input, once, unhandled. That
is P10's push half. The pull half needs no scheduler support; the failure is already in the
record.

| Piece | Is |
|---|---|
| `lila schedule list` | entries, last run, next due |
| `lila schedule run <name>` | fire one now, ignoring the clock — the debug path |
| `lila schedule tick` | run everything due once, then exit — cron-safe, testable, no daemon |
| `lila schedule daemon` | `tick` in a sleep loop; the same code path |

`tick`-then-`daemon` keeps the scheduling logic pure. Due time is a function of (expression,
last run, now), tested with no clock and no sleep.

Open: misfire policy after a long sleep — skip, catch up once, or catch up all. And overlap —
skip or queue. Both want a default before they want a setting.

Cron parsing is ~60 lines of stdlib. The ranges-and-steps subset is enough; a dependency is
not worth it.

### TODO — stubs and replay

`load_stubs(path) -> StubSet`, a handler wrapper that intercepts by node id, with the
semantics under File format. Replay is `StubSet.from_record(record)` — same type, no second
mechanism.

### TODO — failure semantics → `executor.py`, `verification.py`

P10 in code. Four pieces, all core, no new module.

| Piece | Is |
|---|---|
| Error value | a frozen dataclass with a closed `type` enum; the record additionally holds the stacktrace |
| `catch:` | one graph-level field naming a node; the run loop routes an uncaught node error there and binds `$.error` for that subgraph |
| `end` state | success validates `return:`; failed emits the error value. `RunResult` carries the state |
| Lazy resources | resolve on first use inside a node, not at run start, so a dead credential is a catchable node error |

Three static check additions. `$.error` only inside the catch subgraph. `catch:` names a real
node. A warning when a catch subgraph's only reachable exit is a success `end`.

Nesting gives skill-level propagation with no extra code: an uncaught error in a child run
surfaces as the `skill.run` node's error. An error raised inside a catch path is not caught
again. It ends the run at the floor.
