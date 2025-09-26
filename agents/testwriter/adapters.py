import json, re


def parse_pytest(stdout: str, stderr: str, rc: int):
    failed = []
    for line in stdout.splitlines():
        if line.startswith("FAILED ") and "::" in line:
            failed.append(line.split()[1])
    m = re.search(r"(\d+)\s+passed.*?(\d+)\s+failed", stdout)
    passed = int(m.group(1)) if m else 0
    failed_n = int(m.group(2)) if m else (0 if rc == 0 else max(1, len(failed)))
    return {
        "passed": rc == 0,
        "return_code": rc,
        "stdout": stdout,
        "stderr": stderr,
        "failed_tests": failed,
        "summary": {"passed": passed, "failed": failed_n}
    }


def parse_coverage(stdout: str):
    m = re.search(r"TOTAL\s+\d+\s+\d+\s+(\d+)%", stdout)
    total = (int(m.group(1))/100.0) if m else 0.0
    return {"total": total, "raw": stdout}


def parse_pyright(json_output: str):
    try:
        data = json.loads(json_output)
    except Exception:
        return {"issues": []}
    issues = []
    for d in data.get("generalDiagnostics", []):
        issues.append({
            "file": d.get("file", ""),
            "message": d.get("message", ""),
            "severity": d.get("severity", ""),
            "range": d.get("range", {})
        })
    return {"issues": issues}


