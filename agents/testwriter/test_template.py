"""Smart test template generation using AST analysis."""

import ast
from typing import Dict, List, Any, Set
from pathlib import Path


def analyze_function_dependencies(func_node: ast.FunctionDef, source_code: str, module_path: str) -> Dict[str, Any]:
    """Analyze function to determine required imports and test structure."""
    
    # Parse the entire module to understand dependencies
    tree = ast.parse(source_code)
    
    # Find all imports in the module
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                imports.append(f"{module}.{alias.name}" if module else alias.name)
    
    # Analyze function parameters
    params = []
    for arg in func_node.args.args:
        param_info = {
            "name": arg.arg,
            "annotation": ast.unparse(arg.annotation) if arg.annotation else None
        }
        params.append(param_info)
    
    # Analyze return type
    return_annotation = None
    if func_node.returns:
        return_annotation = ast.unparse(func_node.returns)
    
    # Detect what types are used in the function
    used_types = set()
    for node in ast.walk(func_node):
        if isinstance(node, ast.Name):
            used_types.add(node.id)
        elif isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name):
                used_types.add(f"{node.value.id}.{node.attr}")
    
    return {
        "params": params,
        "return_annotation": return_annotation,
        "imports": imports,
        "used_types": list(used_types),
        "has_state": any("State" in t for t in used_types),
        "has_context": any("Context" in t for t in used_types),
        "complexity": _estimate_complexity(func_node)
    }


def _estimate_complexity(func_node: ast.FunctionDef) -> str:
    """Estimate function complexity for test generation."""
    complexity_score = 0
    
    for node in ast.walk(func_node):
        if isinstance(node, (ast.If, ast.While, ast.For)):
            complexity_score += 1
        elif isinstance(node, (ast.Try, ast.ExceptHandler)):
            complexity_score += 1
        elif isinstance(node, ast.FunctionDef) and node != func_node:
            complexity_score += 1
    
    if complexity_score == 0:
        return "simple"
    elif complexity_score <= 3:
        return "medium"
    else:
        return "complex"


def generate_test_template(func_info: Dict[str, Any], module_path: str, dependencies: Dict[str, Any]) -> str:
    """Generate a test template with blanks for LLM to fill."""
    
    func_name = func_info["name"]
    module_import = module_path.replace("/", ".").replace(".py", "")
    if not module_import.startswith("core."):
        module_import = f"core.{module_import}"
    
    # Build imports automatically
    imports = ["import pytest"]
    imports.append(f"from {module_import} import {func_name}")
    
    if dependencies["has_state"]:
        imports.append("from core.state.types import State")
    if dependencies["has_context"]:
        imports.append("from core.context.model import Context")
    
    # Add common imports based on function analysis
    if any("time" in imp for imp in dependencies["imports"]):
        imports.append("import time")
    if any("json" in imp for imp in dependencies["imports"]):
        imports.append("import json")
    if any("pathlib" in imp for imp in dependencies["imports"]):
        imports.append("from pathlib import Path")
    
    imports_section = "\n".join(imports)
    
    # Generate test template based on complexity
    if dependencies["complexity"] == "simple":
        test_template = _generate_simple_template(func_name, dependencies)
    elif dependencies["complexity"] == "medium":
        test_template = _generate_medium_template(func_name, dependencies)
    else:
        test_template = _generate_complex_template(func_name, dependencies)
    
    return f"""# Generated test for function: {func_name}
# Module: {module_path}

{imports_section}

{test_template}
"""


def _generate_simple_template(func_name: str, dependencies: Dict[str, Any]) -> str:
    """Generate template for simple functions."""
    
    # Determine parameter setup
    param_setup = ""
    if dependencies["has_state"]:
        param_setup += "    state = State(data={}, meta={})\n"
    if dependencies["has_context"]:
        param_setup += "    context = Context(id='test', label='test', is_valid=lambda s: True, resources=['test'])\n"
    
    return f"""def test_{func_name}_normal_case():
    \"\"\"Test {func_name} with normal inputs.\"\"\"
{param_setup}    # TODO: Set up test parameters
    # TODO: Call function with test inputs
    # TODO: Assert expected results
    pass


def test_{func_name}_edge_cases():
    \"\"\"Test {func_name} with edge case inputs.\"\"\"
{param_setup}    # TODO: Test edge cases (empty inputs, None values, etc.)
    pass
"""


