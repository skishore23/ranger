"""Main demo runner for topology agent."""

import os
from typing import List, Callable
from dotenv import load_dotenv
from core.state.types import State
from core.context.model import Context
from agents.demo.demo_contexts import CONTEXTS
from core.context.overlaps import compute_overlaps
from agents.demo.demo_actions import get_demo_actions
from agents.demo.demo_goal import goal
from core.engine.scheduler import run
from core.observe.viewer import render_regions_and_path

# Global path tracking for visualization
PATH: List[str] = []


def make_renderer(contexts: List[Context]) -> Callable[[], None]:
    """Create renderer function that updates visualization."""
    overlaps = compute_overlaps(contexts)
    
    def render() -> None:
        # Find currently active contexts and append to path
        active = [ctx.id for ctx in contexts if ctx.is_valid(state)]
        if active:
            # Add first active context to path (simple heuristic)
            PATH.append(active[0])
        
        # Render current state
        render_regions_and_path(contexts, overlaps, PATH, outpath="logs/region_graph.png")
    
    return render


if __name__ == "__main__":
    # Load environment variables from .env file
    load_dotenv()
    
    # Ensure OpenAI API key is set
    os.environ.setdefault("OPENAI_API_KEY", "")
    
    if not os.environ.get("OPENAI_API_KEY"):
        print("Warning: OPENAI_API_KEY not set. LLM actions will fail.")
    
    # Initialize state with demo URL
    state = State(
        data={"url": "https://example.com"},
        meta={"run_id": "demo"}
    )
    
    # Register demo actions
    register_demo()
    
    # Create renderer
    renderer = make_renderer(CONTEXTS)
    
    # Run the topology agent
    print("Starting topology agent demo...")
    run(state, CONTEXTS, goal, renderer, max_ticks=200)
    
    print("Demo completed!")
    print("Check outputs in logs/ directory:")
    print("- logs/events.jsonl: Execution log")
    print("- logs/region_graph.png: Topology visualization") 
    print("- logs/out.txt: Final result")
