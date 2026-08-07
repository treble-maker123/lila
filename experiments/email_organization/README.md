# E-mail Organization

Common task for local agents, no?

(Ran on an M1 MacBook Pro with 16GB of RAM)

## Getting Started

- Run `make run-smoke` in the directory to kick off a [smoke test](datasets/emails_smoke.json) to make sure everything is working as expected,
- A few hand-curated realistic seed data points can be found in `datasets/seed`,

## Hypothesis

Decomposition allows a 9B model to match a frontier loop on extraction accuracy, at the cost of higher wall-clock.

## Task

Agent pipeline: fetch -> gather context -> route.

- Route / Next Step - `reply` | `no_action` | `flag_for_human`,
  - `reply` - The agent has enough information or don't require additional, and can reply directly to the user,
  - `no_action` - No action from the agent. Either the E-mail does not require one (CC'd E-mails, certain promotional E-mails, automated notifications) or the agent leaves it in the inbox because the user may plausibly want to see it. Only when the agent is confident nothing is needed,
  - `flag_for_human` - Needs doing, but the agent can't do it - either out of scope (action in another system, e.g. paying) or missing information (availability). Also the default when the agent isn't sure: surfacing an E-mail is safer than burying it, and it keeps `no_action` meaning "needs nothing" rather than "couldn't tell",
- Draft Reply - text passed to `reply(message)`, only when routing to `reply`,

E-mail *type* (`action_required` | `fyi` | `promotional` | `suspicious`) is kept
as dataset metadata (`category`) for slicing results, but the agent no longer
emits an explicit classification step.

## Setup

Primary

1. ReAct loop, local Qwen3.5 9B,
2. Graph workflow, local Qwen3.5 9B,

Not in the harness yet: a frontier ReAct loop (ceiling) and a voting setup (N
local runs + plurality). Both removed for now to keep loop-vs-graph the only
thing measured. Voting only means anything above temperature 0.

Both setups use native tool calling with the same schemas, so the comparison is
control flow, not decoding mode. Setup 2 exposes only the tools its current node
may use, one turn per node. The one remaining difference is the agent-loop
system prompt, which setup 2 doesn't need — knowing each node's job up front is
what the graph buys.

### Tools

All setups share one tool set, served by a mock MCP server that returns fixed,
per-email values (`Email.tool_returns`) so runs stay deterministic. The ReAct
setups let the model order these calls; the workflow calls them in a fixed
sequence.

Read / environment (each has a fixed return format):

- `get_new_email()` -> `{"email": <text>}`, the email to process (call first),
- `check_calendar_available(time, length)` -> `{"available": <bool>}`, for scheduling asks,
- `check_unknown_sender(sender)` -> `{"known": <bool>}`, whether the sender is a contact,
- `get_note()` -> `{"notes": [<text>, ...]}`, the user's standing preferences.

`get_note` takes no arguments. Retrieval is out of scope, so the store is assumed
to always surface the notes relevant to the email; fixtures may add unrelated notes
as noise. Reading past the noise is the skill exercised, not finding the right note.

Per-email fixtures (`Email.tool_returns`) map each read tool the email expects to be
called to its return value, following the shapes above — e.g. a suspicious email sets
`{"check_unknown_sender": {"known": false}}`. There are no server-side defaults: the
mock server **raises** on any read-tool call without a matching fixture, so missing
fixtures or unexpected lookups fail loud rather than silently returning a value. An
empty `{}` means the email expects no read-tool calls at all.

Answer / routing:

- `reply(message)` -> the agent can answer directly,
- `no_action()` -> the email needs nothing,
- `flag_for_human(actions)` -> needs doing but out of scope / missing info; `actions`
  (the `{verb, subject, deadline}` items) is optional and may be an empty list.

Exactly one routing tool is expected per email. Calling none is an `error`, not a
silent `no_action` — a single-shot node offering only `reply`/`flag_for_human`
cannot express no-action by staying quiet, and in the loop silence was
indistinguishable from the model wandering off. Note `no_action()` is the only
routing tool with no arguments, so it is also the cheapest to emit; watch whether
it gets over-selected on emails that genuinely need action.

Pipeline: `get_new_email -> gather context -> reply | no_action | flag_for_human`.

Actions are extracted only on the `flag_for_human` path — `reply`/no_action emails
carry none.

Anything with a pending action the agent can't complete itself is `flag_for_human`
(e.g. "sign the Q3 budget"), so `reply` cases carry no outstanding actions.
Deliberately, no tool exists for out-of-scope actions like paying — the correct
behavior there is to flag, not to hallucinate a capability.

## Dataset

Synthesized E-mails with hand labels for actions and action items with the following attributes,

- Number of E-mails: 40,
  - 30% promotional / fyi,
    - All promotional and half the fyi target `no_action` (early exit) instead of summarizing for the user, to keep the experiment simple. Summarizing these E-mails as a part of batch is left for future works,
    - The other half of fyi target `reply` — informational, but ending on a question the agent can answer,
  - 30% single-ask,
    - There is one ask in the E-mail as an action, some with a deadline, some don't,
  - 15% multi-ask,
    - Multiple actions needed, possibly supplied as a long E-mail thread,
  - 15% asks buried in E-mail chains,
    - There is an E-mail thread, and the latest E-mail contains some asks,
  - 10% suspicious.
    - Spam or scammers

Multi-ask and buried E-mails skew toward `flag_for_human` as the next step, so within each of those two categories the labels are split evenly between `reply` and `flag_for_human`.

## Metrics

- **Cost** - tokens and wall-clock. This is what the harness reports now.
  - Peak RAM is not measured. Ollama's `/api/ps` reports weights + KV cache, but `context_length` is pinned at 4096 and the KV cache is preallocated, so residency sat flat at 5.26 GiB regardless of setup. Nothing to measure until context length varies.
  - Input tokens are reported twice. *Cumulative* sums every call's prompt — a ReAct loop re-sends the whole conversation each turn, so an N-turn loop counts the shared prefix N times. *Unique* counts each prompt token once (for the loop, just the final call's prompt, since the message list only grows; for the graph the node prompts share no cacheable prefix, so the two are equal). The gap is the loop's re-reading, which Ollama's KV cache mostly avoids in practice — so cumulative overstates the loop's local cost and unique understates it.
  - Wall-clock is rough. The model is warmed up before the timed region so load time doesn't land on the first email alone (`--no-warm-up` to disable).
- **Errors** - email-runs that produced no routing decision, recorded as `next_step: "error"` with an `error.kind` — never folded into `no_action`. `no_action` is correct for ~22% of the dataset, so defaulting failures to it would credit whichever setup flails most.
- **Agreement** - the agreement rate across runs on the routing decision (`next_step`): take the most common answer, how many runs match it?
- **Accuracy** - deliberately *not* scored in the harness yet. The setups capture the full outputs (actions, routing, draft, tool invocations); scoring needs to be designed more carefully (see below) before we put a number on it.
- **Draft quality** - Semantic quality of the output is out of scope for now to keep scoring deterministic. 

## Caveats

- Temperature - at 0, all setups may be consistent (minus hardware-related drift). At 1.0, loop may destabilize in a way that overstates the effect. Need to test across different temperatures,
  - One perk with graphical workflows is that temperature can be set at a finer granularity. For this experiment, we are deliberately not exercising it so comparison stays clean,
- To keep the experiment simple, images and attachments are out of scope.

## Future Work

- Who reads E-mails one at a time? You'd probably want an agent that pulls the latest unread E-mails and process them together. I imagine this is even worse for local models + ReAct style loops because of how much bigger the context gets.