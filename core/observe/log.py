"""Clean topological logging for topology agent events."""

import sys
from core.state.types import Event


def emit(event: Event) -> None:
    """
    Emit clean topological logs showing context regions and actions.
    
    Focus on topological flow, not verbose JSON details.
    """
    # Clean topological logging for real-time feedback
    if event.action.endswith(":start"):
        base_action = event.action.replace(":start", "")
        print(f"   🔄 {event.ctx.upper()}: Starting {base_action}")
    elif event.action.endswith(":reason"):
        print(f"      💭 Reasoning...")
    elif event.action.endswith(":act"):
        print(f"      ⚡ Acting...")
    elif event.action.endswith(":observe"):
        print(f"      👁️  Observing...")
    elif event.action.endswith(":success"):
        print(f"      ✅ Success!")
    elif event.action.endswith(":end"):
        base_action = event.action.replace(":end", "")
        print(f"   ✨ {event.ctx.upper()}: Completed {base_action}")
    elif ":" not in event.action:
        # Regular actions (non-ReAct)
        if event.status == "ok":
            print(f"   ✅ {event.ctx.upper()}: {event.action} completed")
        elif event.status == "noop":
            print(f"   ⚪ {event.ctx.upper()}: {event.action} (no changes)")
        elif event.status == "fail":
            print(f"   ❌ {event.ctx.upper()}: {event.action} failed - {event.notes}")
    
    # Still keep JSONL for detailed analysis
    import os
    os.makedirs("logs", exist_ok=True)
    with open("logs/events.jsonl", "a") as f:
        f.write(event.model_dump_json() + "\n")
