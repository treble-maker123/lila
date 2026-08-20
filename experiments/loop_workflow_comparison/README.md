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

~~As context grows, a 9B models' routing accuracy degrades less in a workflow than in a ReAct loop, but pays in wall-clock time and total tokens.~~ (This is a terribly hypothesis in hindsight because context could be task context and model context. If we're talking about model context, it's irrelevant here. If we're talking about task context, there's engineering solution to loops, e.g. subagents.)
- Total tokens because loops process minimal new tokens because of caching, graph would need to encode each node separately,
- Wall-clock time because more new tokens to process.

But I think the initial motivation was that control flow is delegated to computer, so the real hypothesis should be that graph is more consistent.

(Really, just want to get a feel of workflow vs. loop with some scale)

### Honorable Mentions (i.e. for later when time allows)

- Repair-ability - a failed workflow node tells you which step broke, a failed loop gives you wrong answer,
- Selective temperature - loop has steady temp (AFAIK), probably a not twistable knob in a workflow,
- Workflow compilation > natural language YOLO'ing (?) - large models compile once, small models execute many times,
- Handling drift - underlying data (webpages scrapped) changed, how well does the setup hold?

### Observations & Learnings

- KV cache makes loops input-token efficient, but graph could also take advantage of that,
- Ollama's `prompt_eval_count` is cache-independent, so that efficiency shows up in wall clock, not in the token counts (see "Measuring input tokens"),
- Dispatching calls in code saves token / $$$,
- The loop does a lot of narration, which leads to more output tokens and longer wall-clock time,
- The loop's decision is steered by its own narration / reasoning, the graph isn't. Which has implications on consistency,

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

Per-email fixtures (`Email.tool_returns`) give every read tool a return value,
following the shapes above. There are no server-side defaults: the mock server
**raises** on any read-tool call without a matching fixture, so a typo'd tool name
fails loud rather than silently returning a value.

Every email in `emails_individual.json` carries all three read fixtures, so a
spurious lookup is answered rather than punished. That is deliberate — a raise lands
as an `error`, which is a much harsher and noisier penalty than the mistake deserves.
The cost of over-gathering is measured instead: `read_tool_calls` is reported per
setup, and the `notes_conflict` emails are where gathering actively costs accuracy.

Answer / routing:

- `reply(message)` -> the agent can answer directly,
- `no_action(reason)` -> the email needs nothing,
- `flag_for_human(actions)` -> needs doing but out of scope / missing info; `actions`
  (the `{verb, subject, deadline}` items) is optional and may be an empty list.

Exactly one routing tool is expected per email. Calling none is an `error`, not a
silent `no_action` — a single-shot node offering only `reply`/`flag_for_human`
cannot express no-action by staying quiet, and in the loop silence was
indistinguishable from the model wandering off. All three take an argument: with
`no_action()` empty it was the shortest token path of the three and got selected for
reasons unrelated to the email. If a setup emits several routing calls in one
message, the first wins, in both setups.

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

**Contradictory notes.** Seven E-mails (`notes_conflict: true`) carry a `get_note` fixture built to mislead — stale, contradictory, or about a neighbouring matter — spread across three `reply`, two `flag_for_human` and two `no_action` items so the trait does not correlate with a route. They exist because every other E-mail's notes are relevant and free to fetch, which makes unconditional context-gathering optimal by construction and hands the graph setup its main advantage for free. These are the items where gathering has to be a judgment call. Seven rather than four: at 50 emails the noise floor is ~3.5 emails, and four traps against ~20 emails where notes help could not resolve the penalty they exist to price. It is still 7 against 17 — the incentive to gather unconditionally is reduced, not removed.

**Body length.** Bands run S/M/L/XL (120-180, 200-350, 400-550, 600-750 words) and every band carries all three routes. Length is the independent variable in the hypothesis, so a band holding only one or two routes would make "degrades on long emails" indistinguishable from "gets that route wrong" — the second round had no `reply` at XL at all.

**Category is not balanced against route, and reading results has to allow for it.** `no_action` occurs only in promotional and fyi; suspicious is 5/5 `flag_for_human`; fyi never flags. Category is not given to the agent, but it is readable off the email, so a guesser that recognises the shape and takes each category's most common route scores 32/50 = 64% without calling a tool. Report accuracy against **that** number, not only against the 38% majority-class baseline. Balancing route within category is the fix and has not been done.

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
| `tokens_in_unique` | each prompt token once | For the loop, just the final call's prompt — the message list only grows, so every earlier prompt is a prefix of it. The graph's two node prompts lead with the same email block by construction, so that block is counted once. **Understates** the loop |
| `tokens_out` | generated tokens | — |
| `read_tool_calls` | calls to `get_note` / `check_calendar_available` / `check_unknown_sender` | Gathering is +EV on all but seven emails, so accuracy alone hides how much context a setup bought to get it. Routing and `get_new_email` are excluded: one of each per email by construction |
| `wall_clock_ms` | time in the scored region | Rough. The model is warmed up first so load time doesn't land on email #1 alone (`--no-warm-up` to disable) |
| `peak_context_tokens` | KV occupancy in tokens: `max` over calls of prompt + generated | What must be resident at once, not what was processed. The loop's grows with its transcript; the graph's is its largest single node |
| `memory` | that peak in bytes: `kv_bytes` + `weights_bytes` = `total_bytes` | Weights are equal across setups, so the difference is all `kv_bytes`. Null if calibration failed |

Report both input-token numbers; the gap between them is the loop's re-reading
overhead. Token and time metrics sum across emails; memory takes the **max** — a
setup needs its worst email, not the total.

#### Comparing cost between setups

