# E-mail Organization

Common task for local agents, no?

(Ran on an M1 MacBook Pro with 16GB of RAM)

## Getting Started

- Run `make run-smoke` in the directory to kick off a [smoke test](datasets/emails_smoke.json) to make sure everything is working as expected,
- A few hand-curated realistic seed data points can be found in `datasets/seed`,
- Before generating `datasets/emails_individual.json`, read `datasets/EMAIL_GUIDELINE.md` for the label distribution, body style, and tool-return rules,
- `make score RESULTS=results/<file>.json` re-scores a finished run without touching the model, folding a `labels` block back into the file; `run` writes and prints the same thing at the end,
- `make results-notebook` opens `results/visualize_emails_individual.py`, a marimo notebook over those scored results,
- `make dataset-notebook` opens `datasets/visualize_emails_individual.py`, the same treatment for the dataset itself — distribution, body/note sizes, tool fixtures, and a browsable list. No run needed.

## Hypothesis

As context grows, a 9B models' routing accuracy degrades less in a workflow than in a ReAct loop, but pays in wall-clock time and total tokens.

(Really, just want to get a feel of workflow vs. loop with some scale)

### Honorable Mentions (i.e. for later when time allows)

- Repair-ability - a failed workflow node tells you which step broke, a failed loop gives you wrong answer,
- Selective temperature - loop has steady temp (AFAIK), probably a not twistable knob in a workflow,
- Workflow compilation > natural language YOLO'ing (?) - large models compile once, small models execute many times,
- Handling drift - underlying data (webpages scrapped) changed, how well does the setup hold?

### Observations & Learnings

- KV cache makes loops input-token efficient - for short-horizon tasks it ends up consuming less tokens,

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

- Number of E-mails: 50,
  - 20% promotional,
    - Most target `no_action` (early exit) instead of summarizing for the user, to keep the experiment simple. Summarizing these E-mails as a part of batch is left for future works,
    - Two promotional E-mails instead target `flag_for_human`, because a standing note in `get_note` turns the offer into something the user wants done and the agent cannot do (buying). Without them "promotional" *is* the answer, and the category is worth free points to any model that recognises the shape without reading anything,
  - 20% fyi,
    - Most target `no_action`; the rest target `reply` — informational, but ending on a question the agent can answer,
  - 26% single-ask,
    - There is one ask in the E-mail as an action, some with a deadline, some don't,
  - 12% multi-ask,
    - Multiple actions needed, possibly supplied as a long E-mail thread,
  - 12% asks buried in E-mail chains,
    - There is an E-mail thread, and the latest E-mail contains some asks,
  - 10% suspicious.
    - Spam or scammers

Multi-ask and buried E-mails skew toward `flag_for_human` as the next step, so within each of those two categories the labels are split between `reply` and `flag_for_human`.

**Route balance.** `next_step` is held close enough to balanced that no route is a free default: 17 `reply` / 19 `flag_for_human` / 14 `no_action`. An earlier 40-email set was 47.5% `flag_for_human`, which handed an always-flag baseline 0.475 and flattered any setup biased that way — and the routing policy already names `flag_for_human` as the tie-break when unsure, so the class prior and the policy were pushing in the same direction and could not be told apart. The policy bias stays; the prior does not. Report accuracy against the majority-class baseline, and prefer macro-F1 over accuracy when a run's errors are unevenly spread.

**Contradictory notes.** Four E-mails (`notes_conflict: true`) carry a `get_note` fixture built to mislead — stale, contradictory, or about a neighbouring matter — spread across two `reply` and two `flag_for_human` items so the trait does not correlate with a route. They exist because every other E-mail's notes are relevant and free to fetch, which makes unconditional context-gathering optimal by construction and hands the graph setup its main advantage for free. These are the items where gathering has to be a judgment call.

## Metrics

Scoring is post-processing over captured outputs, so results re-score without
re-running inference.

