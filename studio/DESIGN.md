# Ranger Studio: Visual Topological Agent Builder

## Philosophy: State-First, Not Flow-First

Unlike n8n/Zapier (sequential workflows) or AgentKit (tool chains), Ranger Studio embraces **topological orchestration** where execution emerges from state dependencies, not explicit control flow.

---

## Core Differentiators

### 1. **State Space Canvas** (Not Workflow Canvas)
- Users design the **state space** first (what data exists)
- Then add **capabilities** that transform state
- Execution order emerges automatically from read/write dependencies

### 2. **Capability Cards** (Not Step Nodes)
- Each card declares:
  - `reads`: State fields it needs
  - `writes`: State fields it produces
  - `implementation`: Tool, LLM, Python function, or Human input
- No "then" arrows - topology determines execution order

### 3. **Goal-Oriented** (Not Process-Oriented)
- Define success condition: `goal(["output.report", "metrics.quality"])`
- Engine finds path to goal automatically
- No need to wire "IF this THEN that"

### 4. **Morphisms, Not Mutations**
- Visual representation shows state **transformations**
- Each capability is a morphism: `S₁ → S₂`
- Composable, reusable, testable in isolation

---

## Visual Paradigm

```
┌─────────────────────────────────────────────────────────────┐
│  State Space (The Universe)                                 │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ • user.request          (text)                       │  │
│  │ • search.results        (json)                       │  │
│  │ • analysis.summary      (text)                       │  │
│  │ • output.report         (text)                       │  │
│  │ • metrics.quality       (float)                      │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  Capabilities (Morphisms)                                   │
│                                                              │
│  ┌──────────────────────┐   ┌──────────────────────────┐   │
│  │  gather_context      │   │  analyze_results         │   │
│  │  ───────────────     │   │  ────────────────        │   │
│  │  reads:              │   │  reads:                  │   │
│  │    • user.request    │   │    • search.results      │   │
│  │  writes:             │   │  writes:                 │   │
│  │    • search.results  │   │    • analysis.summary    │   │
│  │  type: @tool         │   │  type: @llm              │   │
│  └──────────────────────┘   └──────────────────────────┘   │
│                                                              │
│  ┌──────────────────────┐   ┌──────────────────────────┐   │
│  │  generate_report     │   │  validate_quality        │   │
│  │  ────────────────    │   │  ─────────────────       │   │
│  │  reads:              │   │  reads:                  │   │
│  │    • analysis.summary│   │    • output.report       │   │
│  │  writes:             │   │  writes:                 │   │
│  │    • output.report   │   │    • metrics.quality     │   │
│  │  type: @llm          │   │  type: @step             │   │
│  └──────────────────────┘   └──────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  Goal Definition                                             │
│  ─────────────────────────────────────────────────────────  │
│  Success when: ["output.report", "metrics.quality"] exist   │
│  AND: metrics.quality >= 0.8                                 │
└─────────────────────────────────────────────────────────────┘
```

**Execution emerges automatically:**
```
user.request → gather_context → search.results → analyze_results 
            → analysis.summary → generate_report → output.report 
            → validate_quality → metrics.quality → GOAL ✓
```

---

## UI/UX Components

### 1. **State Space Designer**
```
┌──────────────────────────────────────┐
│  ⊕ Add State Field                   │
├──────────────────────────────────────┤
│  Field Name: user.request            │
│  Type:       ○ text ○ json ○ number  │
│  Required:   ☑ Initial               │
│  Schema:     (optional JSON schema)  │
└──────────────────────────────────────┘
```

### 2. **Capability Builder** (Modal)
```
┌─────────────────────────────────────────────────┐
│  Create Capability: gather_context              │
├─────────────────────────────────────────────────┤
│  Type: ⦿ @step  ○ @tool  ○ @llm  ○ @human       │
│                                                  │
│  Reads From State:                              │
│  ☑ user.request                                 │
│  ☐ search.query                                 │
│                                                  │
│  Writes To State:                               │
│  ☑ search.results                               │
│  ☐ metadata.source                              │
│                                                  │
│  Implementation:                                │
│  ┌─────────────────────────────────────────┐   │
│  │ def gather_context(ws: Snapshot):       │   │
│  │     request = ws.get("user.request")    │   │
│  │     results = search_api(request)       │   │
│  │     return {"search.results": results}  │   │
│  └─────────────────────────────────────────┘   │
│                                                  │
│  [Save Capability] [Test in Isolation]          │
└─────────────────────────────────────────────────┘
```

