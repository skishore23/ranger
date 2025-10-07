"""Mock FastAPI server for Ranger Studio.

This is a simple prototype demonstrating the API endpoints.
Not for production use - just a reference implementation.
"""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="Ranger Studio API", version="0.1.0")

# CORS for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Models
class StateField(BaseModel):
    """State field definition"""

    type: str  # text, json, number, boolean
    initial: bool = False
    schema: Dict[str, Any] | None = None
    description: str | None = None


class CapabilityImplementation(BaseModel):
    """Capability implementation details"""

    language: str | None = None
    code: str | None = None
    model: str | None = None
    system: str | None = None
    template: str | None = None
    schema: Dict[str, Any] | None = None
    temperature: float | None = None


class Capability(BaseModel):
    """Capability definition"""

    id: str
    type: str  # step, tool, llm, human
    reads: List[str]
    writes: List[str]
    implementation: CapabilityImplementation
    description: str | None = None
    tags: List[str] | None = None


class Goal(BaseModel):
    """Goal definition"""

    scope: List[str]
    validation: str | None = None
    description: str | None = None


class AgentDefinition(BaseModel):
    """Complete agent definition"""

    name: str
    version: str = "1.0.0"
    description: str | None = None
    state: Dict[str, StateField]
    capabilities: List[Capability]
    goal: Goal
    budget: Dict[str, int] | None = None


class TopologyGraph(BaseModel):
    """Computed topology graph"""

    nodes: List[Dict[str, Any]]
    edges: List[Dict[str, Any]]
    execution_order: List[str]
    warnings: List[str] = []


# In-memory storage (mock)
agents: Dict[str, AgentDefinition] = {}


# Endpoints
@app.get("/")
def read_root() -> Dict[str, str]:
    """Health check"""
    return {"status": "ok", "service": "Ranger Studio API"}


@app.post("/agents/", status_code=201)
def create_agent(agent: AgentDefinition) -> Dict[str, Any]:
    """Create a new agent"""
    if agent.name in agents:
        raise HTTPException(status_code=400, detail="Agent already exists")

    agents[agent.name] = agent
    return {"message": "Agent created", "name": agent.name}


@app.get("/agents/{agent_name}")
def get_agent(agent_name: str) -> AgentDefinition:
    """Get agent definition"""
    if agent_name not in agents:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agents[agent_name]


@app.get("/agents/")
def list_agents() -> List[str]:
    """List all agents"""
    return list(agents.keys())


@app.post("/agents/{agent_name}/topology")
def compute_topology(agent_name: str) -> TopologyGraph:
    """Compute execution topology from capabilities"""
    if agent_name not in agents:
        raise HTTPException(status_code=404, detail="Agent not found")

    agent = agents[agent_name]

    # Simple topological sort based on reads/writes
    nodes = []
    edges = []
    execution_order = []
    warnings = []

    # Create nodes
    state_nodes = {}
    for field_name in agent.state.keys():
        state_id = f"state_{field_name}"
        state_nodes[field_name] = state_id
        nodes.append({"id": state_id, "type": "state", "label": field_name})

    for cap in agent.capabilities:
        nodes.append({"id": cap.id, "type": cap.type, "label": cap.id})

    # Create edges
    for cap in agent.capabilities:
        for read_field in cap.reads:
            if read_field in state_nodes:
                edges.append({"from": state_nodes[read_field], "to": cap.id, "type": "read"})

        for write_field in cap.writes:
            if write_field in state_nodes:
                edges.append({"from": cap.id, "to": state_nodes[write_field], "type": "write"})

    # Simple execution order (topological sort mock)
    # Real implementation would use proper algorithm
    sorted_caps = sorted(agent.capabilities, key=lambda c: len(c.reads))
    execution_order = [cap.id for cap in sorted_caps]

    # Check for missing dependencies
    written_fields = set()
    for cap in sorted_caps:
        for read_field in cap.reads:
            if read_field not in agent.state:
                warnings.append(f"Capability {cap.id} reads undefined field: {read_field}")
            elif read_field not in written_fields and not agent.state[read_field].initial:
                warnings.append(f"Capability {cap.id} reads uninitialized field: {read_field}")

        written_fields.update(cap.writes)

    # Check goal reachability
    for goal_field in agent.goal.scope:
        if goal_field not in written_fields:
            warnings.append(f"Goal field {goal_field} is never written")

    return TopologyGraph(
        nodes=nodes, edges=edges, execution_order=execution_order, warnings=warnings
    )


