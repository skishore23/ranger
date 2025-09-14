"""AST-based intelligent function filtering for test generation."""

import ast
from typing import Dict, List, Set, Any, Optional
from pathlib import Path


def analyze_function_importance(func_node: ast.FunctionDef, source_code: str, module_path: str) -> Dict[str, Any]:
    """Analyze a function's importance and testability using AST."""
    
    importance_score = 0.0
    reasons = []
    
    # 1. Public vs Private functions
    if func_node.name.startswith('_'):
        if func_node.name.startswith('__') and func_node.name.endswith('__'):
            # Dunder methods - important for class behavior
            importance_score += 0.7
            reasons.append("dunder_method")
        else:
            # Private functions - lower priority
            importance_score += 0.2
            reasons.append("private_function")
    else:
        # Public functions - high priority
        importance_score += 0.8
        reasons.append("public_function")
    
    # 2. Function complexity (more complex = more important to test)
    complexity = calculate_cyclomatic_complexity(func_node)
    if complexity > 5:
        importance_score += 0.3
        reasons.append("high_complexity")
    elif complexity > 2:
        importance_score += 0.2
        reasons.append("medium_complexity")
    
    # 3. Has docstring (documented functions are more important)
    if ast.get_docstring(func_node):
        importance_score += 0.2
        reasons.append("documented")
    
    # 4. Function role analysis
    role = analyze_function_role(func_node, source_code)
    if role == "entry_point":
        importance_score += 0.4
        reasons.append("entry_point")
    elif role == "core_logic":
        importance_score += 0.3
        reasons.append("core_logic")
    elif role == "utility":
        importance_score += 0.1
        reasons.append("utility")
    
    # 5. Return type analysis
    if func_node.returns:
        importance_score += 0.1
        reasons.append("typed_return")
    
    # 6. Parameter analysis
    param_count = len(func_node.args.args)
    if param_count > 3:
        importance_score += 0.1
        reasons.append("many_parameters")
    
    # 7. Exception handling (functions with try/except are important)
    has_exception_handling = any(
        isinstance(node, ast.Try) for node in ast.walk(func_node)
    )
    if has_exception_handling:
        importance_score += 0.2
        reasons.append("exception_handling")
    
    # 8. External dependencies (functions calling other modules)
    external_calls = count_external_calls(func_node)
    if external_calls > 0:
        importance_score += 0.1
        reasons.append("external_dependencies")
    
    # Extract complete function signature and source
    func_source = ast.unparse(func_node) if hasattr(ast, 'unparse') else ""
    
    # Extract parameter information
    args_info = []
    for arg in func_node.args.args:
        arg_info = {
            "name": arg.arg,
            "annotation": ast.unparse(arg.annotation) if arg.annotation else None
        }
        args_info.append(arg_info)
    
    # Extract return type
    return_type = ast.unparse(func_node.returns) if func_node.returns else None
    
    # Detect State and Context usage
    has_state = any("State" in ast.unparse(arg.annotation) if arg.annotation else False for arg in func_node.args.args)
    has_context = any("Context" in ast.unparse(arg.annotation) if arg.annotation else False for arg in func_node.args.args)
    
    return {
        "name": func_node.name,
        "importance_score": min(importance_score, 1.0),  # Cap at 1.0
        "reasons": reasons,
        "complexity": complexity,
        "is_public": not func_node.name.startswith('_'),
        "is_testable": is_function_testable(func_node),
        "line_count": func_node.end_lineno - func_node.lineno if func_node.end_lineno else 1,
        "role": role,
        "source": func_source,
        "args": args_info,
        "return_type": return_type,
        "has_state": has_state,
        "has_context": has_context
    }


def calculate_cyclomatic_complexity(func_node: ast.FunctionDef) -> int:
    """Calculate cyclomatic complexity of a function."""
    complexity = 1  # Base complexity
    
    for node in ast.walk(func_node):
        # Decision points that increase complexity
        if isinstance(node, (ast.If, ast.While, ast.For, ast.AsyncFor)):
            complexity += 1
        elif isinstance(node, ast.Try):
            complexity += 1
        elif isinstance(node, ast.ExceptHandler):
            complexity += 1
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            complexity += 1
        elif isinstance(node, ast.BoolOp):
            # And/Or operations add complexity
            complexity += len(node.values) - 1
    
    return complexity


def analyze_function_role(func_node: ast.FunctionDef, source_code: str) -> str:
    """Analyze the role/purpose of a function."""
    
    func_name = func_node.name.lower()
    
    # Entry points
    if func_name in ['main', 'run', 'execute', 'start', 'init', '__init__']:
        return "entry_point"
    
    # Core business logic indicators
    if any(keyword in func_name for keyword in [
        'process', 'handle', 'manage', 'create', 'update', 'delete',
        'calculate', 'compute', 'analyze', 'validate', 'transform'
    ]):
        return "core_logic"
    
    # Utility functions
    if any(keyword in func_name for keyword in [
        'get', 'set', 'format', 'parse', 'convert', 'helper',
        'util', 'tool', 'clean', 'normalize'
    ]):
        return "utility"
    
    # Property-like functions
    if func_name.startswith('is_') or func_name.startswith('has_') or func_name.startswith('can_'):
        return "predicate"
    
    return "general"