The harness emits counts, not ratios. Accuracy, precision, recall and F1 are all
derived from them at viewing time, and a count can be re-aggregated across runs
where a ratio cannot.

### Cost

| Metric | Counts | Caveat |
| --- | --- | --- |
| `tokens_in_cumulative` | every call's prompt, summed | A loop re-sends its conversation each turn, so an N-turn loop counts the shared prefix N times. What an uncached, per-call-billed API charges; **overstates** the loop locally, where the KV cache avoids the re-reading |
| `tokens_in_unique` | each prompt token once | For the loop, just the final call's prompt — the message list only grows, so every earlier prompt is a prefix of it. For the graph the node prompts share no cacheable prefix, so it equals cumulative. **Understates** the loop |
| `tokens_out` | generated tokens | — |
| `wall_clock_ms` | time in the scored region | Rough. The model is warmed up first so load time doesn't land on email #1 alone (`--no-warm-up` to disable) |
| `peak_context_tokens` | KV occupancy in tokens: `max` over calls of prompt + generated | What must be resident at once, not what was processed. The loop's grows with its transcript; the graph's is its largest single node |
| `memory` | that peak in bytes: `kv_bytes` + `weights_bytes` = `total_bytes` | Weights are equal across setups, so the difference is all `kv_bytes`. Null if calibration failed |

Report both input-token numbers; the gap between them is the loop's re-reading
overhead. Token and time metrics sum across emails; memory takes the **max** — a
setup needs its worst email, not the total.

#### Measuring memory

`--num-ctx` (default 32768) pins the context window. At Ollama's 4096 default the
loop's transcript overruns it and the oldest tokens are silently dropped, so the
comparison measures truncation rather than control flow.

`/api/ps` can't give peak RAM: the runner reserves weights plus KV for the whole
`num_ctx` at load, identical for both setups. `src/memory.py` calibrates instead —
load at two `num_ctx` values, and the slope of reserved size is one KV slot:

    bytes_per_token = (size(ctx_hi) - size(ctx_lo)) / (ctx_hi - ctx_lo)
    weights_bytes   = size(ctx_lo) - ctx_lo * bytes_per_token

Times `peak_context_tokens`, that is the setup's footprint. Two extra model loads
at startup; `/api/show` config is unreliable here (`qwen3.5:9b` omits
`head_count_kv`).

### Errors

Email-runs that produced no routing decision (`next_step: "error"`), reported as
a rate and by `error.kind`. Never folded into `no_action`: that route is correct
for ~22% of the dataset, so defaulting failures to it would credit whichever
setup flails most.

### Labels

Predicted `next_step` vs. label. One run is one pass over every email.

| Metric | Calculation | Across runs |
| --- | --- | --- |
| `correct` | per run, over all emails; an `error` is never correct | mean / min / max / stdev |
| `majority_correct` | per email, whether the most common answer across runs matches the label | single number |
| `decided` | per run; emails − errors. Denominator for routing quality with robustness factored out | mean / min / max / stdev |
| `tp` / `fp` / `fn` / `tn` | per run, per class, one-vs-rest; `error` predictions count against every class | summed |
| `pass^k` | per email, how many of the runs matched the label | curve over k = 1…runs |

Both `correct / emails` and `correct / decided` are worth reading: the first is
the headline, the second separates routing quality from failing to terminate.

`tp/fp/fn/tn` are counted on two axes: per `next_step` class (is `no_action`
over-emitted? are `flag_for_human` emails buried?) and per `category`, which
shows the email shapes that break without needing new labels.