@app.post("/agents/{agent_name}/compile")
def compile_agent(agent_name: str) -> Dict[str, str]:
    """Compile visual agent to Python code"""
    if agent_name not in agents:
        raise HTTPException(status_code=404, detail="Agent not found")

    agent = agents[agent_name]

    # Generate Python code (simplified)
    code_parts = [
        '"""Auto-generated by Ranger Studio"""',
        "",
        "from __future__ import annotations",
        "",
        "from typing import Dict, Any",
        "from core.sdk import Agent, step, tool, llm, goal",
        "from core.workspace import Snapshot",
        "from topology.types import Budget",
        "",
        "",
    ]

    # Generate capabilities
    for cap in agent.capabilities:
        if cap.type == "step":
            code_parts.append(f"@step(inputs={cap.reads}, outputs={cap.writes})")
            code_parts.append(f"def {cap.id}(ws: Snapshot) -> Dict[str, Any]:")
            if cap.implementation.code:
                # Indent code
                for line in cap.implementation.code.splitlines():
                    code_parts.append(f"    {line}")
            else:
                code_parts.append("    # TODO: Implement logic")
                code_parts.append("    pass")
            code_parts.append("")

        elif cap.type == "llm":
            code_parts.append(f"@llm(")
            code_parts.append(f"    inputs={cap.reads},")
            code_parts.append(f"    outputs={cap.writes},")
            if cap.implementation.model:
                code_parts.append(f'    model="{cap.implementation.model}",')
            if cap.implementation.system:
                code_parts.append(f'    system="{cap.implementation.system}",')
            if cap.implementation.template:
                code_parts.append(f'    template="{cap.implementation.template}",')
            code_parts.append(")")
            code_parts.append(f"def {cap.id}(ws: Snapshot):")
            code_parts.append("    pass")
            code_parts.append("")

    # Generate goal
    code_parts.append(f"@goal(scope={agent.goal.scope})")
    code_parts.append("def agent_goal(ws: Snapshot) -> bool:")
    if agent.goal.validation:
        for line in agent.goal.validation.splitlines():
            code_parts.append(f"    {line}")
    else:
        code_parts.append("    return True")
    code_parts.append("")

    # Generate agent builder
    code_parts.append("def build_agent() -> Agent:")
    code_parts.append("    capabilities = [")
    for cap in agent.capabilities:
        code_parts.append(f"        {cap.id},")
    code_parts.append("    ]")
    if agent.budget:
        code_parts.append(
            f"    budget = Budget(tokens={agent.budget.get('tokens', 10000)}, "
            f"ms={agent.budget.get('ms', 60000)}, "
            f"calls={agent.budget.get('calls', 20)})"
        )
        code_parts.append("    return Agent(capabilities, budget=budget)")
    else:
        code_parts.append("    return Agent(capabilities)")

    code = "\n".join(code_parts)
    return {"code": code, "language": "python"}


@app.post("/agents/{agent_name}/test")
def test_agent(agent_name: str, initial_state: Dict[str, Any]) -> Dict[str, Any]:
    """Test agent with initial state (mock execution)"""
    if agent_name not in agents:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Mock execution result
    return {
        "status": "success",
        "steps": 5,
        "final_state": {**initial_state, "output": "Mock result"},
        "execution_time_ms": 1234,
        "trace": [
            {"capability": "step1", "duration_ms": 100},
            {"capability": "step2", "duration_ms": 500},
        ],
    }


@app.get("/marketplace/capabilities")
def list_marketplace_capabilities() -> List[Dict[str, Any]]:
    """List available capabilities in marketplace"""
    return [
        {
            "id": "web-search",
            "name": "Web Search",
            "description": "Search the web using a search API",
            "type": "tool",
            "reads": ["query"],
            "writes": ["results"],
            "tags": ["web", "search", "data"],
        },
        {
            "id": "summarize-text",
            "name": "Summarize Text",
            "description": "Generate summary of long text",
            "type": "llm",
            "reads": ["text"],
            "writes": ["summary"],
            "tags": ["llm", "text", "summarization"],
        },
        {
            "id": "extract-json",
            "name": "Extract Structured Data",
            "description": "Extract structured JSON from unstructured text",
            "type": "llm",
            "reads": ["text"],
            "writes": ["structured_data"],
            "tags": ["llm", "extraction", "json"],
        },
    ]


if __name__ == "__main__":
    import uvicorn

    print("🚀 Starting Ranger Studio API mock server")
    print("📡 API docs: http://localhost:8080/docs")
    uvicorn.run(app, host="0.0.0.0", port=8080)

