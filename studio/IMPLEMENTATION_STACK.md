# Ranger Studio: Practical Implementation Plan

## TL;DR - Start Small, Build Fast

**Phase 1 MVP (2-3 weeks):** Working visual editor that compiles to Python code
**Phase 2 (1-2 weeks):** Live execution + testing
**Phase 3 (ongoing):** Marketplace, collaboration, polish

---

## Tech Stack (Battle-Tested Open Source)

### Frontend: Modern React Stack

#### 1. **React Flow** ⭐ RECOMMENDED
- **GitHub:** `xyflow/xyflow` (22k+ stars)
- **Purpose:** Node-based visual editor (perfect for capabilities!)
- **Why:** MIT license, TypeScript support, extensive docs, active maintenance
- **Features:**
  - Drag-and-drop nodes
  - Auto-layout algorithms
  - Custom node types (perfect for @step, @tool, @llm, @human)
  - Minimap, zoom, pan
  - Edge connections (read/write dependencies)
  - React 18 compatible

```bash
npm install reactflow
```

**Alternative:** `retejs/rete` (9k stars) - more low-level control but steeper learning curve

#### 2. **Monaco Editor** (VS Code's editor)
- **Package:** `@monaco-editor/react`
- **Purpose:** Code editing for Python functions, JSON schemas
- **Why:** Industry standard, syntax highlighting, autocomplete
- **Features:**
  - Python, JSON, YAML support
  - IntelliSense
  - Error highlighting
  - Theme support

```bash
npm install @monaco-editor/react
```

#### 3. **Zustand** (State Management)
- **GitHub:** `pmndrs/zustand` (44k+ stars)
- **Purpose:** Manage agent definition state
- **Why:** Simpler than Redux, perfect for medium-sized apps
- **Use Cases:**
  - Current agent definition
  - Undo/redo stack
  - Computed topology cache

```bash
npm install zustand
```

#### 4. **React Hook Form** (Forms)
- **GitHub:** `react-hook-form/react-hook-form` (40k+ stars)
- **Purpose:** Capability builder forms
- **Why:** Performant, small bundle, great DX

```bash
npm install react-hook-form
```

#### 5. **Tailwind CSS** (Styling)
- **Purpose:** Rapid UI development
- **Why:** Matches our mockup style, utility-first

```bash
npm install -D tailwindcss postcss autoprefixer
```

#### 6. **D3.js or Cytoscape.js** (Topology Visualization)
- **Purpose:** Show computed execution DAG
- **Why:** Advanced graph layouts, animations
- **Note:** React Flow might be enough for MVP

---

### Backend: Python FastAPI

#### 1. **FastAPI** (Already mocked!)
- **Purpose:** REST API for agent CRUD, compilation, testing
- **Why:** Fast, async, auto-docs, Python native

```bash
pip install fastapi uvicorn[standard]
```

#### 2. **Pydantic V2**
- **Purpose:** Validation for agent definitions
- **Why:** Already used in Ranger core

#### 3. **Jinja2**
- **Purpose:** Template compilation for Python code generation
- **Why:** Already used in Ranger for prompts

#### 4. **NetworkX** (Topology Computation)
- **Purpose:** Topological sort, cycle detection, dependency analysis
- **Why:** Industry standard for graph algorithms in Python

```bash
pip install networkx
```

---

## File Structure (Clean & Scalable)

