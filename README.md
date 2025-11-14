# Build an Agent with Ranger (Step/Tool DX Guide)

This guide shows you how to build production-grade agents with **zero orchestration code**. You write tiny Python functions, declare what each one **reads** and **writes** in a shared **State**, and call `Agent.run(...)`. The engine figures out *when* to run each unit, runs safe work in parallel, and stops when your **Goal** is true.

> ## DX vocabulary
>
> * **Step** — a **pure transform** over State (no side effects).
> * **Tool** — an **action** that may have side effects (CLI/API/LLM/Human).
> * **Goal** — a predicate over State that means “we’re done.”
> * **Agent** — runs ready Steps/Tools until the Goal is satisfied.
> * **Decorators: `@llm` (LLM Tool), `@human` (review/approval Tool).
> * **Param names: prefer **`inputs` / `outputs`** (we also accept `uses` / `updates` as aliases).

---

## Quickstart (CLI-first)

Once Ranger is installed (`pip install -e .` during development), the `ranger` CLI helps you get moving quickly:

```bash
ranger init demo-agent            # scaffold a new agent package + smoke test
ranger trace ranger.db --domain demo --limit 20  # inspect memory atoms
ranger scenario ranger.db --domain demo --json   # replay coverage + goal checks
ranger visualize agents.testwriter.agent:TestWriterAgent --repo . --format svg  # requires `pip install ranger[viz]`
ranger visualize agents.deep_research.agent:DeepResearchAgent --repo . --format svg  # visualize new research agent
```

The scaffold mirrors the conventions used by the bundled test-writer agent and configures memory/LLM regions via `boot.py`. Run `ranger --help` to explore all commands. Extended documentation lives under [`docs/`](docs/README.md).

---

### Agent runtime scaffold

`agents.common.AgentRuntime` centralises memory setup, registry resets, and scenario utilities so your facades stay tiny. Supply your capability list and budget once, then call `run_agent(...)` when you need to execute:

```python
from agents.common import AgentRuntime
from boot import get_default_budget, setup_openai_llm
from core.llm.provider import register_llm_profile
from core.plan import plan, action


class MyAgent(AgentRuntime):
    def __init__(self, **runtime_options):
        super().__init__(
            budget=get_default_budget(),
            memory_key="myagent.memory",
            memory_domain="myagent",
            db_filename="myagent.db",
            **runtime_options,
        )

    def build_plan(self):
        register_llm_profile(
            "myagent.generate",
            region_key="myagent.llm",
            defaults={"model": "gpt-4o-mini", "temperature": 0.0},
            region_factory=lambda: setup_openai_llm(
                key="myagent.llm",
                model="gpt-4o-mini",
                temperature=0.0,
            ),
        )
        stages = [...]
        return plan(*[action(cap) for cap in stages])

    def run(self, *, max_steps: int = 120):
        return self.run_agent(
            initial={"myagent.config": {...}},
            goal=capabilities.my_goal,
            max_steps=max_steps,
        )
```

Guard regions can be passed via the `guard_regions=` argument, and `scenario_report()` / `scenario_timeline()` come for free.

Pass `visualize=True` (or a custom `Path`) to `run()` when you want the runtime to emit a capability graph automatically.

Forward runtime options like `repo_root=Path("/workspace")` or `auto_visualize=True` when instantiating your agent; the base runtime handles filesystem prep and Graphviz integration on your behalf.

---

### Why the framework is shaped this way

- Declarative I/O keeps capability code easy to audit: every function lists the state keys it touches, so reviewers can reason about side effects without hunting through control flow.
- Runtime planning replaces hand-written orchestration: the engine diff-checks snapshots, activates just the capabilities that need to run, and parallelises batches safely.
- State lives in one place: replaying `.ranger/*.db` through `ScenarioHarness` gives deterministic debugging, coverage checks, and regression playback.
- Facades stay minimal: `AgentRuntime` handles memory registration, guardrails, and visualisation so you focus on domain logic, not boilerplate.
- Extensibility follows composition: plug additional capabilities into the list or register new regions in `configure_runtime()` without rewriting the core loop.

