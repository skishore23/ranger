# Ranger Studio Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        USER'S BROWSER                           │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐ │
│  │                    React Frontend                         │ │
│  │                   (TypeScript + Vite)                     │ │
│  │                                                           │ │
│  │  ┌─────────────┐  ┌──────────────┐  ┌─────────────────┐ │ │
│  │  │   Canvas    │  │  Capability  │  │   Topology      │ │ │
│  │  │ (React Flow)│  │   Builder    │  │   Viewer        │ │ │
│  │  │             │  │  (Forms)     │  │  (D3/ReactFlow) │ │ │
│  │  └─────────────┘  └──────────────┘  └─────────────────┘ │ │
│  │         ↓                 ↓                   ↓          │ │
│  │  ┌───────────────────────────────────────────────────── │ │
│  │  │         Zustand Store (State Management)           │ │ │
│  │  │  • Agent definition                                │ │ │
│  │  │  • Current topology                                │ │ │
│  │  │  • UI state                                        │ │ │
│  │  └─────────────────────────────────────────────────────┘ │ │
│  │         ↓                                                  │ │
│  │  ┌─────────────────────────────────────────────────────┐ │ │
│  │  │           API Client (Fetch/Axios)                  │ │ │
│  │  └─────────────────────────────────────────────────────┘ │ │
│  └───────────────────────────────────────────────────────────┘ │
│                              ↓                                  │
│                          REST API                               │
│                      (JSON over HTTP)                           │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                       Backend Server                            │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐ │
│  │                    FastAPI Server                         │ │
│  │                      (Python 3.11+)                       │ │
│  │                                                           │ │
│  │  ┌──────────────────┐    ┌──────────────────┐           │ │
│  │  │  Agent CRUD      │    │   Topology       │           │ │
│  │  │  /agents/        │    │   /topology      │           │ │
│  │  │  • Create        │    │   • Compute DAG  │           │ │
│  │  │  • Read          │    │   • Validate     │           │ │
│  │  │  • Update        │    │   • Detect cycles│           │ │
│  │  │  • Delete        │    │                  │           │ │
│  │  │  • List          │    │  Uses NetworkX   │           │ │
│  │  └──────────────────┘    └──────────────────┘           │ │
│  │                                                           │ │
│  │  ┌──────────────────┐    ┌──────────────────┐           │ │
│  │  │   Compiler       │    │   Executor       │           │ │
│  │  │   /compile       │    │   /execute       │           │ │
│  │  │  • Generate code │    │   • Run agent    │           │ │
│  │  │  • Validate      │    │   • Sandbox      │           │ │
│  │  │  • Export        │    │   • Trace steps  │           │ │
│  │  │                  │    │                  │           │ │
│  │  │  Uses Jinja2     │    │  Import Ranger   │           │ │
│  │  └──────────────────┘    └──────────────────┘           │ │
│  │                                                           │ │
│  │  ┌──────────────────────────────────────────┐           │ │
│  │  │         Marketplace                       │           │ │
│  │  │         /marketplace                      │           │ │
│  │  │         • List capabilities               │           │ │
│  │  │         • Search                          │           │ │
│  │  │         • Install                         │           │ │
│  │  └──────────────────────────────────────────┘           │ │
│  └───────────────────────────────────────────────────────────┘ │
│                              ↓                                  │
│  ┌───────────────────────────────────────────────────────────┐ │
│  │                    Ranger Core                            │ │
│  │                                                           │ │
│  │  Import directly from:                                    │ │
│  │  • core.sdk.Agent                                         │ │
│  │  • core.engine.solve()                                    │ │
│  │  • topology.stitch                                        │ │
│  │                                                           │ │
│  │  Validates generated code is compatible!                  │ │
│  └───────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                         Storage                                 │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────────────┐    ┌──────────────────┐                 │
│  │   PostgreSQL     │    │   File System    │                 │
│  │   (Production)   │    │   (Development)  │                 │
│  │                  │    │                  │                 │
│  │  • Agents        │    │  • agents/*.json │                 │
│  │  • Users         │    │  • examples/     │                 │
│  │  • Marketplace   │    │                  │                 │
│  └──────────────────┘    └──────────────────┘                 │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

## Data Flow

### 1. Creating a Capability

```
User clicks "Add Capability"
        ↓
CapabilityBuilder modal opens
        ↓
User fills form (name, type, reads, writes, implementation)
        ↓
Zustand store updates
        ↓
Canvas re-renders with new capability card
        ↓
User clicks "Save Agent"
        ↓
API POST /agents/{name}
        ↓
FastAPI validates with Pydantic
        ↓
Saves to database/filesystem
        ↓
Returns success
        ↓
UI shows confirmation
```

### 2. Computing Topology

```
User clicks "View Topology"
        ↓
API POST /agents/{name}/topology
        ↓
Backend loads agent definition
        ↓
NetworkX analyzes dependencies:
  • Creates graph nodes (capabilities)
  • Creates edges (reads/writes)
  • Performs topological sort
  • Detects cycles
  • Finds unreachable capabilities
        ↓
Returns:
  • Execution order
  • Graph structure
  • Warnings
        ↓
Frontend renders with React Flow or D3
        ↓
User sees visual DAG
```

### 3. Compiling to Python

```
User clicks "Export"
        ↓
API POST /agents/{name}/compile
        ↓
Backend processes:
  1. Load agent definition
  2. For each capability:
     • Select appropriate Jinja2 template
       (@step → step.py.jinja)
       (@llm  → llm.py.jinja)
       (@tool → tool.py.jinja)
     • Render template with capability data
  3. Assemble imports
  4. Generate goal function
  5. Generate Agent() instantiation
        ↓
Returns Python code as string
        ↓
Frontend shows in Monaco Editor
        ↓
User can copy or download
```

### 4. Testing Agent

```
User clicks "Test Agent"
        ↓
User provides initial state JSON
        ↓
API POST /agents/{name}/execute
        ↓
Backend:
  1. Compile agent to Python
  2. Import Ranger SDK
  3. Instantiate Agent
  4. Create initial Snapshot
  5. Call engine.solve()
  6. Capture trace
        ↓
Returns:
  • Final state
  • Execution trace
  • Performance metrics
        ↓
Frontend displays step-by-step
```

## Component Interaction

### React Flow Canvas ↔ Zustand Store

```typescript
// Canvas reads from store
const { capabilities, stateFields } = useAgentStore();

// User drags capability card
const onNodeDrag = (event, node) => {
  updateCapabilityPosition(node.id, node.position);
};

// User connects capabilities (reads/writes)
const onConnect = (connection) => {
  // Validate connection makes sense
  addDependency(connection);
};
```

### Capability Builder ↔ API

```typescript
// User submits form
const onSubmit = async (data: CapabilityFormData) => {
  // Validate locally
  const validated = capabilitySchema.parse(data);
  
  // Add to store
  addCapability(validated);
  
  // Auto-save to backend
  await api.updateAgent(agentId, getAgentDefinition());
  
  closeModal();
};
```

### Topology Viewer ↔ Backend

```typescript
// Request topology
const { data: topology } = useQuery(
  ['topology', agentId],
  () => api.computeTopology(agentId)
);

// Render with React Flow
const nodes = topology.nodes.map(n => ({
  id: n.id,
  type: n.type === 'capability' ? 'capability' : 'state',
  data: n,
  position: calculatePosition(n) // Auto-layout
}));
```

## Technology Choices Explained

### Why React Flow?

```typescript
// Without React Flow (painful):
// - Manual drag-and-drop
// - Manual zoom/pan
// - Manual edge routing
// - Manual collision detection
// = 2-3 weeks of work

// With React Flow:
import ReactFlow from 'reactflow';

<ReactFlow
  nodes={nodes}
  edges={edges}
  onNodesChange={onNodesChange}
  onEdgesChange={onEdgesChange}
/>
// = Works in 5 minutes! ✨
```

### Why Zustand?

```typescript
// Redux: 100+ lines of boilerplate
// Zustand: 10 lines

import create from 'zustand';

const useAgentStore = create((set) => ({
  capabilities: [],
  addCapability: (cap) => set((state) => ({
    capabilities: [...state.capabilities, cap]
  })),
}));

// That's it!
```

### Why NetworkX?

```python
# Manual topological sort: 50+ lines, easy to get wrong
# NetworkX: 3 lines

import networkx as nx

G = nx.DiGraph()
G.add_edges_from(edges)
order = list(nx.topological_sort(G))  # Done!
```

## Performance Considerations

### Frontend
- React Flow handles 1000+ nodes efficiently
- Zustand updates are fast (no re-renders unless needed)
- Monaco Editor is lazy-loaded (code-splitting)
- Tailwind CSS purges unused styles (small bundle)

### Backend
- FastAPI is async (handles concurrent requests)
- NetworkX algorithms are O(V+E) (fast for typical agents)
- Jinja2 compilation is cached
- Agent definitions are ~10-50KB (fast to transfer)

### Scaling
- **Small agents (5-10 capabilities):** Instant
- **Medium agents (20-50 capabilities):** < 1 second
- **Large agents (100+ capabilities):** < 5 seconds

## Security

### Sandboxing Execution
```python
# Don't just exec() user code!
# Use restricted execution:

from RestrictedPython import compile_restricted

code = compile_agent(agent_def)
safe_code = compile_restricted(code, '<agent>', 'exec')

# Execute in isolated namespace
namespace = {
    '__builtins__': safe_builtins,
    'ws': snapshot,
}
exec(safe_code, namespace)
```

### Input Validation
```python
# Pydantic validates everything
class CapabilityModel(BaseModel):
    id: str = Field(pattern=r'^[a-z_][a-z0-9_]*$')
    reads: List[str]
    writes: List[str]
    # Invalid data throws ValidationError
```

## Development Workflow

```bash
# Terminal 1: Frontend dev server
cd studio/frontend
npm run dev
# Hot reload on save ♨️

# Terminal 2: Backend dev server  
cd studio/backend
uvicorn api.main:app --reload
# Auto-reload on save 🔄

# Terminal 3: Testing
pytest tests/
# Run tests 🧪

# Edit code → Save → See changes instantly!
```

## Summary

**Frontend:** React + React Flow + Zustand = Visual canvas in days, not months
**Backend:** FastAPI + NetworkX + Jinja2 = Compile & validate in Python
**Integration:** REST API with JSON = Simple, debuggable, scalable

**Total lines of code for MVP:** ~2000-3000 (very achievable!)

**Time to working prototype:** 2-3 weeks with focused effort

**Result:** Production-ready visual agent builder that's uniquely Ranger! 🚀