```
ranger/
├── studio/
│   ├── frontend/              # React app
│   │   ├── src/
│   │   │   ├── components/
│   │   │   │   ├── Canvas/
│   │   │   │   │   ├── Canvas.tsx           # Main React Flow canvas
│   │   │   │   │   ├── StateFieldNode.tsx   # State field node component
│   │   │   │   │   ├── CapabilityNode.tsx   # Capability card component
│   │   │   │   │   └── ConnectionEdge.tsx   # Custom edge styling
│   │   │   │   │
│   │   │   │   ├── Builders/
│   │   │   │   │   ├── StateFieldBuilder.tsx
│   │   │   │   │   ├── CapabilityBuilder.tsx
│   │   │   │   │   ├── LLMCapabilityForm.tsx
│   │   │   │   │   ├── StepCapabilityForm.tsx
│   │   │   │   │   └── ToolCapabilityForm.tsx
│   │   │   │   │
│   │   │   │   ├── Topology/
│   │   │   │   │   ├── TopologyGraph.tsx    # Computed DAG view
│   │   │   │   │   └── ExecutionTrace.tsx   # Step-by-step debugger
│   │   │   │   │
│   │   │   │   ├── Export/
│   │   │   │   │   ├── CodePreview.tsx      # Monaco editor
│   │   │   │   │   └── ExportDialog.tsx     # Python/JSON/YAML
│   │   │   │   │
│   │   │   │   └── Common/
│   │   │   │       ├── Sidebar.tsx
│   │   │   │       ├── Toolbar.tsx
│   │   │   │       └── Modal.tsx
│   │   │   │
│   │   │   ├── stores/
│   │   │   │   ├── agentStore.ts           # Zustand store
│   │   │   │   └── topologyStore.ts        # Computed topology cache
│   │   │   │
│   │   │   ├── services/
│   │   │   │   └── api.ts                  # API client
│   │   │   │
│   │   │   ├── types/
│   │   │   │   └── agent.ts                # TypeScript types
│   │   │   │
│   │   │   ├── App.tsx
│   │   │   └── main.tsx
│   │   │
│   │   ├── package.json
│   │   ├── vite.config.ts                  # Vite for dev server
│   │   └── tailwind.config.js
│   │
│   ├── backend/               # FastAPI server
│   │   ├── api/
│   │   │   ├── routes/
│   │   │   │   ├── agents.py               # Agent CRUD
│   │   │   │   ├── topology.py             # Topology computation
│   │   │   │   ├── compile.py              # Code generation
│   │   │   │   ├── execute.py              # Test execution
│   │   │   │   └── marketplace.py          # Capability library
│   │   │   │
│   │   │   ├── models/
│   │   │   │   └── agent.py                # Pydantic models
│   │   │   │
│   │   │   └── main.py                     # FastAPI app
│   │   │
│   │   ├── compiler/
│   │   │   ├── codegen.py                  # Python code generator
│   │   │   ├── templates/                  # Jinja2 templates
│   │   │   │   ├── agent.py.jinja
│   │   │   │   ├── step.py.jinja
│   │   │   │   ├── llm.py.jinja
│   │   │   │   └── tool.py.jinja
│   │   │   └── validator.py                # Validate agent definitions
│   │   │
│   │   ├── topology/
│   │   │   ├── analyzer.py                 # NetworkX-based analysis
│   │   │   ├── sorter.py                   # Topological sort
│   │   │   └── validator.py                # Cycle detection, warnings
│   │   │
│   │   ├── executor/
│   │   │   └── sandbox.py                  # Safe agent execution
│   │   │
│   │   └── requirements.txt
│   │
│   ├── shared/                # Shared between frontend/backend
│   │   └── schema.json        # Agent JSON schema
│   │
│   └── examples/              # Example agents
│       ├── research_agent.json
│       ├── data_pipeline.json
│       └── customer_support.json
```

---

## Phase 1 MVP: Core Builder (2-3 weeks)

### Week 1: Foundation

#### Day 1-2: Setup
```bash
# Frontend
cd studio/frontend
npm create vite@latest . -- --template react-ts
npm install reactflow @monaco-editor/react zustand react-hook-form
npm install -D tailwindcss postcss autoprefixer
npx tailwindcss init -p

# Backend
cd ../backend
pip install fastapi uvicorn pydantic jinja2 networkx
```

#### Day 3-4: Basic Canvas
- Implement React Flow canvas
- Create CapabilityNode component (renders card with reads/writes)
- Add drag-and-drop from sidebar
- Store state in Zustand