def count_external_calls(func_node: ast.FunctionDef) -> int:
    """Count calls to external modules/functions."""
    external_calls = 0
    
    for node in ast.walk(func_node):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                # Method calls like obj.method()
                external_calls += 1
            elif isinstance(node.func, ast.Name):
                # Function calls - check if it's likely external
                func_name = node.func.id
                if func_name not in ['print', 'len', 'str', 'int', 'float', 'bool', 'list', 'dict', 'set']:
                    external_calls += 1
    
    return external_calls


def is_function_testable(func_node: ast.FunctionDef) -> bool:
    """Determine if a function is practically testable."""
    
    # Skip functions that are just pass or raise NotImplementedError
    if len(func_node.body) == 1:
        first_stmt = func_node.body[0]
        if isinstance(first_stmt, ast.Pass):
            return False
        if isinstance(first_stmt, ast.Raise) and isinstance(first_stmt.exc, ast.Call):
            if isinstance(first_stmt.exc.func, ast.Name) and first_stmt.exc.func.id == 'NotImplementedError':
                return False
    
    # Skip abstract methods
    for decorator in func_node.decorator_list:
        if isinstance(decorator, ast.Name) and decorator.id in ['abstractmethod', 'abc.abstractmethod']:
            return False
    
    # All other functions are testable - even simple ones can contain important logic
    return True


def filter_functions_for_testing(module_path: str, source_code: str, min_importance: float = 0.4) -> List[Dict[str, Any]]:
    """Filter functions in a module for test generation based on importance."""
    
    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return []
    
    # Extract module-level imports
    module_imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                module_imports.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                if alias.name == "*":
                    module_imports.append(f"from {module} import *")
                else:
                    module_imports.append(f"from {module} import {alias.name}")
    
    functions = []
    
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            analysis = analyze_function_importance(node, source_code, module_path)
            
            # Only include functions that meet importance threshold and are testable
            if analysis["importance_score"] >= min_importance and analysis["is_testable"]:
                # Add module imports to each function
                analysis["module_imports"] = module_imports
                functions.append(analysis)
    
    # Sort by importance score (highest first)
    functions.sort(key=lambda f: f["importance_score"], reverse=True)
    
    return functions


def get_priority_functions(module_path: str, source_code: str, max_functions: int = 1) -> List[Dict[str, Any]]:
    """Get the highest priority functions for testing."""
    
    all_functions = filter_functions_for_testing(module_path, source_code, min_importance=0.3)
    
    # Prioritize public functions
    public_functions = [f for f in all_functions if f["is_public"]]
    private_functions = [f for f in all_functions if not f["is_public"]]
    
    # Take top public functions first, then important private ones
    priority_functions = public_functions[:max_functions]
    
    if len(priority_functions) < max_functions:
        remaining_slots = max_functions - len(priority_functions)
        priority_functions.extend(private_functions[:remaining_slots])
    
    return priority_functions


def should_skip_function(func_name: str, func_analysis: Dict[str, Any]) -> bool:
    """Determine if a function should be skipped entirely."""
    
    # Skip test functions themselves
    if func_name.startswith('test_'):
        return True
    
    # Skip very low importance functions
    if func_analysis["importance_score"] < 0.2:
        return True
    
    # Skip known problematic function types
    problematic_patterns = [
        'render_', 'plot_', 'draw_', 'visualize_',  # Visualization functions
        '_mock_', '_stub_', '_fake_',  # Test utilities
        'deprecated_', 'legacy_',  # Deprecated functions
        'display_', 'show_', 'print_',  # Display functions
    ]
    
    if any(pattern in func_name.lower() for pattern in problematic_patterns):
        return True
    
    # Skip functions with very high complexity (likely visualization or complex algorithms)
    if func_analysis.get("complexity", 0) > 15:
        return True
    
    # Skip functions that are likely matplotlib/GUI related
    matplotlib_indicators = [
        'plt', 'matplotlib', 'figure', 'axis', 'canvas', 'widget',
        'layout', 'position', 'triangle', 'graph', 'node', 'edge'
    ]
    
    # Check if function name suggests matplotlib usage
    if any(indicator in func_name.lower() for indicator in matplotlib_indicators):
        return True
    
    return False


def analyze_module_testability(module_path: str, source_code: str) -> Dict[str, Any]:
    """Analyze overall module testability and provide recommendations."""
    
    all_functions = filter_functions_for_testing(module_path, source_code, min_importance=0.1)
    priority_functions = get_priority_functions(module_path, source_code)
    
    total_functions = len(all_functions)
    testable_functions = len([f for f in all_functions if f["is_testable"]])
    public_functions = len([f for f in all_functions if f["is_public"]])
    
    avg_importance = sum(f["importance_score"] for f in all_functions) / max(total_functions, 1)
    avg_complexity = sum(f["complexity"] for f in all_functions) / max(total_functions, 1)
    
    return {
        "module_path": module_path,
        "total_functions": total_functions,
        "testable_functions": testable_functions,
        "public_functions": public_functions,
        "priority_functions": len(priority_functions),
        "avg_importance_score": avg_importance,
        "avg_complexity": avg_complexity,
        "recommended_functions": priority_functions,
        "module_testability_score": min(avg_importance * (testable_functions / max(total_functions, 1)), 1.0)
    }