---

### Plan builder (canonical composition path)

- Use `core.plan.plan` to compose capabilities instead of hand-maintained lists: `test_plan = plan(action(cap1)) >> action(cap2)`.
- Plans compile directly into agents (`test_plan.compile()`) and integrate with `AgentRuntime` via `build_plan()`.
- Any inputs not satisfied by upstream outputs are surfaced as dangling requirements so you can seed them with initial state.
- Plans operate on plain capabilities, so existing decorators (`@step`, `@tool`, etc.) continue to work unchanged.
- Mark required initial keys per action via `action(cap, requires_initial={"repo.root"})` and call `plan.describe()` or `plan.validate()` to audit missing inputs before runtime.
- See `docs/API_REFERENCE.md` for a concise API map.

### LLM profiles

- Register shared model settings once: `register_llm_profile("myagent.generate", region_key="myagent.llm", defaults={"model": "gpt-4o-mini"}, region_factory=lambda: setup_openai_llm(...))`.
- Reference the profile in capabilities with `@llm(profile="myagent.generate", inputs=[...], outputs=[...])`—no more manual runner mutation.
- Profiles can declare `region_factory` so the required region is auto-registered the first time it’s used, keeping façade code trivial.

---

# 1) Mental model in 90 seconds

* The **State** is a typed key-value map, e.g. `{"repo.ast": ..., "tests.gen": ...}`.
* A Step/Tool declares what it **inputs** (reads) and **outputs** (writes). It cannot write anything else.
* Readiness: a unit becomes **ready** when all its `inputs` exist **and** either:

  1. any of its `outputs` are missing, **or**
  2. the values of its `inputs` changed since it last ran.
* The Agent picks a batch of ready units with **disjoint outputs** (no two write the same key) and applies them (safe parallelism).
* **No loops.** As State changes, downstream units become ready again. ReAct, retries, and repair **emerge from the data**.

```mermaid
flowchart TD
  S["Snapshot(State)"] --> R["Find Ready (inputs exist & outputs missing/changed)"]
  R -->|none| B["Blocked → WhyNot(missing_for_goal)"]
  R -->|some| P["Pick Compatible Batch (no overlapping outputs)"]
  P --> A["Apply Units"]
  A --> V["Verify & Commit (CAS)"]
  V --> C{"Goal True?"}
  C -- No --> S
  C -- Yes --> D["Done"]
```

---

### Inputs & outputs in practice

- `inputs=[...]` lists **state keys your capability must read** before it can run. When you specify more than one key (e.g. `inputs=["repo.root", "module.index"]`), the engine waits until *all* of them exist in the snapshot.
- `outputs=[...]` names every key your capability promises to write. Multiple outputs mean the returned dict must contain each key; leaving one out fails validation.
- Treat keys as namespaced facts: short dotted paths such as `tests.plan.todo` or `research.summary` keep state readable and avoid collisions.
- You can emit derived data without mutating inputs—return a new dict with just your outputs. Ranger merges it back into the shared snapshot atomically.
- Optional data flows stay declarative: produce flags like `{"tests.run": True}` and let downstream steps declare that flag as an input so the planner tees up the right work.

---

# 2) Minimal API you’ll use

```python
from core.sdk import step, tool, llm, human, goal, Agent
```

- `@step(inputs=[...], outputs=[...])` returns a dict of new state; use it for deterministic transforms, parsing, indexing, and scoring.
- `@tool(inputs=[...], outputs=[...])` lets you call subprocesses, HTTP APIs, or mutate the filesystem—anything with side effects belongs here.
- `@llm(...)` wraps `@tool` with prompting helpers, schema validation, retry semantics, and optional provider wiring.
- `@human(...)` renders a form in the UI so people can approve, edit, or supply gating information without stopping the engine.
- `@goal(scope=[...])` inspects state after every batch; return `True` when you are done or raise `GoalBlocked(...)` with machine-readable details.
- `Agent(capabilities).run(...)` drives execution; in practice you call `AgentRuntime.run_agent(...)` so memory, budgets, and reports are uniform.

