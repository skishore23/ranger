"""Simple function-based test generation action (no ReAct overhead)."""

import ast
import time
from pathlib import Path
from typing import Dict, List, Optional, Any
from core.state.types import State, JSONValue, Delta
from core.action.base import Action
from core.action.safe import Either, Left, Right, safe_file_write, safe_file_read, SafeError
from core.llm.openai_adapter import OpenAIAdapter
from .test_template import analyze_function_dependencies, build_template_prompt
from .ast_filter import get_priority_functions, should_skip_function, analyze_function_importance


def analyze_function(func_node: ast.FunctionDef, source_code: str) -> Dict[str, Any]:
    """Analyze a function to determine what kind of test should be written."""
    lines = source_code.split('\n')
    func_source = '\n'.join(lines[func_node.lineno-1:func_node.end_lineno])
    
    # Basic analysis
    has_return = any(isinstance(node, ast.Return) for node in ast.walk(func_node))
    has_conditionals = any(isinstance(node, (ast.If, ast.While, ast.For)) for node in ast.walk(func_node))
    has_exceptions = any(isinstance(node, (ast.Raise, ast.Try)) for node in ast.walk(func_node))
    
    # Count parameters
    param_count = len(func_node.args.args)
    
    # Determine complexity
    complexity = "simple"
    if has_conditionals or has_exceptions or param_count > 3:
        complexity = "complex"
    elif param_count > 1 or has_return:
        complexity = "medium"
    
    return {
        "name": func_node.name,
        "args": [arg.arg for arg in func_node.args.args],
        "has_return": has_return,
        "has_conditionals": has_conditionals,
        "has_exceptions": has_exceptions,
        "param_count": param_count,
        "complexity": complexity,
        "source": func_source,
        "lineno": func_node.lineno
    }