**Files to create:**
- `frontend/src/components/Canvas/Canvas.tsx`
- `frontend/src/components/Canvas/CapabilityNode.tsx`
- `frontend/src/stores/agentStore.ts`

#### Day 5-7: State Space Designer
- Left sidebar for state fields
- Add/edit/delete state fields
- Visual list with types and "initial" badge

**Files to create:**
- `frontend/src/components/Builders/StateFieldBuilder.tsx`
- `frontend/src/components/Common/Sidebar.tsx`

### Week 2: Capability Builder

#### Day 1-3: Capability Forms
- Modal dialog for creating capabilities
- Type selector (@step, @tool, @llm, @human)
- Form for each type (especially LLM with model, prompt, schema)
- Checkbox list for reads/writes

**Files to create:**
- `frontend/src/components/Builders/CapabilityBuilder.tsx`
- `frontend/src/components/Builders/LLMCapabilityForm.tsx`
- `frontend/src/components/Builders/StepCapabilityForm.tsx`

#### Day 4-5: API Integration
- Connect to FastAPI backend
- Save/load agent definitions
- Real-time validation

**Files to create:**
- `frontend/src/services/api.ts`
- `backend/api/routes/agents.py`

#### Day 6-7: Goal Definition
- Simple form for goal scope
- Optional validation code editor (Monaco)

### Week 3: Topology & Export

#### Day 1-3: Topology Computation
- NetworkX-based dependency analysis
- Topological sort
- Cycle detection and warnings
- Visual DAG display (React Flow or D3)

**Files to create:**
- `backend/topology/analyzer.py`
- `backend/topology/sorter.py`
- `frontend/src/components/Topology/TopologyGraph.tsx`

#### Day 4-5: Code Generation
- Jinja2 templates for Python code
- Compile endpoint
- Preview in Monaco editor

**Files to create:**
- `backend/compiler/codegen.py`
- `backend/compiler/templates/agent.py.jinja`
- `frontend/src/components/Export/CodePreview.tsx`

#### Day 6-7: Polish & Testing
- Error handling
- Loading states
- Basic user testing
- Bug fixes

---

## Phase 2: Execution & Testing (1-2 weeks)

### Week 1: Live Testing

#### Test Runner
- Input initial state (JSON editor)
- Execute agent in sandbox
- Show execution trace
- Display final state

**Files to create:**
- `backend/executor/sandbox.py`
- `frontend/src/components/Topology/ExecutionTrace.tsx`

#### Capability Isolation Testing
- Test single capability with mock state
- Fast feedback loop

### Week 2: Debugging

#### State Inspector
- Step-through debugger
- View state at each step
- Pause/resume execution

---

## Phase 3: Polish & Features (Ongoing)

### Marketplace
- Browse pre-built capabilities
- One-click installation
- Community contributions

### Collaboration
- Multi-user editing (Socket.io)
- Version control (Git integration)
- Comments on capabilities

### Templates
- Pre-built agent templates
- Quick start wizards

---

## Key Libraries Summary

| Library | Purpose | Stars | License |
|---------|---------|-------|---------|
| **React Flow** | Visual canvas | 22k+ | MIT |
| **Monaco Editor** | Code editing | Part of VS Code | MIT |
| **Zustand** | State management | 44k+ | MIT |
| **React Hook Form** | Forms | 40k+ | MIT |
| **Tailwind CSS** | Styling | 78k+ | MIT |
| **FastAPI** | Backend API | 73k+ | MIT |
| **NetworkX** | Graph algorithms | 14k+ | BSD |
| **Pydantic** | Validation | 19k+ | MIT |

**All MIT/BSD - Commercial friendly!** ✅

---

## MVP Feature Checklist

### ✅ Must-Have (Week 1-3)
- [ ] Visual canvas with drag-and-drop
- [ ] Add/edit state fields
- [ ] Create capabilities (@step, @tool, @llm)
- [ ] Define goal
- [ ] Compute topology (auto-ordering)
- [ ] Export to Python code
- [ ] Save/load agents (JSON)