> **Aliases:** `uses/updates` are accepted everywhere as synonyms for `inputs/outputs`.

---

# 3) Hello, Agent (5 minutes)

```python
from core.sdk import step, goal, Agent

@step(inputs=["a"], outputs=["b"])
def plus_one(st):
    return {"b": st["a"] + 1}

@step(inputs=["b"], outputs=["c"])
def times_two(st):
    return {"c": st["b"] * 2}

@goal(scope={"c"})
def done(st):
    return st.get("c") == 4

agent = Agent([plus_one, times_two])
res = agent.run(initial={"a": 1}, goal=done, max_steps=10)
assert res.ok and res.final.value("c") == 4
```

**No orchestration.** The Agent sees `a`, runs `plus_one` → makes `b`; then `times_two` becomes ready, writes `c`; the Goal passes.

---

# 4) ReAct in \~25 lines (Reason → Act → Observe → Refine)

```python
from core.sdk import step, tool, llm, goal, Agent

# REASON: pick next action based on observations
@llm(
  inputs=["question","obs"],
  outputs=["thought"],
  system="Return JSON {thought:str} with either 'search' or 'answer'.",
  template='{"thought":"{{ "search" if (obs|length)<2 else "answer" }}"}',
  schema={"type":"object","properties":{"thought":{"type":"string"}},"required":["thought"]},
  provider=my_llm_provider,
)
def think(st): pass

# PLAN: craft a query when we need search (pure)
@step(inputs=["question","thought"], outputs=["query"])
def plan(st):
    return {"query": f"{st['question']} key facts"} if st["thought"] == "search" else {"query": ""}

# ACT: perform the search (side effect → Tool)
@tool(inputs=["query","obs"], outputs=["obs"])
def search(st):
    q = st["query"]; obs = st.get("obs", [])
    return {"obs": obs + ([{"source":"stub","snippet":f"Result:{q}"}] if q else [])}

# WRITE: synthesize a draft answer
@llm(
  inputs=["question","obs"],
  outputs=["answer.draft"],
  system="Return JSON {text:str, citations:list}.",
  template='{"text":"Answer to {{question}} with {{obs|length}} observations.","citations":[]}',
  schema={"type":"object","properties":{"text":{"type":"string"},"citations":{"type":"array"}},"required":["text","citations"]},
  provider=my_llm_provider,
)
def write(st): pass

@goal(scope={"answer.draft"})
def done(st): return "answer.draft" in st

Agent([think, plan, search, write]).run(initial={"question":"What is ReAct?"}, goal=done)
```

```mermaid
sequenceDiagram
  participant S as State
  participant Think as LLM(think)
  participant Plan as Step(plan)
  participant Search as Tool(search)
  participant Write as LLM(write)

  Note over S: question set, obs empty
  Think->>S: outputs thought="search"
  Plan->>S: outputs query="what is react key facts"
  Search->>S: outputs obs+=[snippet]
  Think->>S: now thought="answer"
  Write->>S: outputs answer.draft
```

---

# 5) A Test-Writer

### 5.1 State keys (convention)

* `repo.root: str` – project path
* `repo.ast: dict` – AST index (files/functions/classes)
* `tests.gen: dict` – generated tests `{files:[{path,content}]}`
* `tests.on_disk: bool` – were tests written to filesystem
* `run.result: dict` – pytest result `{passed, return_code, stdout, stderr, failed}`
* `review.status: str` – `"approved"` / `"changes-requested"`

### 5.2 Steps & Tools

