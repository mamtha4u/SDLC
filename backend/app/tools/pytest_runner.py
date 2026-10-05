"""Run pytest on the project's code in the no-network sandbox and report every test's outcome (from JUnit XML).

Used by Dev (unit tests in tests/) and Quinn (component tests in qa/). AWS calls inside the code are tested against
moto's in-memory AWS (preinstalled in the sandbox image with dummy credentials), never the real account.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET  # noqa: S405 — parses pytest's own JUnit output

from app.tools import sandbox

MARK = "@@JUNIT@@"
COV = "@@COVERAGE@@"


def _pythonpath(files: dict[str, str]) -> str:
    layers = sorted({p.split("/")[1] for p in files if p.startswith("layers/") and "/python/" in p})
    return ":".join(["/work/src", *[f"/work/layers/{name}/python" for name in layers], "/work"])


async def run(files: dict[str, str], targets: list[str], timeout: int = 150, coverage: bool = False) -> dict:
    """files: path → content (src/, layers/, tests/, qa/ ...); targets: test dirs/files. Returns per-test results, and
    with coverage=True the line coverage of src/ and layers/ (pytest-cov) including each file's untested lines."""
    cov = ("--cov=/work/src " + " ".join(f"--cov=/work/layers/{n}/python" for n in sorted({p.split('/')[1] for p in files if p.startswith('layers/')}))
           + " --cov-report=json:/tmp/cov.json --cov-report= ") if coverage else ""
    # Only what we read leaves the sandbox: the full coverage JSON (per function and class) was large enough to push
    # the test results out of the kept output, and every run reported "0 tests" although they ran (10-02).
    compact = ("python -c \"import json;d=json.load(open('/tmp/cov.json'));print(json.dumps({'totals':{'percent_covered':"
               "d['totals']['percent_covered']},'files':{k:{'summary':{'percent_covered':v['summary']['percent_covered']},"
               "'missing_lines':v.get('missing_lines',[])[:3000],'executed_lines':v.get('executed_lines',[])[:6000]} "
               "for k,v in d.get('files',{}).items()}}))\" 2>/dev/null")
    cmd = (f"cd /work && COVERAGE_FILE=/tmp/.coverage PYTHONPATH={_pythonpath(files)} python -m pytest -q -p no:cacheprovider "
           f"-o cache_dir=/tmp/.pc {cov}--junitxml=/tmp/junit.xml {' '.join(targets)} 2>&1 | tail -c 12000; "
           f"echo '{MARK}'; cat /tmp/junit.xml 2>/dev/null; echo '{COV}'; {compact if coverage else 'true'}")
    res = await sandbox.run(files, ["sh", "-c", cmd], timeout=timeout, max_out=2_000_000)
    out, _, rest = res["stdout"].partition(MARK)
    junit, _, cov_json = rest.partition(COV)
    tests: list[dict] = []
    try:
        root = ET.fromstring(junit.strip())  # noqa: S314
        for tc in root.iter("testcase"):
            outcome, message = "passed", ""
            for tag in ("failure", "error", "skipped"):
                el = tc.find(tag)
                if el is not None:
                    outcome = {"failure": "failed", "error": "error", "skipped": "skipped"}[tag]
                    message = (el.get("message") or "") + "\n" + (el.text or "")[-1500:]
                    break
            tests.append({"id": f"{tc.get('classname', '')}::{tc.get('name', '')}", "name": tc.get("name", ""),
                          "outcome": outcome, "message": message.strip()[:1800], "time": float(tc.get("time") or 0)})
    except ET.ParseError:
        pass
    counts = {k: sum(t["outcome"] == k for t in tests) for k in ("passed", "failed", "error", "skipped")}
    return {"total": len(tests), **counts, "ok": bool(tests) and counts["failed"] == 0 and counts["error"] == 0,
            "tests": tests, "output": out[-6000:], "ms": res["ms"], "coverage": _coverage(cov_json) if coverage else None}


def _coverage(raw: str) -> dict | None:
    import json

    try:
        data = json.loads(raw.strip())
    except ValueError:
        return None
    files = {}
    for path, f in data.get("files", {}).items():
        rel = path.removeprefix("/work/")
        files[rel] = {"percent": round(f["summary"]["percent_covered"], 1), "missing": f.get("missing_lines", []),
                      "executed": f.get("executed_lines", [])}
    return {"percent": round(data.get("totals", {}).get("percent_covered", 0.0), 1), "files": files}