### 3. **LLM Capability Builder**
```
┌─────────────────────────────────────────────────┐
│  Create LLM Capability: analyze_results         │
├─────────────────────────────────────────────────┤
│  Model:       [gpt-4o-mini ▼]                   │
│  Temperature: [0.2        ]                     │
│                                                  │
│  Reads:       ☑ search.results                  │
│  Writes:      ☑ analysis.summary                │
│                                                  │
│  System Prompt:                                 │
│  ┌─────────────────────────────────────────┐   │
│  │ You are a research analyst. Synthesize  │   │
│  │ search results into key insights.       │   │
│  └─────────────────────────────────────────┘   │
│                                                  │
│  User Template: (Jinja2)                        │
│  ┌─────────────────────────────────────────┐   │
│  │ Analyze these results:                   │   │
│  │ {{ search.results }}                     │   │
│  │                                          │   │
│  │ Provide: executive summary, key points  │   │
│  └─────────────────────────────────────────┘   │
│                                                  │
│  Schema Output: (JSON Schema)                   │
│  ┌─────────────────────────────────────────┐   │
│  │ {                                        │   │
│  │   "type": "object",                      │   │
│  │   "properties": {                        │   │
│  │     "summary": {"type": "string"},       │   │
│  │     "key_points": {"type": "array"}      │   │
│  │   }                                      │   │
│  │ }                                        │   │
│  └─────────────────────────────────────────┘   │
└─────────────────────────────────────────────────┘
```

### 4. **Tool Capability Builder**
```
┌─────────────────────────────────────────────────┐
│  Create Tool Capability: fetch_data             │
├─────────────────────────────────────────────────┤
│  Tool Type:                                      │
│  ⦿ HTTP API Call                                │
│  ○ Database Query                               │
│  ○ File System                                  │
│  ○ Custom Python                                │
│                                                  │
│  API Configuration:                              │
│  Endpoint:  [https://api.example.com/search  ]  │
│  Method:    [GET ▼]                             │
│  Headers:   {"Authorization": "Bearer..."}      │
│                                                  │
│  Map State to Request:                           │
│  query: {{ user.request }}                      │
│                                                  │
│  Map Response to State:                          │
│  search.results: response.data                  │
└─────────────────────────────────────────────────┘
```

### 5. **Goal Designer**
```
┌─────────────────────────────────────────────────┐
│  Define Success Condition                       │
├─────────────────────────────────────────────────┤
│  Goal is met when:                              │
│                                                  │
│  Required Fields:                               │
│  ☑ output.report                                │
│  ☑ metrics.quality                              │
│  ☐ debug.trace                                  │
│                                                  │
│  Custom Validation: (Python)                    │
│  ┌─────────────────────────────────────────┐   │
│  │ def goal_check(ws: Snapshot) -> bool:   │   │
│  │     report = ws.get("output.report")    │   │
│  │     quality = ws.get("metrics.quality") │   │
│  │     return (len(report) > 100           │   │
│  │             and quality >= 0.8)         │   │
│  └─────────────────────────────────────────┘   │
└─────────────────────────────────────────────────┘
```

---

## Unique Features

### 1. **Automatic Dependency Resolution**
- No manual "connect nodes"
- System analyzes reads/writes and creates execution graph
- Shows **computed topology** visually
- Warns about cycles or missing dependencies

### 2. **Topology Visualization**
```
┌────────────────────────────────────────────────┐
│  Computed Execution Graph                      │
│                                                 │
│  user.request                                  │
│       ↓                                        │
│  gather_context (@tool)                        │
│       ↓                                        │
│  search.results                                │
│       ↓                                        │
│  analyze_results (@llm)                        │
│       ↓                                        │
│  analysis.summary                              │
│       ↓                                        │
│  generate_report (@llm)                        │
│       ↓                                        │
│  output.report                                 │
│       ↓                                        │
│  validate_quality (@step)                      │
│       ↓                                        │
│  metrics.quality → GOAL ✓                      │
└────────────────────────────────────────────────┘
```

### 3. **Capability Marketplace**
Pre-built, composable capabilities:
- `@capability/web-scrape` - reads URL → writes HTML
- `@capability/summarize` - reads text → writes summary
- `@capability/extract-json` - reads text → writes structured data
- `@capability/send-email` - reads message → writes confirmation

### 4. **Live Testing**
```
┌────────────────────────────────────────────────┐
│  Test Workspace                                 │
├────────────────────────────────────────────────┤
│  Initial State:                                 │
│  {                                              │
│    "user.request": "Latest AI research"        │
│  }                                              │
│                                                 │
│  [▶ Run Agent]  [Step Through]  [Reset]        │
│                                                 │
│  Execution Trace:                               │
│  ✓ gather_context    (42ms)                    │
│  ✓ analyze_results   (1.2s)                    │
│  ✓ generate_report   (2.1s)                    │
│  ✓ validate_quality  (5ms)                     │
│  ✓ GOAL ACHIEVED                                │
│                                                 │
│  Final State:                                   │
│  {                                              │
│    "output.report": "AI Research Summary...",  │
│    "metrics.quality": 0.92                     │
│  }                                              │
└────────────────────────────────────────────────┘
```

