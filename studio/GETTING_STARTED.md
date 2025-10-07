# Getting Started with Ranger Studio

Welcome! This guide will walk you through building your first topological agent.

## What You'll Build

A **Research Assistant** that:
1. Takes a user query
2. Searches the web
3. Analyzes results with AI
4. Generates a formatted report

**Time to complete:** ~10 minutes

---

## Step 1: Open Ranger Studio

```bash
# Option A: Open UI mock locally
cd studio/ui
open canvas.html

# Option B: Run the API server
cd studio
python mock/server.py
# Then visit http://localhost:8080/docs
```

---

## Step 2: Create a New Agent

Click **"New Agent"** and give it a name: `research-assistant`

---

## Step 3: Define Your State Space

Think: "What data needs to exist in this agent?"

Click **"⊕ Add State Field"** four times:

### Field 1: User Input
- **Name:** `user.query`
- **Type:** `text`
- **Initial:** ✓ (This is where the user starts)
- **Description:** "The research question from the user"

### Field 2: Search Results
- **Name:** `search.results`
- **Type:** `json`
- **Initial:** ✗
- **Description:** "Raw web search results"

### Field 3: Analysis
- **Name:** `analysis.summary`
- **Type:** `text`
- **Initial:** ✗
- **Description:** "AI-generated analysis of search results"

### Field 4: Final Report
- **Name:** `report.content`
- **Type:** `text`
- **Initial:** ✗
- **Description:** "The final formatted report"

**Your state space is now defined!** 🎉

---

## Step 4: Add Capabilities

Now add the transformations. Don't worry about order - the system figures that out!

### Capability 1: Web Search

Click **"⊕ Add Capability"**

- **Name:** `search_web`
- **Type:** `@tool` (external action)
- **Reads:** `user.query`
- **Writes:** `search.results`
- **Implementation:**
  ```python
  query = ws.get("user.query")
  results = search_api(query, limit=10)
  return {"search.results": results}
  ```

### Capability 2: Analyze Results

Click **"⊕ Add Capability"**

- **Name:** `analyze_results`
- **Type:** `@llm` (AI model)
- **Reads:** `search.results`, `user.query`
- **Writes:** `analysis.summary`
- **Model:** `gpt-4o-mini`
- **System Prompt:**
  ```
  You are a research analyst. Synthesize search results into clear insights.
  ```
- **User Template:**
  ```
  User asked: {{user.query}}
  
  Search results:
  {{search.results}}
  
  Provide: executive summary, key findings, and open questions.
  ```

### Capability 3: Generate Report

Click **"⊕ Add Capability"**

- **Name:** `generate_report`
- **Type:** `@llm`
- **Reads:** `analysis.summary`, `user.query`
- **Writes:** `report.content`
- **Model:** `gpt-4o-mini`
- **System Prompt:**
  ```
  You are a professional report writer. Create well-structured reports.
  ```
- **User Template:**
  ```
  Based on this analysis:
  {{analysis.summary}}
  
  Generate a comprehensive research report on: {{user.query}}
  
  Include: title, executive summary, detailed findings, and conclusions.
  ```

**You now have 3 capabilities!** 🎨

---

## Step 5: Define Success

Click **"Define Goal"**

- **Required Fields:**
  - ✓ `report.content`
  
- **Custom Validation (optional):**
  ```python
  report = ws.get("report.content")
  return len(report) > 100  # Must be substantial
  ```

**Your goal is defined!** 🎯

---

## Step 6: View the Topology

Click **"📊 View Topology"**

The system automatically computed this execution graph:

```
user.query (initial)
     ↓
search_web (@tool)
     ↓
search.results
     ↓
analyze_results (@llm) ←── user.query
     ↓
analysis.summary
     ↓
generate_report (@llm) ←── user.query
     ↓
report.content
     ↓
GOAL ✓
```

**Notice:**
- You never drew arrows!
- Order emerged from read/write dependencies
- System shows all data flows automatically

---

## Step 7: Test Your Agent

Click **"▶️ Test Agent"**

Provide initial state:
```json
{
  "user.query": "What are the latest AI trends in 2025?"
}
```

Click **"Run"**

Watch the execution trace:
```
✓ search_web         42ms
✓ analyze_results    1.2s
✓ generate_report    2.1s
✓ GOAL ACHIEVED      3.4s total
```