`correct` and `pass^k` are two margins of one emails × runs matrix: `correct`
reads a row (one run, every email), `pass^k` a column (one email, right *all* k
times — τ-bench's sense, not best-of-k). With `n` runs and `cᵢ` correct on email
*i*:

    pass^k = (1/emails) · Σᵢ C(cᵢ, k) / C(n, k)

Only `cᵢ` is stored, so every k derives at viewing time. Plot k = 1…runs:
`pass^1` is exactly the mean of `correct / emails`, so the decay from it is
instability, not accuracy. Needs `--runs` ≥ 2 to say anything.

`majority_correct` is the opposite bound — `pass^n` is the setup unaided, majority
is it with n-way voting on top. An email right in 2 of 3 runs gives 0.67 to mean
`correct`, 0 to `pass^3`, 1 to `majority_correct`.

At 50 emails gaps under ~7 points are noise, and `pass^k` is the noisiest here.

Runs are not independent samples when the model is deterministic: at `--temperature
0.0` every run comes back identical, so `--runs 5` is 50 emails of evidence, not
250, and the per-class precision/recall computed over 250 email-runs carry
confidence intervals a factor of √5 too tight. Compare setups pairwise per email
(how many emails did A get right that B got wrong, and vice versa) rather than
comparing the two accuracy figures. `--runs` > 1 only buys information above
temperature 0.

### Actions

Scored only on emails where the label and the prediction both routed
`flag_for_human` — scoring the rest charges a routing miss twice, once here and
once in Labels.

Two questions, three counts:

| Metric | Question | Count |
| --- | --- | --- |
| Precision | Is each predicted action in the label set? | `matched / predicted_total` |
| Recall | Is every label action covered? | `matched / gold_total` |

**How `matched` is defined is still open** — actions are free text, so it rests on
a boolean "does predicted action *i* correspond to label action *j*", and that
boolean is where the ambiguity and the trade-offs live. What it has to satisfy is
settled: verb-sensitive (`pay X` is not `sign X`), subject-sensitive (`sign X` is
not `sign Y`), and indifferent to which field the text landed in. Candidate
definitions and their calibration are to be worked out experimentally.

One thing the definition does not have to carry: a prediction may bundle several
asks into one item (`sign and review X` against two label actions). Both sides
are first split to one action per item so the two sets are compared at the same
granularity.

Every match and non-match is written to an audit sidecar. The definition will be
wrong on some pairs, and at ~30 action items the full decision list is short
enough to read.

Deadlines score separately and weakly: among matched pairs whose label has one,
how many predictions supply a loosely agreeing value. Labels say `Wed, 29 Jul
2026` where the model says `today before legal counsel goes offline` or `null` —
read it as "does it carry deadlines at all", not date accuracy.

### Draft quality

Out of scope for now, to keep scoring deterministic.

## Caveats

- Temperature - at 0, all setups may be consistent (minus hardware-related drift). At 1.0, loop may destabilize in a way that overstates the effect. Need to test across different temperatures,
  - One perk with graphical workflows is that temperature can be set at a finer granularity. For this experiment, we are deliberately not exercising it so comparison stays clean,
- To keep the experiment simple, images and attachments are out of scope.
- Tool namespace size is not held equal, and deliberately so. The graph offers each node only the tools that node may use — three at the gather step, three at the decide step — while the loop carries all seven at every step. Part of any loop-vs-graph gap is therefore the loop choosing from a wider menu, not the control flow itself. Collapsing the read tools behind one entry point would equalize it, but it would also take away the loop's ability to pick, which is the thing being tested. Read it as a real cost of the loop shape: **a loop needs deliberate tool-search design — progressive disclosure, namespacing, retrieval over tool descriptions — and that design work is load-bearing at a scale this experiment (seven tools) barely probes.**
- Setup 2's `get_new_email` is dispatched in code and costs no forward pass, so on emails where the loop routes immediately the graph gets two model calls that have seen the email against the loop's one. Forced ordering is genuinely the graph's advantage; the free call is an implementation choice, and it flatters the graph's cost numbers in particular.

## Future Work

- Who reads E-mails one at a time? You'd probably want an agent that pulls the latest unread E-mails and process them together. I imagine this is even worse for local models + ReAct style loops because of how much bigger the context gets.
