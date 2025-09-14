"""
Ranger Core - Topological Agent Framework

A pure functional framework for building agents as directed graphs where
execution emerges from context transitions, not imperative control flow.

Key principles:
- Contexts define regions with validity predicates
- Actions are pure functions with clear contracts  
- Loops emerge from context transitions
- ReAct patterns arise naturally from refinement contexts
- Strict typing and fail-fast principles throughout

Architecture:
- core.context: Context modeling and validation
- core.action: Action definitions and execution
- core.engine: Scheduler and goal management
- core.state: Immutable state management
- core.llm: Language model adapters
- core.observe: Logging and visualization
"""

__version__ = "0.1.0"