def _generate_medium_template(func_name: str, dependencies: Dict[str, Any]) -> str:
    """Generate template for medium complexity functions."""
    
    param_setup = ""
    if dependencies["has_state"]:
        param_setup += "    state = State(data={}, meta={})\n"
    if dependencies["has_context"]:
        param_setup += "    context = Context(id='test', label='test', is_valid=lambda s: True, resources=['test'])\n"
    
    return f"""def test_{func_name}_normal_case():
    \"\"\"Test {func_name} with normal inputs.\"\"\"
{param_setup}    # TODO: Set up test parameters
    # TODO: Call function and verify results
    pass


def test_{func_name}_edge_cases():
    \"\"\"Test {func_name} with edge case inputs.\"\"\"
{param_setup}    # TODO: Test boundary conditions
    pass


def test_{func_name}_error_conditions():
    \"\"\"Test {func_name} error handling.\"\"\"
{param_setup}    # TODO: Test invalid inputs that should raise exceptions
    # Use pytest.raises(ExceptionType):
    pass
"""


def _generate_complex_template(func_name: str, dependencies: Dict[str, Any]) -> str:
    """Generate template for complex functions."""
    
    param_setup = ""
    if dependencies["has_state"]:
        param_setup += "    state = State(data={}, meta={})\n"
    if dependencies["has_context"]:
        param_setup += "    context = Context(id='test', label='test', is_valid=lambda s: True, resources=['test'])\n"
    
    return f"""def test_{func_name}_normal_case():
    \"\"\"Test {func_name} with normal inputs.\"\"\"
{param_setup}    # TODO: Set up test parameters
    # TODO: Call function and verify results
    pass


def test_{func_name}_edge_cases():
    \"\"\"Test {func_name} with edge case inputs.\"\"\"
{param_setup}    # TODO: Test boundary conditions
    pass


def test_{func_name}_error_conditions():
    \"\"\"Test {func_name} error handling.\"\"\"
{param_setup}    # TODO: Test invalid inputs
    # Use pytest.raises(ExceptionType):
    pass


def test_{func_name}_state_changes():
    \"\"\"Test {func_name} state modifications.\"\"\"
{param_setup}    # TODO: Test how function modifies state/context
    pass


def test_{func_name}_integration():
    \"\"\"Test {func_name} integration with other components.\"\"\"
{param_setup}    # TODO: Test function in realistic scenarios
    pass
"""


def build_template_prompt(func_info: Dict[str, Any], module_path: str, dependencies: Dict[str, Any]) -> str:
    """Build a focused prompt for filling in the test template with full function context."""
    
    template = generate_test_template(func_info, module_path, dependencies)
    
    # Get full function source with complete context
    full_function_source = func_info.get('source', f"def {func_info['name']}(...): ...")
    
    return f"""You are filling in a test template for a Python function. Study the complete function definition and replace all TODO comments with actual test code.

COMPLETE FUNCTION DEFINITION:
```python
{full_function_source}
```

FUNCTION ANALYSIS:
- Module: {module_path}
- Name: {func_info['name']}
- Parameters: {dependencies.get('params', [])}
- Return Type: {dependencies.get('return_annotation', 'Any')}
- Complexity: {dependencies.get('complexity', 'simple')}
- Has State objects: {dependencies.get('has_state', False)}
- Has Context objects: {dependencies.get('has_context', False)}
- Used types: {dependencies.get('used_types', [])}

TEMPLATE TO COMPLETE:
```python
{template}
```

RULES:
- Study the COMPLETE FUNCTION DEFINITION above to understand exactly what parameters it expects
- Replace ALL TODO comments with working test code that matches the function signature
- Use realistic test data based on the actual function parameters and types shown above
- Test both success and failure cases where appropriate
- Keep existing imports and structure
- Output ONLY the completed Python code (no markdown, no explanations)
- Make assertions specific and meaningful
- Ensure all test functions start with 'def test_'
- Remove all TODO comments - replace with actual code
- If function expects Context objects, create them with Context(id="test", label="test", is_valid=lambda s: True, resources=["test"])
- If function expects State objects, create them with State(data={{...}}, meta={{}})

Complete the template by replacing ALL TODO comments:"""