```python
from core.sdk import step, tool, llm, human, goal, Agent

# Build a minimal AST index (pure Step)
@step(inputs=["repo.root"], outputs=["repo.ast"])
def build_ast(st):
    import ast, os
    root = st["repo.root"]; files_idx = []
    for dirpath, _, files in os.walk(root):
        for f in files:
            if f.endswith(".py") and not f.startswith("_"):
                p = os.path.join(dirpath, f)
                try:
                    tree = ast.parse(open(p, "r", encoding="utf-8").read())
                    funcs  = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and not n.name.startswith("_")]
                    classes= [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and not n.name.startswith("_")]
                    if funcs or classes:
                        files_idx.append({"path": p, "functions": funcs, "classes": classes})
                except Exception:
                    pass
    return {"repo.ast": {"files": files_idx}}

# Generate tests from AST (LLM Tool)
@llm(
  inputs=["repo.ast"], outputs=["tests.gen"],
  system="You are a senior Python test engineer. Return JSON {files:[{path,content}]}",
  template="path/to/gen_tests_prompt.jinja",
  schema={"type":"object","properties":{"files":{"type":"array"}},"required":["files"]},
  provider=my_llm_provider,
)
def generate_tests(st): pass

# Optional human review (non-blocking Tool)
@human(
  inputs=["tests.gen"], outputs=["review.status","tests.gen"],
  title="Review generated tests", description="Approve/suggest edits",
  fields=[{"name":"approve","type":"bool"},{"name":"comment","type":"text"}]
)
def review(sub, st):
    if sub.get("approve"): return {"review.status":"approved"}
    if "files" in sub:     return {"review.status":"changes-applied","tests.gen":{"files": sub["files"]}}
    return {"review.status":"needs-work"}

# Write tests to disk (Tool)
@tool(inputs=["tests.gen","repo.root"], outputs=["tests.on_disk"])
def write_to_disk(st):
    import os
    out = os.path.join(st["repo.root"], "tests_generated")
    os.makedirs(out, exist_ok=True)
    for f in st["tests.gen"]["files"]:
        path = os.path.join(out, os.path.basename(f["path"]).replace(".py","_test.py"))
        with open(path, "w", encoding="utf-8") as w: w.write(f["content"])
    return {"tests.on_disk": True}

# Run pytest (Tool)
@tool(inputs=["repo.root","tests.on_disk"], outputs=["run.result"])
def run_pytest(st):
    import subprocess
    p = subprocess.run(["python","-m","pytest","-q","--maxfail=1","--disable-warnings"],
                       cwd=st["repo.root"], capture_output=True, text=True)
    return {"run.result": {
        "passed": p.returncode == 0, "return_code": p.returncode,
        "stdout": p.stdout[-2000:], "stderr": p.stderr[-2000:], "failed": []
    }}

# Repair tests with LLM (Tool): writes updated tests.gen
@llm(
  inputs=["run.result","tests.gen"], outputs=["tests.gen"],
  system="Repair failing pytest tests. Return JSON {files:[{path,content}]}",
  template="path/to/repair_tests_prompt.jinja",
  schema={"type":"object","properties":{"files":{"type":"array"}},"required":["files"]},
  provider=my_llm_provider,
)
def repair(st): pass

@goal(scope={"run.result"})
def tests_passing(st):
    return st.get("run.result", {}).get("passed") is True

agent = Agent([build_ast, generate_tests, review, write_to_disk, run_pytest, repair])
result = agent.run(initial={"repo.root": "./your_project"}, goal=tests_passing, max_steps=40)
```

```mermaid
graph LR
  subgraph "Data (State)"
    A[repo.ast]; B[tests.gen]; C[tests.on_disk]; D[run.result]
  end
  subgraph "Units"
    S1[Step: build_ast]; T1[LLM: generate_tests]; H1[Human: review]
    T2[Tool: write_to_disk]; T3[Tool: run_pytest]; L1[LLM: repair]
  end
  S1 --> A
  A --> T1 --> B
  B --> H1 --> B
  B --> T2 --> C --> T3 --> D --> L1 --> B
```

**Why it loops by itself:** if `run_pytest` fails, `repair` becomes ready (it **inputs** `run.result`), updates `tests.gen`, which makes `write_to_disk` and then `run_pytest` ready again—until tests pass.

---

### Deep Research Agent (Firecrawl + citations)

The `agents/deep_research` package applies the same topology patterns to autonomous, citation-rich research. Highlights:

- `capture_request` normalizes the topic + configuration into a durable request artifact.
- `design_research_plan` and `synthesize_notes` lean on the LLM region (with offline fallbacks) to craft outlines and structured findings.
- `gather_sources` integrates with Firecrawl (or synthetic stand-ins) to fetch web intelligence, persisting atoms for replay.
- `draft_report` assembles a multi-section, 15+ page briefing with guaranteed citation density; `ensure_feedback` / `human_review` keep a human-in-the-loop hook available.
- `finalize_report`, `persist_report`, and `summarize_execution` deliver the final document, metrics, and provenance.

Run it offline or with real API keys:

```bash
export FIRECRAWL_KEY=sk-...
python -m agents.deep_research.agent --topic "Long-term outlook for quantum sensing"
```

Visualize the capability graph after installing the `viz` extra:

```bash
ranger visualize agents.deep_research.agent:DeepResearchAgent --repo . --format svg
```

---

# 6) How the engine decides what runs (at a glance)

```mermaid
classDiagram
  class Agent {
    +run(initial, goal, max_steps) SolveResult
  }
  class Goal {
    +scope: set[str]
    +__call__(Snapshot)->bool
  }
  class StepTool {
    +id
    +inputs: set[str]
    +outputs: set[str]
    +kind: "step"|"tool"|"llm"|"human"
    +run(Snapshot)->dict
  }
  class State {
    +snapshot()->Snapshot
    +cas_commit(delta, provenance)->Snapshot
  }
  Agent --> Goal
  Agent --> StepTool
  Agent --> State
```

* **Snapshot semantics:** every unit sees a consistent view for its run.
* **Write locality:** a unit can only write declared `outputs` (fail-fast otherwise).
* **Compatible batch:** no overlapping `outputs` in the same step → safe parallelism.
* **Determinism:** snapshot digests detect no-progress; runs are reproducible.

---

# 7) LLM providers (OpenAI today, anything tomorrow)

`@llm` takes a `provider` so you can swap vendors easily. Keep your `OpenAIAdapter`; add a tiny shim:

```python
# providers/openai_provider.py
from core.llm.provider import LLMProvider

class OpenAIProvider(LLMProvider):
    def __init__(self, adapter): self.adapter = adapter
    def generate(self, *, system, prompt, model, temperature=None, max_tokens=None, schema=None) -> str:
        messages = ([{"role":"system","content":system}] if system else []) + [{"role":"user","content":prompt}]
        return self.adapter.chat(messages=messages, model=model, force_json=bool(schema),
                                 max_tokens=max_tokens or 1500, temperature=temperature or 0.2,
                                 task_description="ranger.llm")
```

Use it:

```python
from providers.openai_provider import OpenAIProvider
from core.llm.openai_adapter import OpenAIAdapter

my_llm_provider = OpenAIProvider(OpenAIAdapter())
```

Swapping to Anthropic/Groq/local = replace that `provider=`.

---

# 8) Debugging & guarantees (you get these “for free”)

* **Deterministic steps:** consistent snapshots; same inputs → same outputs (Steps).
* **Parallel-safe:** units with disjoint outputs run together.
* **No hidden writes:** undeclared writes → clear error with offending keys.
* **Why-not proofs:** if nothing’s ready, you get `WhyNot("no_ready", missing=[...])` based on the Goal’s scope.
* **No-progress guard:** identical snapshot digest → `WhyNot("no_progress")` prevents thrash.
* **Human is non-blocking:** `@human` can be pending while unrelated work proceeds.

**Common fixes**

* Missing key? Add an **adapter Step** that derives it from what you already have.
* Two units append to the same list? Configure a **merge** strategy for that key.

---

# 9) Patterns & anti-patterns

**Do**

* Keep units tiny and **single-purpose** (easy to test & reuse).
* Validate LLM outputs with **JSON schema**; store **digests** for reproducibility.
* Make repair Tools **idempotent**: same inputs → same edits.

**Avoid**

* Hidden global state; everything should flow through State.
* Forcing order unless you must; the engine’s evidence-first policy is usually best.
* Long blocking Tools; split into smaller ones and let readiness drive.

---

# 10) Copy-paste checklist for a new agent

