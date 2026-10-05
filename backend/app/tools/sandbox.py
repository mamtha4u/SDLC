"""Run agent-written Python in a throwaway Docker container: no network, read-only, non-root, capped CPU/memory.

Used by Dev (unit tests) and Quinn (component tests) via tools/pytest_runner.py. The image `orkestra-sandbox:py314`
(src/infra/host/sandbox.Dockerfile) has lxml, pytest, boto3 and moto preinstalled, with dummy AWS credentials.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import tempfile
import time
from functools import lru_cache
from pathlib import Path

from app.core.config import get_settings

IMAGE = os.environ.get("ORKESTRA_SANDBOX_IMAGE", "orkestra-sandbox:py314")
MARK = "@@ORKESTRA_RESULT@@"

RUNNER = f'''
import importlib.util, json, sys, traceback
out = []
try:
    spec = importlib.util.spec_from_file_location("mapping", "/work/transform.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
except Exception as e:
    print("{MARK}" + json.dumps({{"load_error": f"{{type(e).__name__}}: {{e}}", "trace": traceback.format_exc()[-1500:]}}))
    sys.exit(0)
for case in json.load(open("/work/cases.json", encoding="utf-8")):
    try:
        r = mod.transform(case["input"])
        if isinstance(r, (dict, list)):
            r = json.dumps(r, ensure_ascii=False)
        out.append({{"name": case["name"], "ok": True, "output": r}})
    except Exception as e:
        out.append({{"name": case["name"], "ok": False, "error": f"{{type(e).__name__}}: {{e}}"}})
print("{MARK}" + json.dumps({{"results": out}}, ensure_ascii=False))
'''


class SandboxError(Exception):
    pass


@lru_cache
def available() -> bool:
    """Docker CLI present AND the daemon answering (Docker Desktop installed-but-stopped counts as unavailable)."""
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


async def run(files: dict[str, str], cmd: list[str], timeout: int = 40, memory: str = "512m", pids: int = 128,
              max_out: int = 20000) -> dict:
    """`max_out`: how much of stdout to keep (the end of it)."""
    if not available():
        raise SandboxError("The code sandbox (Docker) isn't installed on this host")
    root = get_settings().data_dir / "sandbox-runs"
    root.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(dir=root))
    try:
        work.chmod(0o755)
        for name, content in files.items():
            p = work / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            p.chmod(0o644)
        args = ["docker", "run", "--rm", "--network", "none", "--read-only", "--tmpfs", "/tmp:size=64m",
                "--memory", memory, "--cpus", "1", "--pids-limit", str(pids), "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges", "-e", "PYTHONDONTWRITEBYTECODE=1",
                "-v", f"{work}:/work:ro", IMAGE, "timeout", str(timeout - 5), *cmd]
        t0 = time.perf_counter()
        proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise SandboxError(f"Timed out after {timeout}s")
        return {"exit_code": proc.returncode, "stdout": out.decode(errors="replace")[-max_out:],
                "stderr": err.decode(errors="replace")[-6000:], "ms": int((time.perf_counter() - t0) * 1000)}
    finally:
        shutil.rmtree(work, ignore_errors=True)


async def run_transform(code: str, cases: list[dict]) -> dict:
    """cases: [{name, input}] → {results: [{name, ok, output|error}]} or {load_error}."""
    res = await run({"transform.py": code, "runner.py": RUNNER, "cases.json": json.dumps(cases, ensure_ascii=False)},
                    ["python", "/work/runner.py"])
    line = next((ln for ln in res["stdout"].splitlines() if ln.startswith(MARK)), None)
    if line is None:
        raise SandboxError(f"Runner produced no result (exit {res['exit_code']}): {res['stderr'][-800:]}")
    return json.loads(line[len(MARK):])
