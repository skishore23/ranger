from core.sdk import step, tool, llm, Agent, goal
from core.merge import WriteSpec, MergeMode
from agents.testwriter.schemas import TESTS_GEN_SCHEMA
from agents.testwriter.adapters import parse_pytest, parse_coverage, parse_pyright
from core.llm.provider import OpenAIProvider
import os, json, ast, subprocess, shlex


@tool(inputs=["codebase.path"], outputs=["env.loaded"])
def load_env(ws):
    print("🔧 Loading environment variables...")
    root = ws.value("codebase.path")
    env_path = os.path.join(root, ".env")
    loaded = {}
    if os.path.exists(env_path):
        try:
            with open(env_path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k = k.strip(); v = v.strip().strip('"').strip("'")
                    os.environ[k] = v
                    loaded[k] = True
            print(f"✅ Loaded {len(loaded)} environment variables")
        except Exception as e:
            print(f"❌ Failed to load .env: {e}")
    else:
        print("⚠️  No .env file found")
    return {"env.loaded": bool(loaded)}


@tool(inputs=["codebase.path"], outputs=["deps.lock"])
def sync_deps(ws):
    print("📦 Checking system dependencies...")
    root = ws.value("codebase.path")
    
    # Check if required tools are available
    def check_cmd(cmd: str) -> bool:
        try:
            result = subprocess.run(shlex.split(cmd), cwd=root, capture_output=True, text=True)
            return result.returncode == 0
        except Exception:
            return False
    
    checks = {
        "pytest": "python -m pytest --version",
        "coverage": "python -c 'import coverage; print(coverage.__version__)'",
        "openai": "python -c 'import openai; print(openai.__version__)'"
    }
    
    missing = []
    for name, cmd in checks.items():
        if check_cmd(cmd):
            print(f"   ✅ {name} available")
        else:
            print(f"   ❌ {name} missing")
            missing.append(name)
    
    if missing:
        print(f"⚠️  Missing dependencies: {missing}")
        print("   Install with: pip install pytest pytest-cov coverage openai")
    
    print("✅ Dependency check complete")
    return {"deps.lock": {"ok": len(missing) == 0, "missing": missing}}


@step(inputs=["codebase.path", "target.file"], outputs=["repo.ast", "top.package"])
def index_repo(ws):
    print("📋 Indexing repository...")
    root = ws.value("codebase.path")
    target_file_data = ws.value("target.file")
    files = []
    pkgnames = set()
    
    # Handle both string and dict formats for target.file
    if isinstance(target_file_data, dict):
        target_file = target_file_data["file"]
        reasoning = target_file_data.get("reasoning", "No reasoning provided")
        print(f"   🎯 LLM selected: {target_file}")
        print(f"   💡 Reasoning: {reasoning}")
    else:
        target_file = target_file_data
        print(f"   🎯 Indexing single file: {target_file}")
    
    if os.path.exists(os.path.join(root, target_file)):
        files.extend(_index_single_file(root, target_file))
    else:
        print(f"   ⚠️  File not found: {target_file}")
    
    # Extract package names
    for file_info in files:
        rel_path = file_info["path"]
        top = rel_path.split(os.sep)[0]
        if top and top != "tests":
            pkgnames.add(top.replace("/", "."))
    
    top_pkg = sorted(list(pkgnames))[0] if pkgnames else "core"
    print(f"✅ Indexed {len(files)} files")
    return {"repo.ast": {"files": files}, "top.package": top_pkg}


def _index_single_file(root, target_file):
    """Helper: Index a single file and extract its functions/classes"""
    files = []
    try:
        src = open(target_file, "r", encoding="utf-8").read()
        if len(src) > 3000:  # Skip large files
            print(f"   ⚠️  Skipping large file: {target_file}")
            return files
            
        tree = ast.parse(src)
        
        # Extract function signatures
        funs = []
        for n in ast.walk(tree):
            if isinstance(n, ast.FunctionDef) and not n.name.startswith("_"):
                args = []
                for arg in n.args.args:
                    args.append(arg.arg)
                sig = f"{n.name}({', '.join(args)})"
                funs.append({"name": n.name, "signature": sig})
        
        # Extract class info
        clss = []
        for n in ast.walk(tree):
            if isinstance(n, ast.ClassDef) and not n.name.startswith("_"):
                methods = []
                for item in n.body:
                    if isinstance(item, ast.FunctionDef) and not item.name.startswith("_"):
                        args = []
                        for arg in item.args.args:
                            args.append(arg.arg)
                        sig = f"{item.name}({', '.join(args)})"
                        methods.append({"name": item.name, "signature": sig})
                clss.append({"name": n.name, "methods": methods})
        
        if funs or clss:
            rel = os.path.relpath(target_file, root)
            files.append({"path": rel, "functions": funs, "classes": clss})
            print(f"   📄 {rel}: {len(funs)} functions, {len(clss)} classes")
    
    except Exception as e:
        print(f"   ❌ Failed to parse {target_file}: {e}")
    
    return files


@tool(inputs=["codebase.path"], outputs=["lsp.diagnostics"])
def run_pyright(ws):
    root = ws.value("codebase.path")
    p = subprocess.run(["python", "-m", "pyright", "--outputjson"], cwd=root, capture_output=True, text=True)
    diags = parse_pyright(p.stdout or p.stderr)
    return {"lsp.diagnostics": diags}


@llm(
    inputs=["repo.ast", "lsp.diagnostics", "top.package"], 
    outputs=["tests.gen"],
    provider=OpenAIProvider(),
    model="gpt-4o-mini",
    temperature=0.0,
    max_tokens=1500,
    template=open(os.path.join(os.path.dirname(__file__), "prompts", "gen_tests.jinja"), "r").read(),
    schema=TESTS_GEN_SCHEMA,
    write_specs={"tests.gen": WriteSpec(merge_mode=MergeMode.MERGE_JSON)},
    map=lambda ws: {
        "repo_ast": ws.value("repo.ast"),
        "lsp_diags": ws.value("lsp.diagnostics"),
        "top_pkg": ws.value("top.package"),
        "schema_name": "TESTS_GEN_SCHEMA"
    }
)
def generate_tests(ws): 
    print("🤖 Generating tests using LLM...")
    pass


@tool(inputs=["tests.gen", "codebase.path"], outputs=["tests.fs", "dirty.tests"])
def write_tests(ws):
    print("✍️  Writing test files...")
    root = ws.value("codebase.path")
    tests_gen = ws.value("tests.gen")
    
    if not isinstance(tests_gen, dict) or "files" not in tests_gen:
        raise ValueError(f"Invalid tests.gen format: {tests_gen}")
    
    files = tests_gen["files"]
    paths = []
    content_hash = ""
    
    for f in files:
        rel = f["path"]
        if not rel.startswith("tests/"):
            raise ValueError(f"Unsafe test path: {rel}")
        abspath = os.path.join(root, rel)
        os.makedirs(os.path.dirname(abspath), exist_ok=True)
        
        # Content should be a string (Python code)
        content = f["content"]
        if not isinstance(content, str):
            raise ValueError(f"Test content must be a string, got {type(content)}: {content}")
            
        with open(abspath, "w", encoding="utf-8") as fh:
            fh.write(content)
        paths.append(rel)
        content_hash += content  # Accumulate content for hash
        print(f"   Wrote: {rel} ({len(content)} chars)")
    
    # Include content hash so run_tests becomes ready when content changes
    import hashlib
    content_digest = hashlib.sha256(content_hash.encode()).hexdigest()[:8]
    
    print(f"✅ Wrote {len(paths)} test files (content hash: {content_digest})")
    return {
        "tests.fs": {
            "paths": paths, 
            "content_hash": content_digest,
            "timestamp": __import__('time').time()
        }, 
        "dirty.tests": True
    }


@tool(inputs=["tests.fs", "codebase.path"], outputs=["run.result", "coverage.report"])
def run_tests(ws):
    print("🧪 Running tests...")
    root = ws.value("codebase.path")
    env = os.environ.copy()
    env["PYTHONPATH"] = root + os.pathsep + env.get("PYTHONPATH", "")
    args = ["python", "-m", "pytest", "-q", "--maxfail=1", "--disable-warnings"]
    
    # Handle both old format (list) and new format (dict with paths)
    tests_fs = ws.value("tests.fs") if ws.exists("tests.fs") else []
    if isinstance(tests_fs, dict):
        test_paths = tests_fs.get("paths", [])
        content_hash = tests_fs.get("content_hash", "unknown")
        print(f"   Test content hash: {content_hash}")
    else:
        test_paths = tests_fs if isinstance(tests_fs, list) else []
    
    if test_paths:
        args += test_paths[:2]
        print(f"   Running tests: {', '.join(test_paths[:2])}")
    
    p = subprocess.run(args, cwd=root, capture_output=True, text=True, env=env)
    rr = parse_pytest(p.stdout, p.stderr, p.returncode)
    
    print(f"   Test result: {'✅ PASSED' if rr.get('passed') else '❌ FAILED'}")
    
    # Get coverage for entire codebase (not just tested files)
    print("📊 Checking coverage...")
    # Include all core files in coverage report, not just tested ones
    c = subprocess.run(["python", "-m", "coverage", "report", "-m", "--include=core/*"], cwd=root, capture_output=True, text=True, env=env)
    cov = parse_coverage(c.stdout or c.stderr)
    
    print(f"   Coverage: {cov['total']:.1f}% (entire core/ directory)")
    
    return {"run.result": rr, "coverage.report": {"total": cov["total"]}}


@tool(inputs=["run.result", "codebase.path"], outputs=["deps.missing", "import.fixes", "dirty.tests"])
def repair_imports_and_deps(ws):
    rr = ws.value("run.result") or {}
    stdout = rr.get("stdout", "") + rr.get("stderr", "")
    missing = []
    fixes = []
    for line in stdout.splitlines():
        if "ModuleNotFoundError: No module named" in line:
            mod = line.split("named", 1)[1].strip().strip("'\"")
            missing.append(mod)
        if "attempted relative import with no known parent package" in line:
            fixes.append("Ensure absolute imports in tests.")
    if missing:
        root = ws.value("codebase.path")
        cmd = ["python", "-m", "pip", "install", *missing]
        subprocess.run(cmd, cwd=root, capture_output=True, text=True)
    return {"deps.missing": missing, "import.fixes": fixes, "dirty.tests": bool(missing or fixes)}


@step(inputs=["run.result"], outputs=["needs_repair", "failure_analysis"])
def check_test_failures(ws):
    """Step: Analyze test failures in detail"""
    rr = ws.value("run.result")
    needs_repair = bool(rr and not rr.get("passed", False))
    
    failure_analysis = {"failed_tests": [], "error_patterns": []}
    
    if needs_repair and rr:
        stdout = rr.get("stdout", "")
        stderr = rr.get("stderr", "")
        
        # Parse pytest output for specific failures
        lines = (stdout + "\n" + stderr).split("\n")
        current_test = None
        current_error = []
        
        for line in lines:
            # Detect test failure headers like "FAILED tests/test_validate.py::test_ensure"
            if "FAILED " in line and "::" in line:
                if current_test:
                    failure_analysis["failed_tests"].append({
                        "test": current_test,
                        "error": "\n".join(current_error)
                    })
                current_test = line.split("FAILED ")[1].strip()
                current_error = []
            elif current_test and ("Error" in line or "Exception" in line or "TypeError" in line):
                current_error.append(line.strip())
        
        # Add the last test if any
        if current_test:
            failure_analysis["failed_tests"].append({
                "test": current_test,
                "error": "\n".join(current_error)
            })
        
        print(f"   Analyzed {len(failure_analysis['failed_tests'])} failing tests")
    
    return {"needs_repair": needs_repair, "failure_analysis": failure_analysis}

@step(inputs=["failure_analysis", "repo.ast"], outputs=["source_context"])
def analyze_source_code(ws):
    """Step: Read source code to understand actual function behavior"""
    failure_analysis = ws.value("failure_analysis")
    repo_ast = ws.value("repo.ast")
    
    source_context = {"functions": {}}
    
    if not failure_analysis.get("failed_tests"):
        return {"source_context": source_context}
    
    print("🔍 Analyzing source code for failing tests...")
    
    # For each failed test, try to understand the source functions
    for failure in failure_analysis["failed_tests"]:
        test_name = failure["test"]
        if "::" in test_name:
            test_file, test_func = test_name.split("::")
            
            # Try to read the actual source code being tested
            for file_info in repo_ast.get("files", []):
                file_path = file_info["path"]
                if "validate" in test_file and "validate" in file_path:
                    try:
                        with open(file_path, 'r') as f:
                            source_code = f.read()
                        
                        # Extract function definitions and their exception types
                        import re
                        for func_info in file_info.get("functions", []):
                            func_name = func_info["name"]
                            # Find what exceptions this function raises
                            pattern = rf"def {func_name}.*?(?=def|\Z)"
                            match = re.search(pattern, source_code, re.DOTALL)
                            if match:
                                func_body = match.group(0)
                                # Look for raise statements
                                raises = re.findall(r'raise\s+(\w+)', func_body)
                                source_context["functions"][func_name] = {
                                    "signature": func_info["signature"],
                                    "raises": raises,
                                    "body_snippet": func_body[:200] + "..." if len(func_body) > 200 else func_body
                                }
                                print(f"   {func_name} raises: {raises}")
                    except Exception as e:
                        print(f"   Failed to analyze {file_path}: {e}")
    
    return {"source_context": source_context}

@llm(
    inputs=["failure_analysis", "source_context", "tests.fs", "needs_repair"], 
    outputs=["tests.gen"],
    provider=OpenAIProvider(),
    model="gpt-4o-mini", 
    temperature=0.0,
    max_tokens=1500,
    template=open(os.path.join(os.path.dirname(__file__), "prompts", "repair_tests.jinja"), "r").read(),
    schema=TESTS_GEN_SCHEMA,
    write_specs={"tests.gen": WriteSpec(merge_mode=MergeMode.MERGE_JSON)},
    map=lambda ws: {
        "failure_analysis": ws.value("failure_analysis"),
        "source_context": ws.value("source_context"),
        "current_tests": _read_current_test_files(ws),  # Include current test content
        "schema_name": "TESTS_GEN_SCHEMA"
    }
)
def repair_tests_llm(ws): 
    print("🔧 Repairing tests based on detailed failure analysis...")
    pass

@step(inputs=["needs_repair", "tests.gen"], outputs=["dirty.tests"])
def mark_tests_dirty(ws):
    """Step: Mark tests as dirty if repairs were made"""
    needs_repair = ws.value("needs_repair")
    return {"dirty.tests": needs_repair}


@step(inputs=["coverage.report", "coverage.target"], outputs=["needs_coverage"])
def check_coverage_target(ws):
    """Step: Check if coverage needs improvement (pure logic)"""
    cov = ws.value("coverage.report") or {"total": 0.0}
    target = ws.value("coverage.target") if ws.exists("coverage.target") else 0.75
    needs_coverage = cov["total"] < target
    return {"needs_coverage": needs_coverage}

@llm(
    inputs=["coverage.report", "repo.ast", "tests.fs", "coverage.target", "needs_coverage"], 
    outputs=["tests.gen"],
    provider=OpenAIProvider(),
    model="gpt-4o-mini",
    temperature=0.0, 
    max_tokens=1500,
    template=open(os.path.join(os.path.dirname(__file__), "prompts", "lift_coverage.jinja"), "r").read(),
    schema=TESTS_GEN_SCHEMA,
    write_specs={"tests.gen": WriteSpec(merge_mode=MergeMode.MERGE_JSON)},
    map=lambda ws: {
        "cov_total": (ws.value("coverage.report") or {"total": 0.0})["total"],
        "cov_hot": {},
        "repo_ast": ws.value("repo.ast"),
        "test_paths": ws.value("tests.fs"),
        "target": ws.value("coverage.target") if ws.exists("coverage.target") else 0.75,
        "schema_name": "TESTS_GEN_SCHEMA"
    }
)
def lift_coverage_llm(ws): pass

@step(inputs=["needs_coverage"], outputs=["dirty.tests"])
def mark_coverage_dirty(ws):
    """Step: Mark tests as dirty if coverage improvements were made"""
    needs_coverage = ws.value("needs_coverage")
    return {"dirty.tests": needs_coverage}


@tool(inputs=["codebase.path"], outputs=["formatting.applied"])
def format_code(ws):
    root = ws.value("codebase.path")  # Using system python directly
    subprocess.run(["python", "-m", "ruff", "check", "--fix", "tests"], cwd=root)
    subprocess.run(["python", "-m", "black", "tests"], cwd=root)
    return {"formatting.applied": True}


def _read_current_test_files(ws):
    """Helper: Read current test file contents for incremental repair"""
    if not ws.exists("tests.fs"):
        return {}
    
    tests_fs = ws.value("tests.fs")
    root = ws.value("codebase.path")
    
    # Handle both old format (list) and new format (dict with paths)
    if isinstance(tests_fs, dict):
        test_paths = tests_fs.get("paths", [])
    else:
        test_paths = tests_fs if isinstance(tests_fs, list) else []
    
    current_tests = {}
    for test_path in test_paths:
        full_path = os.path.join(root, test_path)
        if os.path.exists(full_path):
            try:
                with open(full_path, 'r', encoding='utf-8') as f:
                    current_tests[test_path] = f.read()
            except Exception as e:
                print(f"   Warning: Could not read {test_path}: {e}")
    
    return current_tests


@llm(
    inputs=["codebase.path"],
    outputs=["target.file"],
    provider=OpenAIProvider(),
    model="gpt-4o-mini",
    temperature=0.0,
    max_tokens=1500,
    template="""You are an expert test strategist. Analyze the pytest coverage output and pick the SINGLE best file to test next.

COVERAGE ANALYSIS:
{{ coverage_output }}

STRATEGY:
1. Pick ONE file with 0% coverage that has the most statements (highest impact)
2. Avoid files that are too complex (>100 statements) - start with simpler ones  
3. Focus on core functionality files first
4. ALWAYS pick a different file from any that already have tests

Return JSON with the single file path:
{"file": "core/filename.py", "reasoning": "why this file"}""",
    schema={"type": "object", "properties": {"file": {"type": "string"}, "reasoning": {"type": "string"}}},
    map=lambda ws: {
        "coverage_output": _get_coverage_analysis(ws)
    }
)
def pick_next_file_llm(ws):
    """LLM: Pick next file based on coverage analysis"""
    print("🎯 Using LLM to pick next file based on coverage...")
    pass  # The @llm decorator handles the response automatically


@step(inputs=["run.result", "coverage.report", "coverage.target"], outputs=["target.file"])
def trigger_next_file_pick(ws):
    """Step: Trigger next file selection when current tests pass and coverage < target"""
    run_result = ws.value("run.result")
    coverage_report = ws.value("coverage.report")
    coverage_target = ws.value("coverage.target")
    
    if run_result.get("status") == "passed":
        current_coverage = coverage_report.get("coverage_percent", 0)
        
        if current_coverage < coverage_target:
            print(f"✅ Tests passed ({current_coverage}% < {coverage_target}%) - need next file")
            # Return a special value to trigger pick_next_file_llm
            return {"target.file": "NEED_NEXT_FILE"}
        else:
            print(f"🎯 Target coverage reached! ({current_coverage}% >= {target_coverage}%)")
            return {"target.file": "COMPLETE"}
    else:
        # Keep current target if tests failing
        current = ws.value("target.file") if ws.exists("target.file") else None
        return {"target.file": current}




@step(inputs=["run.result", "coverage.report", "coverage.target"], outputs=["next.cycle"])
def check_if_need_next_file(ws):
    """Step: Check if we need to pick next file after current tests pass"""
    run_result = ws.value("run.result")
    coverage_report = ws.value("coverage.report")
    coverage_target = ws.value("coverage.target")
    
    if run_result.get("status") == "passed":
        current_coverage = coverage_report.get("coverage_percent", 0)
        print(f"✅ Current file tests passed! Coverage: {current_coverage}%")
        
        if current_coverage >= coverage_target:
            print(f"🎯 Target coverage {coverage_target}% reached!")
            return {"next.cycle": {"needed": False, "reason": "target_reached"}}
        else:
            print(f"📈 Need more coverage ({current_coverage}% < {coverage_target}%) - pick next file")
            return {"next.cycle": {"needed": True, "reason": "need_more_coverage"}}
    else:
        print("❌ Tests still failing - continue with current file")
        return {"next.cycle": {"needed": False, "reason": "tests_failing"}}


def _get_coverage_analysis(ws):
    """Helper: Get detailed coverage analysis using pytest CLI"""
    root = ws.value("codebase.path")
    
    print("📊 Running coverage analysis...")
    
    # Run pytest with coverage to get detailed output
    cmd = ["python", "-m", "pytest", "tests/", "--cov=core", "--cov-report=term-missing", "--tb=no", "-q"]
    
    try:
        result = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=60)
        coverage_output = result.stdout + "\n" + result.stderr
        
        print(f"   Coverage analysis output:\n{coverage_output}")
        return coverage_output
        
    except subprocess.TimeoutExpired:
        return "Coverage analysis timed out"
    except Exception as e:
        return f"Coverage analysis failed: {e}"


