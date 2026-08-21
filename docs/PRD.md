# Lean Intelligent Local Agent

## Overview

Current agent harnesses (e.g. Hermes, OpenClaw) encode skills as natural language instructions interpreted turn-by-turn by an LLM. While the setup lowers the barrier to entry for both technical and non-technical users alike to experiment with agents, the setup has many issues that affect the usability of such agents,

**Reliability** - Executions are non-deterministic, and there is no way to guarantee a job executes the same way twice,
**Brittle** - Small wording changes or model weight updates could silently alter behavior,
**Observability** - When a multi-step task fails, the user must reconstruct what happened by reading chat transcripts,
**Guardrails** - LLM has discretion over control flow at every step, making it difficult to enforce hard constraints which are crucial for high-stakes workflows,
**Efficiency** - Re-deriving control flow from natural language on every run means the LLM re-reasons about "what to do next" even for well-understood, repeatable tasks.

These dimensions place a ceiling on how useful the existing tools are, with natural language instructions at the core of the issues, making these harnesses hard to trust for anything beyond experimentation and hobby projects.

### High-Level Solution

A graph-first agent harness where control flow is an explicit artifact, and every point of model discretion is bounded by construction and visible.

**Skills** are versioned graphs that the user can read, diff, and reason about. Control flow lives in deterministic artifact rather than being re-derived from natural language on each run.

**Nodes** are the primitives. API/MCP call, LLM call, sub-skill invocation, parallel, and wait (for human, for time, etc).

**Memory** Hierarchical and different types of memory accessible via skills to enable ad-hoc querying and minimize context size.

### High-Level UX

Users install LILA on their computer that meets a minimum spec requirement (e.g. 16GB of RAM) and can immediately access a set of pre-defined, well-tested skills that they can customize.

Advanced users with more robust hardware can author their own skills with the provided testing infrastructure, as well as optionally a more powerful local model.

### Tenet

**Focused problems**. Instead of optimizing for generalization, LILA operates on well-tailored and well-evaluated graph-based skills.

**Consistency**. LILA operates consistently by providing tooling to optimize for repeatability, so that it can run day-in and day-out.

**Efficient**. LILA should be able to operate on small language models as a baseline for daily tasks, only requiring more robust (local) models for special occasions like skill authoring.

**Easy-to-use**. One-click install, battery included, everything local. 

## Goals

### Immediate

**Minimally viable authoring experience** - graph-based skills can be authored by technical users familiar with the system.

**High-confidence skills** - skill authors could instill skill users confidence by developing robust evaluations around the skills, using tools provided by this project.

**Bounded execution** - a loop is just graph, so loops are still supported. But each loop declares a maximum iteration count and has clear exit condition.

**Inspect-able control flow** - every skill is represented as an versioned, explicit artifact a user can read, diff, and reason about without reading a chat transcript. When failures occur, it is localized to a specific node on the graph with explicit inputs and outputs.

**Hard guardrails** - default nodes to enforce acquisition of human reviews or approvals, instead of suggestions via prompt.

**Reusable and composable skills** - graphs can be built from smaller subgraphs instead of needing to be rebuilt from the ground up each time.

**Consistently useful** - the system prioritizes depth and reliability over broad general-purpose flexibility. It should solve a narrow set of high-value problems day-in-and-day-out instead of all problems, some days.

**Secure and trust-worthy** - executions are explicit and auditable, potentially harmful operations are sandboxed to limit impact. Dry-run of skills can give users a sense of what will happen without causing havoc.

**Runs well on an <8B model** - Natural-language harnesses like Hermes struggle to plan and sequence actions reliably on small models. Here, graphs own the control flow and routing by default, so the LLM only needs to perform a narrow, well-scoped task rather than open-ended agentic reasoning, which maximizes capability per token/dollar of compute.

### Deferred / Vision

**Self-improvement** - the framework will not learn and personalize skills based on feedback in conversations in V1 but will be explored in later versions.

**Robust skill authoring with visual builder** - graph definition starts in config and code, visual builder will be explored in later versions.

**Exploration mode** - ReAct style agent loops are generalized skills that will be explored in future versions.

**Message platform integration** - the agent will only be reachable directly via API for initial launch. However, message platform integration (e.g. iMessage, Slack, Discord, etc) will need to be explored to make interaction with the agent frictionless.

**Multi-user support** - support for acting on behalf of different users to browse the internet, take actions against APIs is not in scope for V1 but will be explored in later versions.

### Non-Goals

**Solving model reliability** - the framework solves deterministic orchestration (*given the same input), not deterministic generation. An LLM (or any model) call within a single node may still be indeterministic.

**Skill marketplace** - skills will be contained in disparate Github repos and the ecosystem will require word of mouth to bootstrap.

## Risks

### Can a lean model perform judgement well enough to be running daily?

There are still uncertainty around how well 9B models generalize, though this may become a less significant risk by the day as small models are becoming more and more capable.

### Will an author tolerate the cost of writing a graph instead of a paragraph of prose?

In the short term, the repo maintainer will author targeted skills to bootstrap LILA. 

The long term goal around authoring is to provide a verification point, i.e. the dry-run mechanism, and allow users with more robust hardware to run larger models (e.g. 27-30B models) to generate skills against natural language prose.

The dry-run mechanism only proves that the skill runs. The skill authors who wish to share their skills (e.g. with coworkers, family, etc) and demonstrate their robustness can add evaluation data alongside their skills.

### Will a user find the harness too rigid in its interaction?

Need to separate interaction from execution. Interaction => natural language = fluid.
