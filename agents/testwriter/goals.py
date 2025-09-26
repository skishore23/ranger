from core.sdk import goal


@goal(scope={"run.result", "coverage.report"})
def tests_passing_and_covered(ws):
    rr = ws.value("run.result") if ws.exists("run.result") else {}
    cov = ws.value("coverage.report") if ws.exists("coverage.report") else {"total": 0.0}
    target = ws.value("coverage.target") if ws.exists("coverage.target") else 0.75
    return bool(rr.get("passed")) and float(cov.get("total", 0.0)) >= float(target)