1. **List State keys** you care about (inputs & outputs).
2. Write **Steps** for pure transforms; **Tools** for side effects (CLI/API/LLM/Human).
3. If using LLMs, use `@llm(..., provider=...)` with a JSON schema.
4. Add `@human` if you need review/approval (non-blocking).
5. Write a **Goal** checking the terminal condition.
6. `Agent([...]).run(initial=..., goal=..., max_steps=...)`.
7. Test with tiny fixtures; mock the LLM provider; simulate human submissions.

---

# 6) Project Structure

```
ranger/
├── core/                           # Core framework
│   ├── __init__.py                 # Package initialization and version
│   ├── sdk.py                      # Main user API (@step, @tool, @llm, @human, @goal, Agent)
│   ├── engine.py                   # Topological execution engine
│   ├── capability.py               # Capability definitions and runners
│   ├── workspace.py                # Immutable state management (Snapshot, Workspace)
│   ├── merge.py                    # Write specifications and merge modes
│   ├── errors.py                   # Error types (SolveResult, WhyNot)
│   ├── validate.py                 # Value validation and type checking
│   ├── hints.py                    # Type hints and validation helpers
│   ├── provenance.py               # Execution provenance tracking
│   ├── llm/                        # Language model integration
│   │   ├── __init__.py
│   │   └── provider.py             # LLM provider protocol and implementations
│   ├── runners/                    # Execution runners
│   │   ├── __init__.py
│   │   ├── python_runner.py        # Pure Python function execution
│   │   ├── llm_runner.py           # LLM call execution
│   │   └── human_runner.py         # Human interaction execution
│   └── observe/                    # Observability and logging
│       ├── __init__.py
│       └── bus.py                  # Event bus for observability
├── agents/                         # Example agents
│   └── testwriter/                 # Test generation agent
│       ├── __init__.py
│       ├── agent.py                # Facade wiring capabilities into an Agent
│       ├── capabilities.py         # Steps/Tools orchestrating the pipeline
│       ├── types.py                # Dataclasses for metadata + config
│       └── utils.py                # Helper functions for indexing/scoring
├── tests/                          # Unit tests
│   └── test_validate.py
├── tests_generated/                # Generated tests by testwriter agent
│   ├── conftest.py
│   ├── test_engine_class.py
│   ├── test_engine_core.py
│   ├── test_engine_edge_cases.py
│   └── test_report.json
├── docs/                           # Additional guides
│   ├── README.md
│   ├── AGENT_GUIDE.md
│   ├── API_REFERENCE.md
│   └── LLM_PROVIDER_GUIDE.md
├── README.md                       # This file
└── pyproject.toml                  # Project configuration
```

## Core Module Files

- **`sdk.py`**: Primary user interface with decorators (`@step`, `@tool`, `@llm`, `@human`, `@goal`) and `Agent` class
- **`engine.py`**: Topological execution engine that runs capabilities until goals are met
- **`capability.py`**: Defines `Capability` class and `Runner` protocol for different execution types
- **`workspace.py`**: Immutable state management with `Snapshot` (read-only view) and `Workspace` (mutable store)
- **`merge.py`**: Write specifications and merge modes for handling state updates
- **`errors.py`**: Result types (`SolveResult`, `WhyNot`) for execution outcomes
- **`validate.py`**: Value validation and type checking utilities
- **`hints.py`**: Type hints and validation helpers
- **`provenance.py`**: Execution provenance tracking for debugging and auditing

## Runners Module

- **`python_runner.py`**: Executes pure Python functions (used by `@step`)
- **`llm_runner.py`**: Handles LLM calls with templating and schema validation (used by `@llm`)
- **`human_runner.py`**: Manages human interaction workflows (used by `@human`)

## Agents Module

- **`testwriter/`**: Complete example agent that generates and improves tests
  - **`capabilities.py`**: End-to-end pipeline (index → generate → execute → summarise)
  - **`agent.py`**: Thin façade that exposes a `run()` helper
  - **`types.py` / `utils.py`**: Shared data structures and helper functions

---

That's it. With **Steps** (pure) and **Tools** (actions) over a shared **State**, Ranger gives you orchestration-free agents that are **predictable, parallel, and production-ready**—without making you draw a single graph.
