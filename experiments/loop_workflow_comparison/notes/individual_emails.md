# Analysis on the Individual Emails Dataset

## Specific Datapoints

There may be some bad data in the mix, so I'm ignoring any E-mails where both methods got the answer wrong for now, though analysis on those may yield interesting insights.

### e005 - Promotion (Loop Wrong vs. Graph Right)

(This is an example of the types of E-mail one should not send, the wordiness, holy cow)

#### Background

Loop response: `flag_for_human({decide, whether to tell Amir that pricing appendix will be included in final deck or sent separately})`

Loop reason: 

> This is an internal work email from Carla to Shannon about a client note and deck appendix. The key question at the end is whether Carla should tell Amir that the pricing appendix will be included in the final deck or held back separately. Looking at this, I need to determine what action is needed: - This appears to be between colleagues (Carla and Shannon) - There's a decision being made about how to handle client communication regarding pricing information - The email ends with "should I tell Amir..." - Carla is asking for guidance Since there isn't enough context here to make the right call on whether to include or exclude the pricing appendix, and this seems like it needs human judgment based on company policy or prior decisions about pricing sensitivity, I should flag this for human handling. The action needed would be to provide a yes/no answer to Carla's question about including the pricing appendix in the final deck.

Graph response: `reply(Yes, tell him it will be included in the final deck. This aligns with our decision to keep the pricing appendix so procurement can see assumptions in one file. You're good to send the note now.)`

#### Analysis

- We're making a simplifying assumption that the notes are correct,
- The loop seems to have skipped over the notes completely - there's no mentioning of the notes in the loop, while the graph seems to have taken it into account properly,

Q: Did the loop get the notes properly?
A: No, the LLM did not call `get_notes` at all. The graph forces a gather context step (though no cost constraints, so it may have affinity toward making the calls), whereas the loop is only instructed via language (would explicit instructions to MCP servers help?).

Based on the small sample, it seems that loop favors `flag_for_human` more than graph - 15 FP for loop (really 3 E-mails repeated 5 times) vs 0 for graph.

[Counts by class and category](individual_emails/temp1.0-matrix-over-class.png)

Q: Does loop *ever* call get_notes to respond?
A: Yes! The immediate next data point (e011) it has an example of the loop agent fetching context.

> This email from Rachel Kim needs a response - she's asking which wording to use for a customer quote (\"less than a week\" vs \"under ten days\"). She mentions that comms already settled this on their side, but wants confirmation before design can proceed. I need to determine the correct phrase or ask her what was decided previously.\n\nSince Rachel is directly asking me (Shannon) which wording should be used and says they've already settled it on our side, I should check if there's any context in my notes about this decision, or reply with a question seeking clarification since I don't have the information to answer definitively.\n\nLet me first check my notes for any relevant policies or previous decisions:

So for this particular instance, it may just be an example of inconsistently following that specific piece of instruction.


### e025 - Multi-Ask (Loop Wrong vs. Graph Right)

The behavior is exactly the same as e005. The loop agent responded with `flag_for_human` while the graph agent correctly `reply`'d with information from notes, and the two diverged because the loop agent did not check notes.

### e031 - Buried (Loop Wrong vs. Graph Right)

Same as e005 and e025, loop agent skipped context gathering.

### e036 - Buried (Loop Right vs. Graph Wrong)

The E-mail has an explicit ask: "Can you name the owner for the Saturday escalation shift by 11:00 ET today?".

The loop landing on `flag_for_human`, which is the golden label, and graph is landing on `reply` with the following message,

```
I will approve the escalation owner for the Saturday morning shift (08:00 - 12:00 ET) by 11:00 today as requested. Please let me know who you would like to assign, or I can select someone from the team if needed once we confirm availability outside of those already committed.
```

Q: Where is the "approve the escalation owner" coming from?
A: Maybe the note `2026-07-29] Customer confirmed Saturday go-live, but escalation owner still needs approval.` confused the agent? Did the agent just get lucky by not fetching the note?

### e038 - Suspicious (Loop Wrong, Graph Right)

The for both is to raise `flag_for_human` if unsure, and not leave it alone.

### Summary

The theme across all of the discrepancies, with the exception of e038, seem to be the loop's bias to skip the instruction to not fetch additional notes - the graph's eagerness to fetch additional notes liked caused it to be confused in e036.

A re-run after minimizing the bias may yield different results.

## What about temp=0.6?

All of the metrics drop across the board when accuracy metrics are compared,

N=40 emails, 5 runs each, qwen3.5-9b, no thinking, ctx 32768.

| Metric | Loop @ 0.0 | Graph @ 0.0 | Loop @ 0.6 | Graph @ 0.6 |
| --- | --- | --- | --- | --- |
| correct / emails | 0.825 (33.0/40) | 0.900 (36.0/40) | 0.730 (29.2/40) | 0.865 (34.6/40) |
| correct / decided | 0.825 (33.0/40.0) | 0.923 (36.0/39.0) | 0.749 (29.2/39.0) | 0.901 (34.6/38.4) |
| majority_correct | 0.825 (33/40) | 0.900 (36/40) | 0.775 (31/40) | 0.950 (38/40) |
| pass^1 | 0.825 | 0.900 | 0.730 | 0.865 |
| pass^2 | 0.825 | 0.900 | 0.610 | 0.770 |
| pass^3 | 0.825 | 0.900 | 0.535 | 0.688 |
| pass^4 | 0.825 | 0.900 | 0.485 | 0.615 |
| pass^5 | 0.825 | 0.900 | 0.450 | 0.550 |
| errors | 0 | 5 (provider_error) | 5 (no_route_called) | 8 (5 no_route_called, 3 provider_error) |
| reply precision | 0.917 | 0.824 | 0.808 | 0.859 |
| reply recall | 0.786 | 1.000 | 0.600 | 0.871 |
| reply f1 | 0.846 | 0.903 | 0.689 | 0.865 |
| no_action precision | 0.700 | 0.778 | 0.625 | 0.688 |
| no_action recall | 1.000 | 1.000 | 1.000 | 0.943 |
| no_action f1 | 0.824 | 0.875 | 0.769 | 0.795 |
| flag_for_human precision | 0.833 | 1.000 | 0.711 | 0.888 |
| flag_for_human recall | 0.789 | 0.789 | 0.726 | 0.832 |
| flag_for_human f1 | 0.811 | 0.882 | 0.719 | 0.859 |

Graph is consistently better than loop, but only slightly. Besides the graph encoding execution in code as having an advantage, are there any other confounding factors I'm not taking into account?

## Miscellaneous Thoughts

- The loop always uses one iteration just to get the new E-mail, whereas it's a few lines of Python (cheaper, faster). So the inference budget isn't even, and graph gets that for free (w.r.t. tokens, peak KV, and wall clock time),
- The graph-based approach can expose a subset of tools easily, whereas the loop-based approach needs careful tool search design to provide the model a right set of tools to choose from,
- `get_notes` increases token usage (bringing notes into context), we want to maximize accuracy while minimize token usage, graph the two together instead of separately,