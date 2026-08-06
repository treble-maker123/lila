# E-mail Organization

Common task for local agents, no?

(Ran on an M1 MacBook Pro with 16GB of RAM)

## Hypothesis

Decomposition allows a 9B model to match a frontier loop on extraction accuracy, at the cost of higher wall-clock.

## Task

Four stages: classify -> extract -> decide -> draft.

- Classify Type - `action_required` | `fyi` | `promotional` | `suspicious`,
  - To keep the experiment simple, we will stick to a small set of common E-mail types,
- Extract Actions - `{verb, subject, deadline | null}`,
  - Actions in the E-mail that requires human attention,
- Decide Next Step - `reply` | `no_action` | `flag_for_human`,
  - `reply` - The agent has enough information or don't require additional, and can reply directly to the user,
  - `no_action` - No action from the agent. Either the E-mail does not require one (CC'd E-mails, certain promotional E-mails, automated notifications) or the agent leaves it in the inbox because the user may plausibly want to see it. Default when the agent isn't sure.
  - `flag_for_human` - Needs doing, but the agent can't do it - either out of scope (action in another system, e.g. paying) or missing information (availability),
- Draft Reply - text, only if decide == reply,

## Setup

Primary

1. ReAct loop, local Qwen3.5 9B,
2. Graph workflow, local Qwen3.5 9B,

Ceiling

3. ReAct loop, frontier (serves as the ceiling),

Addendum
4. Run voting, ReAct loop, local 9B, many times + voting.

## Dataset

Synthesized E-mails with hand labels for actions and action items with the following attributes,

- Number of E-mails: 40,
  - 30% promotional / fyi (early exit),
    - `no_action` as the target instead of summarizing for the user to keep the experiment simple. Summarizing these E-mails as a part of batch is left for future works,
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

- **Mean per-run accuracy** - the actions are measured against E-mail labels for each run and averaged,
- **Agreement** - the agreement rate across runs, i.e. take the most common answer, how many runs match it?
- **Cost** - number of tokens, wall-clock, peak RAM.
- **Draft quality** - Semantic quality of the output is out of scope for now to keep the experiment deterministically score-able. 

## Caveats

- Temperature - at 0, all setups may be consistent (minus hardware-related drift). At 1.0, loop may destabilize in a way that overstates the effect. Need to test across different temperatures,
  - One perk with graphical workflows is that temperature can be set at a finer granularity. For this experiment, we are deliberately not exercising it so comparison stays clean,
- To keep the experiment simple, images and attachments are out of scope.

## Future Work

- Who reads E-mails one at a time? You'd probably want an agent that pulls the latest unread E-mails and process them together. I imagine this is even worse for local models + ReAct style loops because of how much bigger the context gets.