### 5. **Memory Regions** (Visual)
```
┌────────────────────────────────────────────────┐
│  Topology Regions                               │
├────────────────────────────────────────────────┤
│  ⊕ Add Region                                  │
│                                                 │
│  [🗄️ Memory: SQLite]                           │
│  Domain:     research                          │
│  Retention:  7 days                            │
│  Schema:     research.*                        │
│                                                 │
│  [🛡️ Guard: PII Detection]                     │
│  Mode:       mask                              │
│  Patterns:   email, phone, ssn                 │
│                                                 │
│  [🤖 Model: OpenAI GPT-4]                      │
│  Endpoint:   openai.llm                        │
│  Budget:     10K tokens/run                    │
└────────────────────────────────────────────────┘
```

---

## Export Formats

### 1. **Python Code** (For Developers)
```python
from core.sdk import Agent, step, tool, llm, goal
from core.workspace import Snapshot

@tool(inputs=["user.request"], outputs=["search.results"])
def gather_context(ws: Snapshot):
    request = ws.get("user.request")
    results = search_api(request)
    return {"search.results": results}

@llm(
    inputs=["search.results"],
    outputs=["analysis.summary"],
    model="gpt-4o-mini",
    template="Analyze: {{search.results}}"
)
def analyze_results(ws: Snapshot):
    pass

# ... etc
```

### 2. **JSON Schema** (For Storage)
```json
{
  "agent": {
    "name": "research-agent",
    "version": "1.0.0",
    "state_space": {
      "user.request": {"type": "text", "initial": true},
      "search.results": {"type": "json"},
      "analysis.summary": {"type": "text"},
      "output.report": {"type": "text"}
    },
    "capabilities": [
      {
        "id": "gather_context",
        "type": "tool",
        "reads": ["user.request"],
        "writes": ["search.results"],
        "implementation": {
          "language": "python",
          "code": "..."
        }
      }
    ],
    "goal": {
      "scope": ["output.report", "metrics.quality"],
      "validation": "..."
    }
  }
}
```

### 3. **YAML** (Human-Readable)
```yaml
agent:
  name: research-agent
  
state:
  user.request:
    type: text
    initial: true
  search.results:
    type: json
    
capabilities:
  - name: gather_context
    type: tool
    reads: [user.request]
    writes: [search.results]
    
  - name: analyze_results
    type: llm
    reads: [search.results]
    writes: [analysis.summary]
    model: gpt-4o-mini
    
goal:
  when: [output.report, metrics.quality]
  condition: quality >= 0.8
```

---

## Implementation Architecture

```
studio/
├── frontend/                 # React/Vue UI
│   ├── canvas/              # Visual state space
│   ├── builders/            # Capability builders
│   ├── preview/             # Live testing
│   └── export/              # Code generation
│
├── backend/                 # FastAPI server
│   ├── compiler/            # Visual → Ranger code
│   ├── validator/           # Check topology correctness
│   ├── simulator/           # Run agent in sandbox
│   └── marketplace/         # Capability library
│
└── shared/
    ├── schema.json          # Agent definition schema
    └── stdlib/              # Standard capability library
```

---

## Key Innovation: **No Workflows, Only Topology**

| Traditional (n8n/Zapier) | Ranger Studio |
|-------------------------|---------------|
| "When X happens, do Y" | "What state transformations exist?" |
| Sequential steps | Parallel-ready morphisms |
| Manual error handling | Fail-fast by design |
| Complex conditionals | Natural emergence from dependencies |
| Hard to test | Each capability isolated |
| Imperative | Declarative + Functional |

---

## User Journey

### Example: Building a Research Agent

1. **Define State Space**
   - Add: `user.query` (text, initial)
   - Add: `web.results` (json)
   - Add: `summary.text` (text)
   - Add: `final.report` (text)

2. **Add Capabilities** (No specific order!)
   - "Web Search" → reads `user.query`, writes `web.results`
   - "Summarize" → reads `web.results`, writes `summary.text`
   - "Format Report" → reads `summary.text`, writes `final.report`

3. **Define Goal**
   - Success when: `final.report` exists

4. **Preview Topology**
   - System shows: `user.query → Web Search → web.results → Summarize → summary.text → Format Report → final.report → GOAL`

5. **Test & Deploy**
   - Run with test input
   - Export as Python code or deploy to Ranger Cloud

---

## Business Model Opportunities

### Free Tier
- Local execution
- 10 capabilities per agent
- Community marketplace

### Pro Tier
- Cloud execution
- Unlimited capabilities
- Private capability library
- Team collaboration
- Version control

### Enterprise
- Self-hosted
- Custom regions (databases, APIs)
- SSO/RBAC
- SLA guarantees

---

## Why This Wins

1. **Simpler Mental Model**: "What states exist?" vs "What steps to take?"
2. **Automatic Optimization**: Topology finds parallelization opportunities
3. **Testable**: Each capability runs in isolation
4. **Maintainable**: Change reads/writes, topology updates automatically
5. **Professional**: Exports to real code, not black box
6. **Scalable**: Category theory foundation allows formal verification

---

## Next Steps

1. Build visual prototype (Figma)
2. Implement compiler (Visual DSL → Ranger SDK)
3. Create marketplace of 50+ standard capabilities
4. Beta with power users
5. Open source core, monetize hosting/marketplace

