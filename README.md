# Lean Intelligent Local Agent (LILA)

Current agent harnesses such as Hermes and OpenClaw encode skills as natural language instructions interpreted turn-by-turn by an LLM (aka "loops"). It lowers the barrier to entry for both technical and non-technical users alike to experiment with agents, but it requires a large model to re-derive control flow on every run: the same job can execute differently when repeated, small wording changes silently alter behavior, and debugging a failed multi-step task means reading a chat transcript.

LILA is a graph-first harness that makes the control flow explicit. Nodes do one job, edges decide what happens next. A narrow job and minimal context is a task a small model can handle, a full agent loop is not.

LILA runs locally, not just your state, but the model as well. Your conversations stay on your machine instead of someone else's. Target hardware is a MacBook with 16GB of unified memory, or a GPU with 11GB of VRAM.

**Why not LILA?**

Graph-first also means LILA is biased toward repeatable tasks, not open-ended exploration - open-ended research, one-off tasks where defining a graph costs more than just asking, anything needing a frontier model's judgement in a single shot.