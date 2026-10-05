"""Shared pieces for the agents that write the codebase (Terra: infra/, Dev: src/ layers/ tests/, Quinn: qa/)."""
from __future__ import annotations

import json
import re

from app.agents.base import AgentError, obj
from app.db.base import SessionLocal
from app.db.models import Project
from app.services.storage import ProjectStore, StorageError

FILES = {"type": "array", "description": "The complete set of files you own (path relative to the project root)",
         "items": obj({"path": {"type": "string"}, "content": {"type": "string"}})}
TEXT_FILE = re.compile(r"\.(tf|tfvars|hcl|py|txt|md|json|ini|cfg|toml|yaml|yml|xml|sh)$")


def read(store: ProjectStore, path: str, default: str = "") -> str:
    try:
        return store.read(path).decode("utf-8", errors="replace")
    except StorageError:
        return default


def files_under(store: ProjectStore, prefixes: tuple[str, ...]) -> dict[str, str]:
    return {f["path"]: read(store, f["path"]) for f in store.tree() if f["path"].startswith(prefixes) and TEXT_FILE.search(f["path"])}


def checked_files(items: list[dict], prefixes: tuple[str, ...], owner: str, max_files: int = 80) -> dict[str, str]:
    """The agent's files, refusing anything outside the folders it owns."""
    out: dict[str, str] = {}
    for f in items:
        p = f["path"].strip().lstrip("./")
        if ".." in p.split("/") or p.startswith("/") or not p.startswith(prefixes):
            raise AgentError(f"{p}: {owner} may only write under {', '.join(prefixes)}. Other folders belong to other agents "
                             "(infra/ → Terra; src/, layers/, tests/ → Dev; qa/ → Quinn). Reference their paths instead, and "
                             "say in `notes` what they must provide.")
        if not TEXT_FILE.search(p):
            raise AgentError(f"{p}: only text source files are allowed")
        if len(f["content"]) > 400_000:
            raise AgentError(f"{p} is too large")
        out[p] = f["content"]
    if not out:
        raise AgentError("no files submitted")
    if len(out) > max_files:
        raise AgentError(f"too many files ({len(out)} > {max_files})")
    return out


# Incremental file passing for the test loops: the agent sends only what it adds or changes; the platform keeps the
# rest. Resending every file on every run made the output tokens (the expensive part) grow with each iteration.
CHANGED_FILES = {"type": "array", "description": "Only the files you add or change in this step (path relative to the project "
                 "root, full new content). Files you sent earlier, or that already existed, are kept as they are.",
                 "items": FILES["items"]}
DELETE = {"type": "array", "items": {"type": "string"}, "description": "Paths to remove from your files (usually empty)"}


class WorkingSet:
    def __init__(self, start: dict[str, str], prefixes: tuple[str, ...], owner: str) -> None:
        self.files, self.prefixes, self.owner = dict(start), prefixes, owner

    def apply(self, a: dict) -> dict[str, str]:
        """Merge a tool call's `files` (changes) and `delete` into the set; returns the complete set."""
        files = dict(self.files)
        for p in a.get("delete") or []:
            files.pop(p.strip().lstrip("./"), None)
        if a.get("files"):
            files.update(checked_files(a["files"], self.prefixes, self.owner, max_files=200))
        if not files:
            raise AgentError("You have no files yet: send them in `files`.")
        if len(files) > 80:
            raise AgentError(f"too many files ({len(files)} > 80)")
        self.files = files
        return dict(files)

    def listing(self) -> str:
        return "\n".join(f"- {p} ({len(c)} chars)" for p, c in sorted(self.files.items()))


def examples_fixture(project_id: str) -> str:
    """Atlas's worked examples, as the JSON fixture Dev's and Quinn's tests parametrize over."""
    from app.agents.ba import load_mapping

    m = load_mapping(project_id) or {}
    return json.dumps([{k: s[k] for k in ("name", "description", "input", "expect", "expected")} for s in m.get("samples", [])],
                      indent=2, ensure_ascii=False)


TALK_ORDER = ("ba", "ta", "tp", "de", "qa")
TALK_SPLIT = "\n## The conversation"  # talks/<agent>.md: what was agreed, then the full conversation (agents/talk.py)


def talk_notes(store: ProjectStore, own: str | None = None) -> str:
    """What each specialist told the crew in their kickoff conversation (talks/<agent>.md, agents/talk.py): the agreed
    points of every finished talk, and the whole conversation for the agent's own (`own`)."""
    have = {f["path"] for f in store.tree() if f["path"].startswith("talks/") and f["path"].endswith(".md")}
    parts = []
    for agent in TALK_ORDER:
        text = read(store, f"talks/{agent}.md") if f"talks/{agent}.md" in have else ""
        if text:
            parts.append(text[:24000] if agent == own else text.split(TALK_SPLIT)[0][:6000])
    if not parts:
        return ""
    return ("# Kickoff conversations: what each specialist on the user's team told the crew. These are their decisions: "
            "follow them, and where they said \"you decide\", use the recommendation recorded there.\n\n" + "\n\n".join(parts))


def context(store: ProjectStore, requirement_md: str, *, lld: bool = True, mapping: bool = True, infra: bool = False,
            code: bool = False, limit: int = 60000, own: str | None = None) -> str:
    """The documents an agent builds from, newest version. `own`: the agent whose kickoff conversation is shown in full."""
    from app.services.naming import convention_block

    parts = [f"# Signed-off requirement\n{requirement_md}"]
    talks = talk_notes(store, own)
    if talks:
        parts.append(talks)
    naming = convention_block(store.root.name)
    if naming:
        parts.append(naming)
    if mapping:
        parts.append(f"# Approved data mapping (Atlas)\n{read(store, '01_data_mapping.md', '(none)')[:limit]}")
    if lld:
        parts.append(f"# Approved LLD (Archie)\n{read(store, '03_lld.md', '(none)')[:limit]}")
        design = read(store, "diagrams/design.json")
        if design:
            try:
                res = json.loads(design).get("resources", [])
                parts.append("# Resources in the design\n" + "\n".join(f"- {r['name']} ({r['type']}): {r['purpose']}. {r['key_settings']}" for r in res))
            except ValueError:
                pass
    if infra:
        tf = files_under(store, ("infra/",))
        parts.append("# Terra's infrastructure (infra/)\n" + "\n\n".join(f"## {p}\n```hcl\n{c[:12000]}\n```" for p, c in tf.items()))
    if code:
        src = files_under(store, ("src/", "layers/"))
        parts.append("# Dev's code\n" + "\n\n".join(f"## {p}\n```\n{c[:15000]}\n```" for p, c in src.items()))
    uploads = [f["path"] for f in store.tree() if f["path"].startswith("inputs/")]
    if uploads:
        parts.append("# Files the user supplied (inputs/)\n" + "\n\n".join(
            f"## {p}\n```\n{read(store, p)[:15000]}\n```" for p in uploads if TEXT_FILE.search(p)))
    return "\n\n".join(parts)


async def new_minor_version(project_id: str, reason: str) -> str:
    """Fixes go into a new minor version (v1.1 → v1.2); the previous one stays untouched."""
    store = ProjectStore(project_id)
    v = store.new_version(reason, minor=True)
    async with SessionLocal() as db:
        p = await db.get(Project, project_id)
        p.current_version = v
        await db.commit()
    return v