Inspect the final state:
```json
{
  "user.query": "What are the latest AI trends in 2025?",
  "search.results": [...],
  "analysis.summary": "Key trends include...",
  "report.content": "# AI Trends 2025\n\n## Executive Summary\n..."
}
```

**Your agent works!** ✅

---

## Step 8: Export

Click **"⬇️ Export"** and choose format:

### Python Code
```python
from core.sdk import Agent, tool, llm

@tool(inputs=["user.query"], outputs=["search.results"])
def search_web(ws: Snapshot):
    query = ws.get("user.query")
    results = search_api(query, limit=10)
    return {"search.results": results}

@llm(
    inputs=["search.results", "user.query"],
    outputs=["analysis.summary"],
    model="gpt-4o-mini",
    template="User asked: {{user.query}}..."
)
def analyze_results(ws: Snapshot):
    pass

# ... etc ...

agent = Agent([search_web, analyze_results, generate_report])
```

### JSON Schema
```json
{
  "name": "research-assistant",
  "state": {
    "user.query": {"type": "text", "initial": true},
    "search.results": {"type": "json"},
    ...
  },
  "capabilities": [...]
}
```

### YAML Config
```yaml
agent:
  name: research-assistant
  
state:
  user.query:
    type: text
    initial: true
  ...
  
capabilities:
  - name: search_web
    type: tool
    ...
```

---

## What You Just Learned

### 1. State-First Design
You defined **what data exists**, not **what steps to take**.

### 2. Declarative Capabilities
You declared **reads** and **writes** - no manual wiring!

### 3. Automatic Topology
The system figured out execution order from dependencies.

### 4. Goal-Oriented
You defined **success condition**, not a specific path.

### 5. Export to Code
You got real Python code, not a proprietary format.

---

## Next Steps

### 🎓 Tutorials
- **Add Human Review:** Insert a `@human` capability for approval
- **Add Validation:** Create a `@step` to check quality metrics
- **Add Branching:** Multiple paths based on state (no IF nodes needed!)
- **Add Memory:** Use topology regions to persist data

### 📚 Browse Marketplace
- Install pre-built capabilities
- Explore templates (research, data processing, customer support)
- Contribute your own capabilities

### 🚀 Deploy
- Export to Python and run locally
- Deploy to Ranger Cloud
- Integrate with CI/CD pipeline

---

## Common Questions

### Q: How do I add an IF/ELSE?

**A:** You don't! Define multiple capabilities with different conditions. The topology automatically executes the right ones based on available state.

Example:
```python
@step(inputs=["data"], outputs=["valid_data"])
def validate_data(ws):
    data = ws.get("data")
    if is_valid(data):
        return {"valid_data": data}
    else:
        raise GoalBlocked("invalid_data")

@step(inputs=["valid_data"], outputs=["result"])
def process_valid(ws):
    # Only runs if valid_data exists
    ...
```

### Q: How do I handle errors?

**A:** Ranger uses **fail-fast** principle. If a capability raises an exception, execution stops and the error propagates. No try/catch spaghetti!

### Q: Can capabilities run in parallel?

**A:** Yes! If two capabilities have no dependencies, they run automatically in parallel.

Example:
```
Capability A: [input] → [output_a]
Capability B: [input] → [output_b]

→ A and B run at the same time!
```

### Q: How do I test a single capability?

**A:** Click the capability card and choose **"Test in Isolation"**. Provide mock state and see the output without running the whole agent.

---

## Tips & Tricks

### 🎯 Tip 1: Start Small
Build with 2-3 capabilities first. Test. Then expand.

### 🎯 Tip 2: Use Descriptive Names
`search.results` is better than `data1`. Future you will thank you!

### 🎯 Tip 3: Leverage Marketplace
Don't reinvent the wheel. Browse the marketplace first.

### 🎯 Tip 4: Test Each Capability
Use isolation testing to catch bugs early.

### 🎯 Tip 5: Watch the Topology
The computed graph shows you what's actually happening.

---

## Get Help

- 📖 **Docs:** [studio/DESIGN.md](DESIGN.md)
- 💬 **Community:** Discord / Reddit / GitHub Discussions
- 🐛 **Issues:** GitHub Issues
- 📧 **Email:** support@ranger.dev

---

## Welcome to Topological Agent Design! 🚀

You've just built your first agent using **state-first, topology-driven architecture**. 

This is fundamentally different from traditional workflow tools, but the payoff is huge:
- Simpler mental model
- Easier to maintain
- Automatic parallelization
- Testable in isolation
- Exports to real code

**Happy building!** 🎉