Same pairing argument as the labels: both setups see the same emails, so compare
per-email differences rather than the two totals. The notebook runs a **Wilcoxon
signed-rank** test on `dᵢ = log cost₁(eᵢ) − log cost₂(eᵢ)`, where each `cost(eᵢ)`
is the mean over runs — signed-rank rather than a t-test because cost is
right-skewed, per-email means rather than per-email-runs because the runs are not
independent (n = 50, not 250). The effect is the Hodges–Lehmann median difference
with a distribution-free 95% CI; on the log scale it reads as a ratio, which is
the honest scale for a multiplicative quantity.

Five metrics are tested, three of which bracket the same input tokens:

| Metric | Reads as |
| --- | --- |
| `tokens_in_unique` | Perfect-cache floor — assumes repeats are served free |
| `tokens_in_cumulative` | No-cache ceiling |
| `tokens_in_cumulative` minus the `fetch` role | Control-flow-clean: drops the graph's free `get_new_email` |
| `tokens_out` | The loop's narration |
| `wall_clock_ms` | The only one of the five that costs anything locally |

Neither input-token endpoint is a bill. A real API charges cache reads at a
fraction of the input rate and cache writes at a premium, so an invoice sits
between them; locally the KV cache already avoids the re-reading, so it costs
wall clock and nothing else. Agreement across all three is the claim worth
making — disagreement says the gap is caching policy, not control flow. The
fetch-excluded row is only computable on `cumulative`, which is a per-call sum
with parallel `call_roles`; `tokens_in_unique` is a single derived number (for
the loop, the final call's prompt) and cannot be decomposed that way.

`read_tool_calls` and `peak_context_tokens` are reported without a p-value —
they correlate with the tested metrics, so pricing them too would buy
multiplicity rather than information.

#### Measuring input tokens

Ollama's `prompt_eval_count` reports the **full** prompt on every call, whether or
not the KV cache already held a prefix of it — probed against the live server rather
than assumed. Send a prompt twice back to back and the count is identical both times.
So `tokens_in_cumulative` and `peak_context_tokens` mean what they say, and the
memory numbers derived from the peak are sound.

The flip side is that the provider will not deduplicate a shared prefix for you. The
graph's node prompts are built to lead with the same email block precisely so the
second node's prefill reuses the first node's cache; counting both prompts in full
charged it twice. `src/tokens.py` measures that block by difference (a node prompt
costs `HEAD + email + suffix + TAIL`, the suffix alone costs `HEAD + suffix + TAIL`)
using one extra call at startup, and subtracts it once. The measurement is short of
the true shared prefix by `HEAD`, a few tokens, which errs toward crediting the graph
with more unique input rather than less.

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

The notebook runs that comparison as an exact two-sided McNemar test —
`scipy.stats.binomtest(b, b + c, 0.5)` over the discordant emails — under two
per-email notions of correct, which bracket the same runs:

| Pairing | Email counts as correct when | Reads as |
| --- | --- | --- |
| `majority` | a strict majority of runs match the label (a tie does not) | the setup with n-way voting on top |
| `pass^n` | *every* run matches the label | the setup unaided, asked to be right every time |

A gap that opens only under `pass^n` is a consistency gap, not an accuracy one.
Only k = n is tested: for k < n the per-email `pass^k` is `C(cᵢ,k)/C(n,k)`, an
average over run subsets rather than the binary outcome McNemar pairs on.

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

## Scheduling

The setups are **interleaved per email** and the order alternates: setup 1 on e001,
setup 2 on e001, setup 2 on e002, setup 1 on e002, and so on. Running one setup to
completion and then the other tied setup to position — whichever went second
inherited a warmer machine, and the loop's system prompt is identical on every email,
so its first call per email hit a prefix cache left by the *previous* email while the
graph's email-led prompts never did. Both landed on `wall_clock_ms` as if they were
control flow.

The prefix cache is also busted before each unit (one email, one setup) with a short
throwaway call outside the timed region. Best effort: with `OLLAMA_NUM_PARALLEL > 1`
the buster may land in a different slot than the run that follows it.

Lowering `--num-ctx` is *not* a way to force cache eviction — it sizes the context
window, and at 4096 the loop's transcript is silently truncated, which is the failure
the flag exists to prevent. Leave it at 32768.

## Caveats

- Temperature - at 0, all setups may be consistent (minus hardware-related drift). At 1.0, loop may destabilize in a way that overstates the effect. Need to test across different temperatures,
  - One perk with graphical workflows is that temperature can be set at a finer granularity. For this experiment, we are deliberately not exercising it so comparison stays clean,
- To keep the experiment simple, images and attachments are out of scope.
- Tool namespace size is not held equal, and deliberately so. The graph offers each node only the tools that node may use — three at the gather step, three at the decide step — while the loop carries all seven at every step. Part of any loop-vs-graph gap is therefore the loop choosing from a wider menu, not the control flow itself. Collapsing the read tools behind one entry point would equalize it, but it would also take away the loop's ability to pick, which is the thing being tested. Read it as a real cost of the loop shape: **a loop needs deliberate tool-search design — progressive disclosure, namespacing, retrieval over tool descriptions — and that design work is load-bearing at a scale this experiment (seven tools) barely probes.**
- Setup 2's `get_new_email` is dispatched in code and costs no forward pass, so on emails where the loop routes immediately the graph gets two model calls that have seen the email against the loop's one. Forced ordering is genuinely the graph's advantage; the free call is an implementation choice, and it flatters the graph's cost numbers in particular.

## Future Work

- Who reads E-mails one at a time? You'd probably want an agent that pulls the latest unread E-mails and process them together. I imagine this is even worse for local models + ReAct style loops because of how much bigger the context gets.
