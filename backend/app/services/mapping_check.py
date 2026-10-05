"""Checks Atlas's worked examples against his own mapping table. No generated code is run.

Atlas (BA/DA) writes a document, not code. This check makes sure the document agrees with itself:
  output examples: every `copy` field equals the trimmed source value of that example's input, every `constant`
                   equals its value, every mandatory target field is present;
  reject examples:  the input really breaks a rule (unreadable, wrong root element, or a mandatory source element
                   missing/empty). Rejections by a value rule can't be judged from the table: they're marked for
                   Dev's unit tests and Quinn's test cases instead of being guessed at.
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET  # noqa: S405 — DTDs/entities are refused before parsing (see parse)
from typing import Any

MISSING = object()


class Unreadable(Exception):
    pass


def parse(text: str) -> Any:
    t = (text or "").strip()
    if not t:
        raise Unreadable("empty body")
    if t[0] in "{[":
        try:
            return json.loads(t)
        except ValueError as exc:
            raise Unreadable(f"invalid JSON ({exc})") from exc
    if t.startswith("<"):
        if re.search(r"<!\s*(DOCTYPE|ENTITY)", t, re.I):
            raise Unreadable("DTD / entity declarations are refused")
        try:
            return ET.fromstring(t)  # noqa: S314
        except ET.ParseError as exc:
            raise Unreadable(f"malformed XML ({exc})") from exc
    raise Unreadable("neither XML nor JSON")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def get(doc: Any, path: str) -> Any:
    """Value at a simple path: /Root/Child/@attr for XML, a.b[0].c (or $.a.b) for JSON. MISSING if absent."""
    path = (path or "").strip()
    if not path or path.upper() in ("N/A", "NA", "-"):
        return MISSING
    if isinstance(doc, ET.Element):
        absolute = path.startswith("/")
        parts = [re.sub(r"\[.*?\]$", "", p) for p in path.strip("/").split("/") if p]
        attr = parts.pop()[1:] if parts and parts[-1].startswith("@") else None
        node = doc
        if parts and _local(doc.tag) == parts[0]:
            parts = parts[1:]
        elif absolute:
            return MISSING  # wrong root element
        for p in parts:
            node = next((c for c in node if _local(c.tag) == p), None)
            if node is None:
                return MISSING
        if attr:
            return node.attrib.get(attr, MISSING)
        return node.text or ""
    cur = doc
    for p in [p for p in re.split(r"[./]", path.lstrip("$")) if p]:
        m = re.fullmatch(r"(.*?)\[(\d+)\]", p)
        key, idx = (m.group(1), int(m.group(2))) if m else (p, None)
        if key:
            if not isinstance(cur, dict) or key not in cur:
                return MISSING
            cur = cur[key]
        if idx is not None:
            if not isinstance(cur, list) or idx >= len(cur):
                return MISSING
            cur = cur[idx]
    return cur


def norm(v: Any) -> str:
    if v is MISSING or v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (dict, list)):
        return json.dumps(v, sort_keys=True)
    return str(v).strip()


def _root(rows: list[dict]) -> str | None:
    for r in rows:
        p = (r.get("source_path") or "").strip()
        if p.startswith("/") and not p.startswith("//"):
            return p.strip("/").split("/")[0]
    return None


def check(data: dict) -> list[dict]:
    rows, out = data["rows"], []
    root = _root(rows)
    for s in data["samples"]:
        notes: list[str] = []
        ok, verified, checked = True, True, 0
        try:
            inp = parse(s["input"])
        except Unreadable as exc:
            inp, unreadable = None, str(exc)
        else:
            unreadable = ""
        if s["expect"] == "output":
            if inp is None:
                ok = False
                notes.append(f"the input can't be read ({unreadable}) but the example expects an output")
            elif root and isinstance(inp, ET.Element) and _local(inp.tag) != root:
                ok = False
                notes.append(f"the input's root element is <{_local(inp.tag)}>, the mapping expects <{root}>")
            try:
                result = parse(s["expected"])
            except Unreadable as exc:
                ok = False
                notes.append(f"the expected output isn't valid ({exc})")
                result = None
            if inp is not None and result is not None:
                for r in rows:
                    tgt = get(result, r["target"])
                    if r["target_moc"] == "M" and tgt is MISSING:
                        ok = False
                        notes.append(f"mandatory field {r['target']} is missing from the expected output")
                        continue
                    kind = r.get("rule_kind", "derived")
                    if kind == "copy":
                        src = get(inp, r["source_path"])
                        if src is MISSING:
                            if r["source_moc"] == "M":
                                ok = False
                                notes.append(f"{r['source_path']} is mandatory but isn't in the input")
                            elif norm(tgt):
                                ok = False
                                notes.append(f"{r['target']} has a value but {r['source_path']} isn't in the input")
                            continue
                        checked += 1
                        if norm(tgt) != norm(src):
                            ok = False
                            notes.append(f"{r['target']} is {norm(tgt)!r}, but the input's {r['source_path']} is {norm(src)!r}")
                    elif kind == "constant" and tgt is not MISSING:
                        checked += 1
                        if norm(tgt) != norm(r["target_sample"]):
                            ok = False
                            notes.append(f"{r['target']} should be the constant {r['target_sample']!r}, not {norm(tgt)!r}")
            summary = f"{checked} field(s) checked against the mapping table" if ok else "; ".join(notes)
        else:
            missing = []
            if inp is not None and not (root and isinstance(inp, ET.Element) and _local(inp.tag) != root):
                missing = [r["source_path"] for r in rows if r["source_moc"] == "M" and not norm(get(inp, r["source_path"]))]
            if inp is None:
                summary = f"rejected: input is unreadable ({unreadable})"
            elif root and isinstance(inp, ET.Element) and _local(inp.tag) != root:
                summary = f"rejected: root element <{_local(inp.tag)}> instead of <{root}>"
            elif missing:
                summary = "rejected: mandatory " + ", ".join(missing) + " missing or empty"
            else:
                verified = False
                summary = "rejected by a value rule: not judgeable from the table, so Dev and Quinn must cover it in tests"
        out.append({"name": s["name"], "expect": s["expect"], "pass": ok, "verified": verified, "checked": checked,
                    "actual": summary})
    return out
