"""Versioned project storage on the local filesystem (the spec's "versioning without GitHub").

projects/<id>/
  manifest.json   current version, released versions, agent run history
  CHANGELOG.md    one entry per version
  v1/ v2/ ...     every agent output; a released version is never modified
  logs/           audit logs (LLM calls, tool calls, AWS calls)
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from app.core.config import get_settings

VERSION_LAYOUT = ["diagrams", "infra", "src", "tests", "reports", "bugs"]


class StorageError(Exception):
    pass


class ProjectStore:
    def __init__(self, project_id: str) -> None:
        self.root = get_settings().projects_dir / project_id

    # ---- manifest -------------------------------------------------------------
    @property
    def manifest_path(self) -> Path:
        return self.root / "manifest.json"

    def manifest(self) -> dict:
        return json.loads(self.manifest_path.read_text(encoding="utf-8"))

    def _save_manifest(self, m: dict) -> None:
        tmp = self.manifest_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(m, indent=2), encoding="utf-8")
        tmp.replace(self.manifest_path)  # atomic on POSIX

    # ---- lifecycle ------------------------------------------------------------
    def create(self, name: str) -> None:
        if self.root.exists():
            raise StorageError("project folder already exists")
        (self.root / "logs").mkdir(parents=True)
        self._make_version_dir("v1")
        now = datetime.now(timezone.utc).isoformat()
        self._save_manifest({"name": name, "current_version": "v1", "released": [], "created_at": now, "runs": []})
        (self.root / "CHANGELOG.md").write_text(f"# Changelog — {name}\n\n## v1 — {now[:10]}\n- Project created\n",
                                                 encoding="utf-8")

    def delete(self) -> None:
        if self.root.exists():
            shutil.rmtree(self.root)

    def _make_version_dir(self, version: str) -> Path:
        vdir = self.root / version
        for sub in VERSION_LAYOUT:
            (vdir / sub).mkdir(parents=True, exist_ok=True)
        return vdir

    def next_version(self, minor: bool = False) -> str:
        cur = self.manifest()["current_version"]
        major, _, minor_n = cur[1:].partition(".")
        return f"v{major}.{int(minor_n or 0) + 1}" if minor else f"v{int(major) + 1}"

    def new_version(self, reason: str, minor: bool = False) -> str:
        """Release the current version and start the next one as a copy (v1 → v1.1 for fixes, v1 → v2 for CRs)."""
        m = self.manifest()
        cur = m["current_version"]
        nxt = self.next_version(minor)
        shutil.copytree(self.root / cur, self.root / nxt)
        m["released"].append(cur)
        m["current_version"] = nxt
        self._save_manifest(m)
        self.append_changelog(nxt, [reason])
        return nxt

    def append_changelog(self, version: str, lines: list[str]) -> None:
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with (self.root / "CHANGELOG.md").open("a", encoding="utf-8") as fh:
            fh.write(f"\n## {version} — {stamp}\n" + "".join(f"- {ln}\n" for ln in lines))

    # ---- audit logs (append-only JSONL, outside the versioned folders) --------
    def append_log(self, name: str, record: dict) -> None:
        logs = self.root / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        record = {"ts": datetime.now(timezone.utc).isoformat(), **record}
        with (logs / f"{name}.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str, ensure_ascii=False) + "\n")

    # ---- files ----------------------------------------------------------------
    def _safe(self, version: str, rel: str) -> Path:
        pure = PurePosixPath(rel)
        if pure.is_absolute() or ".." in pure.parts:
            raise StorageError("invalid path")
        return self.root / version / pure

    def write(self, rel: str, content: str | bytes, version: str | None = None) -> Path:
        m = self.manifest()
        version = version or m["current_version"]
        if version in m["released"]:
            raise StorageError(f"{version} is released and read-only — create a new version first")
        path = self._safe(version, rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
        return path

    def remove(self, rel: str) -> None:
        """Delete one file from the current (unreleased) version, e.g. a file an agent dropped from its new set."""
        m = self.manifest()
        if m["current_version"] in m["released"]:
            raise StorageError(f"{m['current_version']} is released and read-only")
        path = self._safe(m["current_version"], rel)
        if path.is_file():
            path.unlink()

    def replace_files(self, prefixes: tuple[str, ...], files: dict[str, str]) -> list[str]:
        """An agent's complete new file set under its own prefixes: write them, drop its files that are gone."""
        stale = [f["path"] for f in self.tree() if f["path"].startswith(prefixes) and f["path"] not in files]
        for p in stale:
            self.remove(p)
        for p, c in files.items():
            self.write(p, c)
        return stale

    def read(self, rel: str, version: str | None = None) -> bytes:
        path = self._safe(version or self.manifest()["current_version"], rel)
        if not path.is_file():
            raise StorageError("file not found")
        return path.read_bytes()

    def versions(self) -> list[str]:
        def key(v: str):
            major, _, minor = v[1:].partition(".")
            return int(major), int(minor or 0)

        return sorted((p.name for p in self.root.iterdir() if p.is_dir() and p.name.startswith("v")), key=key)

    def tree(self, version: str | None = None) -> list[dict]:
        base = self.root / (version or self.manifest()["current_version"])
        out = []
        for p in sorted(base.rglob("*")):
            if p.is_file():
                out.append({"path": p.relative_to(base).as_posix(), "size": p.stat().st_size,
                            "modified": datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat()})
        return out