### 🎯 Nice-to-Have (Week 4+)
- [ ] Live agent execution
- [ ] Topology visualization
- [ ] Isolation testing
- [ ] Monaco code editor integration
- [ ] Error highlighting
- [ ] Undo/redo

### 🚀 Future
- [ ] Marketplace
- [ ] Multi-user editing
- [ ] Cloud deployment
- [ ] Templates library

---

## Quick Start Commands

### Option A: Vite (Faster, Modern)
```bash
cd studio/frontend
npm create vite@latest . -- --template react-ts
npm install reactflow @monaco-editor/react zustand react-hook-form
npm install -D tailwindcss
npm run dev
```

### Option B: Create React App (More stable)
```bash
cd studio/frontend
npx create-react-app . --template typescript
npm install reactflow @monaco-editor/react zustand react-hook-form
npm start
```

### Backend
```bash
cd studio/backend
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn api.main:app --reload
```

---

## React Flow Example (5 minutes to working prototype!)

```tsx
// Canvas.tsx
import ReactFlow, { Node, Edge } from 'reactflow';
import 'reactflow/dist/style.css';

const initialNodes: Node[] = [
  {
    id: 'search_web',
    type: 'capability',
    position: { x: 100, y: 100 },
    data: {
      label: 'search_web',
      type: '@tool',
      reads: ['user.query'],
      writes: ['search.results']
    }
  }
];

export function Canvas() {
  return (
    <div style={{ height: '100vh' }}>
      <ReactFlow nodes={initialNodes} />
    </div>
  );
}
```

**That's it! Working canvas in 10 lines!**

---

## Why This Stack?

### 1. **React Flow is Perfect**
- Built specifically for node-based editors
- Used by Linear, Stripe, etc.
- Handles 90% of the hard stuff (drag, zoom, connections)
- We just customize the nodes to match Ranger's style

### 2. **Python Backend**
- Stays in Ranger's ecosystem
- Easy to compile to actual Ranger SDK code
- Can directly import `core.sdk` for validation

### 3. **Fast Iteration**
- Vite dev server: instant hot reload
- React Hook Form: no boilerplate
- Tailwind: style as you code

### 4. **Production Ready**
- All libraries have 10k+ stars, actively maintained
- MIT licensed - no legal issues
- TypeScript for type safety

---

## Next Immediate Steps

### Step 1: Initialize Project (Today!)
```bash
cd /Users/kishore/ranger/studio
mkdir frontend backend

# Frontend
cd frontend
npm create vite@latest . -- --template react-ts
npm install reactflow @monaco-editor/react zustand
npm run dev

# Backend (in new terminal)
cd ../backend
mkdir -p api/routes compiler topology
touch api/main.py
# Copy mock/server.py logic to api/main.py
```

### Step 2: First Component (Tomorrow)
- Create `Canvas.tsx` with React Flow
- Render one hardcoded capability card
- Make it draggable

### Step 3: Zustand Store (Day 3)
- Define agent state structure
- Add actions (addCapability, removeCapability, etc.)
- Connect Canvas to store

### Step 4: First Form (Day 4-5)
- StateFieldBuilder modal
- Save to Zustand store
- See it appear in sidebar

---

## Success Metrics for MVP

1. **User can create a simple agent in < 5 minutes**
2. **Generated Python code is valid and runs**
3. **Topology correctly shows dependencies**
4. **No crashes, clear error messages**

---

## Questions to Decide

1. **Vite or CRA?** → Recommend Vite (faster)
2. **Tailwind or CSS-in-JS?** → Recommend Tailwind (matches mockups)
3. **Monaco or CodeMirror?** → Recommend Monaco (VS Code quality)
4. **Client-side or server-side topology?** → Server-side (can reuse Ranger's logic)

---

## Let's Build It! 🚀

Ready to start? We can:
1. **Initialize the React + FastAPI project structure**
2. **Build the first working canvas with React Flow**
3. **Create the capability builder form**

Which would you like to tackle first?

