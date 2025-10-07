# Ranger Studio

**Visual Topological Agent Builder** - No-code tool for creating Ranger agents.

## What Makes It Different?

Unlike n8n/Zapier/AgentKit that use sequential workflows, Ranger Studio uses **topological orchestration** where execution emerges from state dependencies.

### Traditional Tools
```
[Trigger] → [Step 1] → [If/Else] → [Step 2] → [Done]
```

### Ranger Studio
```
State Space + Capabilities → Automatic Topology → Execution
```

## Quick Start

```bash
# Install dependencies
pip install -e ".[studio]"

# Run mock server
cd studio
python -m mock.server

# Open browser
http://localhost:8080
```

## Directory Structure

```
studio/
├── DESIGN.md           # Complete design document
├── mock/               # Simple implementation mocks
│   ├── server.py       # FastAPI backend mock
│   ├── compiler.py     # Visual DSL → Python compiler
│   ├── templates/      # Agent templates
│   └── examples/       # Example agents
├── ui/                 # UI mockups (HTML/CSS/JS)
│   ├── canvas.html     # State space designer
│   ├── builder.html    # Capability builder
│   └── preview.html    # Live testing
└── schemas/            # JSON schemas
    ├── agent.json      # Agent definition
    └── capability.json # Capability spec
```

## Core Concepts

### 1. State-First Design
Define what data exists, not what steps to take.

### 2. Capability Cards
Each card declares reads/writes - topology figures out order.

### 3. Goal-Oriented
Define success condition, let engine find the path.

### 4. Automatic Parallelization
Independent capabilities run concurrently.

### 5. Export to Code
Generate real Python code, not proprietary format.

## Status

🚧 **Prototype Phase**
- ✅ Design document complete
- ✅ Mock server ready
- ✅ Example agents
- 🔄 UI implementation in progress
- 📋 Compiler next

## License

MIT