def coverage_html(title: str, files: dict[str, str], r: dict, gate: float | None = None) -> str:
    """A standalone, downloadable coverage + test report (one HTML file, no scripts): the totals, every file with its
    covered and untested lines highlighted in the source, and every test's result."""
    from html import escape

    cov = r.get("coverage") or {"percent": 0, "files": {}}
    rows, sources = [], []
    for path, f in sorted(cov["files"].items()):
        tone = "#16a34a" if f["percent"] >= (gate or 80) else "#d97706" if f["percent"] >= 60 else "#dc2626"
        anchor = escape(path.replace("/", "_"))
        rows.append(f'<tr><td><a href="#{anchor}">{escape(path)}</a></td><td class="num">{f["percent"]}%</td>'
                    f'<td><div class="bar"><span style="width:{f["percent"]}%;background:{tone}"></span></div></td>'
                    f'<td class="num">{len(f.get("missing", []))}</td></tr>')
        missing, executed = set(f.get("missing", [])), set(f.get("executed", []))
        code = files.get(path, "")
        lines = "".join(f'<tr class="{"miss" if n in missing else "hit" if n in executed else ""}"><td class="ln">{n}</td>'
                        f'<td><pre>{escape(line) or " "}</pre></td></tr>' for n, line in enumerate(code.splitlines(), 1))
        sources.append(f'<section id="{anchor}"><h3>{escape(path)} <small>{f["percent"]}% · untested lines: '
                       f'{escape(", ".join(map(str, f.get("missing", []))) or "none")}</small></h3><table class="src">{lines}</table></section>')
    tests = "".join(f'<tr><td class="{t["outcome"]}">{ {"passed": "✓", "failed": "✗", "error": "✗", "skipped": "–"}[t["outcome"]] } {t["outcome"]}</td>'
                    f'<td><code>{escape(t["id"])}</code>{"<pre class=msg>" + escape(t["message"][:1500]) + "</pre>" if t.get("message") else ""}</td></tr>'
                    for t in r.get("tests", []))
    gate_line = f" · gate {gate:.0f}% {'✓ met' if cov['percent'] >= gate else '✗ not met'}" if gate is not None else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title><style>
body{{font:14px/1.5 system-ui,-apple-system,Segoe UI,sans-serif;color:#0f172a;background:#f8fafc;margin:0;padding:24px 32px}}
h1{{margin:0 0 4px;font-size:24px}} h2{{margin:32px 0 10px;font-size:18px}} h3{{margin:24px 0 6px;font-size:15px}} small{{color:#64748b;font-weight:400}}
.kpis{{display:flex;gap:12px;flex-wrap:wrap;margin:16px 0}} .kpi{{background:#fff;border:1px solid #e2e8f0;border-radius:14px;padding:10px 16px;min-width:140px}}
.kpi b{{display:block;font-size:22px}} table{{border-collapse:collapse;width:100%;background:#fff}} td,th{{padding:6px 10px;border-bottom:1px solid #eef2f7;text-align:left;vertical-align:top}}
.num{{text-align:right;font-variant-numeric:tabular-nums}} .bar{{height:8px;background:#e2e8f0;border-radius:9px;overflow:hidden;min-width:160px}} .bar span{{display:block;height:100%}}
.src{{font:12.5px/1.45 ui-monospace,Consolas,monospace;border:1px solid #e2e8f0}} .src td{{border:0;padding:0 8px}} .src pre{{margin:0;white-space:pre}}
.ln{{color:#94a3b8;text-align:right;user-select:none;width:1%}} tr.hit{{background:#ecfdf5}} tr.miss{{background:#fee2e2}} tr.miss .ln{{color:#dc2626;font-weight:700}}
.passed{{color:#16a34a;white-space:nowrap}} .failed,.error{{color:#dc2626;white-space:nowrap}} .skipped{{color:#64748b}} .msg{{white-space:pre-wrap;color:#475569;margin:4px 0 0}}
a{{color:#4f46e5}} .legend span{{display:inline-block;padding:1px 8px;border-radius:6px;margin-right:6px}}
</style></head><body>
<h1>{escape(title)}</h1><p><small>Line coverage of src/ and layers/, measured with pytest-cov in Orkestra's no-network sandbox (Python 3.14, moto for AWS).</small></p>
<div class="kpis"><div class="kpi">Coverage<b>{cov['percent']}%</b><small>{escape(gate_line.strip(' ·'))}</small></div>
<div class="kpi">Tests<b>{r.get('passed', 0)}/{r.get('total', 0)}</b><small>passed</small></div><div class="kpi">Failing<b>{r.get('failed', 0) + r.get('error', 0)}</b></div>
<div class="kpi">Files<b>{len(cov['files'])}</b></div></div>
<h2>Files</h2><table><tr><th>File</th><th class="num">Covered</th><th></th><th class="num">Untested lines</th></tr>{''.join(rows)}</table>
<h2>Tests ({r.get('total', 0)})</h2><table>{tests}</table>
<h2>Source</h2><p class="legend"><span style="background:#ecfdf5">run by the tests</span><span style="background:#fee2e2">never run</span><span>not code (comments, blank lines)</span></p>
{''.join(sources)}
</body></html>
"""


def report_md(title: str, r: dict) -> str:
    lines = [f"# {title}", "", f"**{r['passed']}/{r['total']} passed**, {r['failed']} failed, {r['error']} errors, "
             f"{r['skipped']} skipped · {r['ms'] / 1000:.1f}s in the no-network sandbox (Python 3.14, moto for AWS)", "",
             "| Test | Result | Detail |", "|---|---|---|"]
    for t in r["tests"]:
        mark = {"passed": "✅", "failed": "❌", "error": "💥", "skipped": "⏭"}[t["outcome"]]
        detail = t["message"].splitlines()[0][:160].replace("|", "\\|") if t["message"] else ""
        lines.append(f"| `{t['id']}` | {mark} {t['outcome']} | {detail} |")
    if r.get("coverage"):
        c = r["coverage"]
        lines += ["", f"## Line coverage: **{c['percent']}%**", "", "| File | Covered | Untested lines |", "|---|---|---|"]
        lines += [f"| `{p}` | {f['percent']}% | {', '.join(map(str, f['missing'][:30])) or '–'} |" for p, f in sorted(c["files"].items())]
    return "\n".join(lines) + "\n\n```\n" + r["output"][-3000:] + "\n```\n"