class SimpleGenerateTestAction(Action):
    """Simple action to generate tests for functions (no ReAct overhead)."""
    
    name: str = "simple_generate_test"
    locks: List[str] = ["generation"]
    timeout_s: int = 120
    max_retries: int = 1
    allow: bool = True
    
    def pre(self, state: State) -> bool:
        """Check if we need to generate tests (simple: no tests generated yet)."""
        return (
            "api_info" in state.data and 
            "target_modules" in state.data and
            "generated_tests" not in state.data  # Simple: generate once, then stop
        )
    
    def args(self, state: State) -> Dict[str, JSONValue]:
        """Extract arguments for generation."""
        return {
            "output_dir": state.data["output_dir"],
            "repo_path": state.data["repo_path"]
        }
    
    def run(self, state: State, **kwargs: JSONValue) -> Optional[Delta]:
        """Generate tests using functional Either patterns."""
        api_info = state.data.get("api_info", {})
        target_modules = state.data.get("target_modules", [])
        output_dir = Path(str(kwargs.get("output_dir", "")))
        repo_path = Path(str(kwargs.get("repo_path", "")))
        
        if not isinstance(api_info, dict) or not isinstance(target_modules, list):
            return None
        
        # Functional pipeline for test generation
        from core.action.safe import traverse_either
        
        result = (
            traverse_either(target_modules, lambda module: self._generate_tests_for_module(
                module, api_info, repo_path, output_dir, state
            ))
            .fold(
                left=self._handle_generation_error,
                right=self._handle_generation_success
            )
        )
        
        return result
    
    def _generate_tests_for_module(self, module_info: Any, api_info: Dict, repo_path: Path, output_dir: Path, state: State) -> Either[SafeError, Dict[str, Any]]:
        """Generate tests for a single module functionally."""
        if not isinstance(module_info, dict):
            return Left(SafeError(
                message="Invalid module info format",
                error_type="ValidationError",
                context={"module_info": str(module_info)}
            ))
        
        module_path = module_info.get("path", "")
        if not module_path or module_path not in api_info:
            return Right({"module_path": module_path, "tests_generated": 0, "skipped": True})
        
        module_api = api_info[module_path]
        if "error" in module_api:
            return Right({"module_path": module_path, "tests_generated": 0, "error": True})
        
        full_path = repo_path / module_path
        
        return (
            safe_file_read(full_path)
            .flat_map(lambda source_code: self._process_module_functions(
                module_path, source_code, output_dir, state
            ))
        )
    
    def _process_module_functions(self, module_path: str, source_code: str, output_dir: Path, state: State) -> Either[SafeError, Dict[str, Any]]:
        """Process functions in a module functionally."""
        try:
            # Get priority functions using AST analysis
            priority_functions = get_priority_functions(module_path, source_code, max_functions=3)
            generated_tests = state.data.get("generated_tests", {}).copy()
            tests_generated = 0
            
            for func_analysis in priority_functions:
                func_name = func_analysis["name"]
                
                # Skip if function should be avoided
                if should_skip_function(func_name, func_analysis):
                    continue
                
                test_key = f"test_{module_path.replace('/', '_').replace('.py', '')}_{func_name}.py"
                if test_key in generated_tests:
                    continue  # Skip existing tests
                
                # Skip functions that have failed repair multiple times
                function_attempts = state.data.get("function_repair_attempts", {})
                if function_attempts.get(test_key, 0) >= 3:
                    continue  # Skip functions that are too difficult to repair
                
                # Generate test for this function
                test_result = self._generate_single_test(
                    module_path, func_analysis, output_dir, generated_tests
                )
                
                if test_result:
                    generated_tests.update(test_result)
                    tests_generated += 1
                    print(f"   ✅ Generated test {tests_generated}")
            
            return Right({
                "module_path": module_path,
                "tests_generated": tests_generated,
                "generated_tests": generated_tests
            })
            
        except Exception as e:
            return Left(SafeError(
                message=f"Function processing failed: {str(e)}",
                error_type="ProcessingError",
                context={"module_path": module_path}
            ))
    
    def _generate_single_test(self, module_path: str, func_analysis: Dict, output_dir: Path, generated_tests: Dict) -> Optional[Dict]:
        """Generate a single test functionally."""
        try:
            func_name = func_analysis["name"]
            test_key = f"test_{module_path.replace('/', '_').replace('.py', '')}_{func_name}.py"
            
            print(f"🎯 Selected priority function: {func_name} (importance: {func_analysis['importance_score']:.2f})")
            print(f"   📊 Reasons: {', '.join(func_analysis['reasons'])}")
            
            # Generate test content using template system
            # Use AST-extracted function information directly
            dependencies = {
                "params": func_analysis.get("args", []),
                "return_annotation": func_analysis.get("return_type", "Any"),
                "complexity": func_analysis.get("complexity", "simple"),
                "has_state": func_analysis.get("has_state", False),
                "has_context": func_analysis.get("has_context", False),
                "used_types": [],
                "imports": func_analysis.get("module_imports", [])
            }
            prompt = build_template_prompt(func_analysis, module_path, dependencies)
            
            # Use LLM to generate test
            llm = OpenAIAdapter()
            raw_response = llm.chat(
                [{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=1500
            )
            
            # Strip markdown formatting from LLM response
            test_code = self._clean_llm_response(raw_response)
            
            # Write test file to tests/ subdirectory
            tests_dir = output_dir / "tests"
            test_file_path = tests_dir / test_key
            test_file_path.parent.mkdir(parents=True, exist_ok=True)
            
            write_result = safe_file_write(test_file_path, test_code)
            if write_result.tag == "Left":
                print(f"❌ Failed to write test file: {write_result.left.message}")
                return None
            
            return {
                test_key: {
                    "function": func_name,
                    "module": module_path,
                    "file_path": str(test_file_path),
                    "status": "generated",
                    "timestamp": time.time()
                }
            }
            
        except Exception as e:
            print(f"❌ Test generation failed for {func_name}: {str(e)}")
            return None
    
    def _clean_llm_response(self, raw_response: str) -> str:
        """Clean LLM response by removing markdown formatting."""
        # Remove markdown code blocks
        cleaned = raw_response.strip()
        
        # Remove opening ```python or ```
        if cleaned.startswith('```python'):
            cleaned = cleaned[9:]
        elif cleaned.startswith('```'):
            cleaned = cleaned[3:]
        
        # Remove closing ```
        if cleaned.endswith('```'):
            cleaned = cleaned[:-3]
        
        return cleaned.strip()
    
    def _handle_generation_error(self, error: SafeError) -> Delta:
        """Handle generation errors - fail fast."""
        raise RuntimeError(f"Test generation failed: {error.message}")
    
    def _handle_generation_success(self, module_results: List[Dict[str, Any]]) -> Delta:
        """Handle successful test generation."""
        all_generated_tests = {}
        total_tests = 0
        
        for result in module_results:
            if "generated_tests" in result:
                all_generated_tests.update(result["generated_tests"])
                total_tests += result.get("tests_generated", 0)
        
        if total_tests > 0:
            print(f"🎯 BULK GENERATION COMPLETE: {total_tests} tests generated")
            return {
                "set": {
                    "generated_tests": all_generated_tests,
                    "tests_written_ts": time.time()
                }
            }
        
        return None  # No functions need testing
    
    def _generate_test_for_function_old(
        self, 
        module_path: str, 
        func_info: Dict[str, Any], 
        output_dir: str, 
        repo_path: str,
        generated_tests: Dict[str, Any]
    ) -> Optional[Delta]:
        """Generate test code for a specific function."""
        try:
            # Read the source file to get full function analysis
            repo_path_obj = Path(repo_path)
            full_path = repo_path_obj / module_path
            
            with open(full_path, 'r') as f:
                source_code = f.read()
            
            # Parse AST to get detailed function info
            tree = ast.parse(source_code)
            
            # Find the specific function node
            func_node = None
            for node in ast.walk(tree):
                if (isinstance(node, ast.FunctionDef) and 
                    node.name == func_info["name"] and
                    node.lineno == func_info["lineno"]):
                    func_node = node
                    break
            
            if not func_node:
                return None
            
            # Analyze function in detail
            detailed_func_info = analyze_function(func_node, source_code)
            
            # Analyze dependencies for smart template generation
            dependencies = analyze_function_dependencies(func_node, source_code, module_path)
            
            # Generate template-based prompt
            prompt = build_template_prompt(detailed_func_info, module_path, dependencies)
            
            # Call LLM to generate test
            llm = OpenAIAdapter()
            response = llm.chat(
                [{"role": "user", "content": prompt}],
                force_json=False,  # Template approach returns raw Python code
                temperature=0.1,
                max_tokens=2000
            )
            
            # Clean up response (remove markdown code blocks if present)
            test_code = response.strip()
            if test_code.startswith("```python"):
                test_code = test_code[9:]
            if test_code.startswith("```"):
                test_code = test_code[3:]
            if test_code.endswith("```"):
                test_code = test_code[:-3]
            test_code = test_code.strip()
            
            # Validate the generated test
            if not test_code or "def test_" not in test_code:
                return None
            
            # Auto-add time import if needed
            if ("time." in test_code or "time(" in test_code) and "import time" not in test_code:
                test_code = "import time\n" + test_code
            
            # Create test file
            func_name = detailed_func_info["name"]
            test_filename = f"test_{module_path.replace('/', '_').replace('.py', '')}_{func_name}.py"
            
            output_path = Path(output_dir)
            test_dir = output_path / "tests" / "generated"
            test_dir.mkdir(parents=True, exist_ok=True)
            
            test_file_path = test_dir / test_filename
            with open(test_file_path, 'w') as f:
                f.write(test_code)
            
            # Update generated tests tracking
            new_generated_tests = generated_tests.copy()
            new_generated_tests[test_filename] = {
                "module": module_path,
                "function": func_name,
                "status": "generated",
                "path": str(test_file_path)
            }
            
            return {"set": {"generated_tests": new_generated_tests}}
            
        except Exception as e:
            print(f"Error generating test for {func_info.get('name', 'unknown')}: {e}")
            return None
