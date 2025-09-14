"""
Testwriter Agent - Autonomous Test Generation

A topological agent that generates comprehensive test suites using:
- AST-based function analysis
- Template-driven test generation  
- LLM-powered test repair
- Coverage-driven target selection

The agent operates through context-driven execution, transitioning between
states like repo_ready → coverage_baselined → targets_chosen → tests_generated.
"""
