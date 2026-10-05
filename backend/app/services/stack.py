"""The project's tech stack, read from its files (no AI call; user, 10-03: "the language, runtime, packages used, test
tools… show them somewhere; better on TA's page"). Every item says which file it came from, so nothing is guessed."""
from __future__ import annotations

import ast
import re
import sys

from app.agents.buildkit import files_under
from app.services.storage import ProjectStore

LAMBDA_PROVIDED = {"boto3", "botocore", "awslambdaric", "s3transfer", "jmespath", "urllib3", "dateutil", "six"}  # in the Python runtime
TEST_TOOLS = {"pytest": "test runner", "moto": "fakes AWS in tests", "pytest_cov": "coverage (pytest-cov)", "coverage": "coverage",
              "hypothesis": "property-based tests", "freezegun": "fixed clocks in tests", "responses": "fakes HTTP in tests"}


def _reqs(text: str) -> list[tuple[str, str]]:
    out = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        m = re.match(r"([A-Za-z0-9_.\-\[\]]+)\s*([<>=!~]=?\s*[^;\s]+)?", line)
        if m and m.group(1):
            out.append((m.group(1), (m.group(2) or "").replace(" ", "")))
    return out


def _imports(code: str) -> set[str]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return set()
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.add(node.module.split(".")[0])
    return names


def stack(project_id: str) -> dict:
    from app.agents.ta import quality_gates
    from app.services import aws_access

    store = ProjectStore(project_id)
    infra = {p: c for p, c in files_under(store, ("infra/",)).items() if p.endswith(".tf")}
    code = files_under(store, ("src/", "layers/", "tests/"))
    tf = "\n".join(infra.values())

    runtimes = sorted(set(re.findall(r'runtime\s*=\s*"([a-z]+[0-9.]+)"', tf)))
    arch = "arm64 (Graviton)" if re.search(r'architectures\s*=\s*\[\s*"arm64"', tf) else ("x86_64" if runtimes else None)
    memory = sorted({int(m) for m in re.findall(r"memory_size\s*=\s*(\d+)", tf)})
    timeouts = sorted({int(m) for m in re.findall(r"\btimeout\s*=\s*(\d+)", tf)})
    lang = None
    if runtimes:
        m = re.match(r"([a-z]+)([0-9.]+)", runtimes[0])
        lang = {"name": m.group(1).title() if m else runtimes[0], "version": m.group(2) if m else "", "from": "infra/ (runtime)"}
    elif any(p.endswith(".py") for p in code):
        lang = {"name": "Python", "version": "", "from": "src/ (*.py)"}

    packages = []
    for p, c in sorted(code.items()):
        if p.endswith("requirements.txt") and not p.startswith("tests/"):
            where = f"layer {p.split('/')[1]}" if p.startswith("layers/") else f"function {p.split('/')[1]}"
            packages += [{"name": n, "version": v, "where": where, "from": p} for n, v in _reqs(c)]
    own = {p.split("/")[-1][:-3] for p in code if p.endswith(".py")} | {p.split("/")[3] for p in code
                                                                         if p.startswith("layers/") and p.count("/") >= 4 and "/python/" in p}
    stdlib = set(getattr(sys, "stdlib_module_names", ()))
    run_imports: set[str] = set()
    test_imports: set[str] = set()
    for p, c in code.items():
        if p.endswith(".py"):
            (test_imports if p.startswith("tests/") else run_imports).update(_imports(c))
    declared = {x["name"].lower().replace("-", "_").split("[")[0] for x in packages}
    third = sorted(n for n in run_imports - stdlib - own if n not in LAMBDA_PROVIDED)
    undeclared = [n for n in third if n.lower() not in declared]  # imported, but in no requirements.txt
    tests = [{"name": n.replace("_", "-"), "what": TEST_TOOLS[n], "from": "tests/"} for n in sorted(test_imports) if n in TEST_TOOLS]
    if any(p.startswith("tests/") and p.endswith(".py") for p in code):  # Orkestra's sandbox always runs them this way
        for name, what in (("pytest", "test runner (Orkestra's sandbox)"), ("pytest-cov", "coverage and its gate (Orkestra's sandbox)")):
            if not any(t["name"] == name for t in tests):
                tests.append({"name": name, "what": what, "from": "src/backend/app/tools/pytest_runner.py"})

    providers = [{"name": s, "version": v} for _, s, v in re.findall(r'(\w+)\s*=\s*{\s*source\s*=\s*"([^"]+)"\s*,\s*version\s*=\s*"([^"]+)"', tf)]
    tf_version = (re.search(r'required_version\s*=\s*"([^"]+)"', tf) or [None, None])[1]
    services = [aws_access.label(s) for s in aws_access.services(aws_access.resource_types(infra))[0]] if infra else []
    gates = quality_gates(project_id) or {}
    return {
        "language": lang,
        "runtime": {"lambda": runtimes, "arch": arch, "memory_mb": memory, "timeout_s": timeouts, "from": "infra/*.tf"} if runtimes else None,
        "packages": packages,
        "imports": {"third_party": third, "aws_sdk": sorted(run_imports & LAMBDA_PROVIDED), "own": sorted(run_imports & own),
                    "stdlib": sorted(run_imports & stdlib), "undeclared": undeclared},
        "tests": tests,
        "services": services,
        "iac": {"tool": "Terraform", "version": tf_version, "providers": providers, "from": "infra/versions.tf"} if infra else None,
        "gates": {"coverage": gates.get("min_coverage_percent"), "rules": gates.get("rules") or []},
        "ready": {"infra": bool(infra), "code": any(p.startswith("src/") for p in code)},
    }
