"""Echo — Requirements Analyst (Intake agent).

Jobs:
  intake.chat      one interview turn: Echo captures answers and asks the next question (streamed → typewriter)
  intake.review    a review round: playback, gaps, suggestions, services (mode "reflect" = "what did we miss?")
  intake.finalize  writes and freezes 00_requirement.md after sign-off (streamed live), then hands to Orion

Speed & cost: the stable prompt (role + organisation context + topic map) and the conversation history are
cache-friendly prefixes; only the latest turn + current state is new each time. Chat turns run at a lower effort
than reviews and the final document (configurable in agents.yaml).
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

import anthropic

from app.agents import llm
from app.agents.base import AgentError, Tool, obj, run_loop
from app.db.base import SessionLocal, utcnow
from app.db.models import Intake, Project, User
from app.orchestrator.bus import bus
from app.orchestrator.runner import JobContext, handler
from app.services.requirement_template import answers_for_prompt, questions
from app.services.storage import ProjectStore

CATEGORIES = ["cost", "resilience", "security", "performance", "operability", "simplicity", "data-quality", "compliance"]


# ── state helpers ─────────────────────────────────────────────────────────────
async def load_intake(project_id: str) -> Intake:
    async with SessionLocal() as db:
        row = await db.get(Intake, project_id)
        if row is None:
            row = Intake(project_id=project_id)
            db.add(row)
            await db.commit()
            await db.refresh(row)
        return row


async def update_intake(project_id: str, **fields) -> None:
    async with SessionLocal() as db:
        row = await db.get(Intake, project_id)
        for k, v in fields.items():
            setattr(row, k, v)
        await db.commit()


def system_blocks() -> list[dict]:
    """Everything stable for the whole project, cached across calls: role, organisation context, topic map."""
    ids = "\n".join(f"- {q['id']}: {q['label']}" for q in questions().values())
    text = (llm.prompt("intake.md") + "\n\n" + llm.prompt("org_context.md")
            + f"\n\n# Topic map (IDs for `answers`)\n{ids}")
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


def context_dump(intake: Intake, include_chat: bool = True) -> str:
    parts = ["# What we know so far (topic map answers)", answers_for_prompt(intake.answers or {})]
    for u in intake.uploads or []:
        parts.append(f"# Uploaded document: {u['name']}\n{u['text']}")
    if include_chat and intake.chat:
        parts.append("# Chat so far\n" + "\n".join(f"{m['role'].upper()}: {m['text']}" for m in intake.chat[-60:]))
    for r in intake.rounds or []:
        parts.append(f"# Your review round {r['round']} ({r['mode']})\n" + json.dumps(
            {k: r[k] for k in ("headline", "gaps", "suggestions", "assumptions", "conflicts")}, ensure_ascii=False))
    if intake.decisions:
        parts.append("# User decisions on your suggestions\n" + json.dumps(intake.decisions))
    if intake.gap_answers:
        parts.append("# User answers to your questions (gap id → answer)\n" + json.dumps(intake.gap_answers, ensure_ascii=False))
    return "\n\n".join(parts)


async def emit_updated(ctx: JobContext, message: str = "") -> None:
    await ctx.emit("intake.updated", message, agent="intake")


class Draft:
    """Live text shown while Echo works (typewriter reply, review progress, the document being written).
    Throttled writes to the DB (polled by the UI) + an SSE delta for browsers where streaming works."""

    def __init__(self, project_id: str, every: float = 0.35) -> None:
        self.pid, self.every, self.last, self.text = project_id, every, 0.0, ""

    async def set(self, text: str, force: bool = False) -> None:
        if text == self.text:
            return
        self.text = text
        if force or time.monotonic() - self.last >= self.every:
            self.last = time.monotonic()
            await update_intake(self.pid, draft=text)
            await bus.publish("intake.delta", project_id=self.pid, agent="intake", data={"text": text}, persist=False)


def friendly(exc: Exception) -> str:
    if isinstance(exc, llm.BudgetExceeded | AgentError):
        return str(exc)
    if isinstance(exc, anthropic.APIError):
        return ("Claude on AWS had a temporary problem and didn't finish, even after retrying. "
                "Nothing was lost. Press Try again.")
    return f"Something went wrong: {str(exc)[:160]}"


async def _start(ctx: JobContext, activity: str) -> None:
    await update_intake(ctx.project_id, draft="", last_error=None)
    await ctx.set_agent("intake", "working", activity)


async def _fail(ctx: JobContext, exc: Exception, kind: str, payload: dict) -> None:
    msg = friendly(exc)
    await update_intake(ctx.project_id, busy=None, draft=None,
                        last_error={"kind": kind, "payload": payload, "message": msg, "at": utcnow().isoformat()})
    blocked = isinstance(exc, llm.BudgetExceeded)
    await ctx.set_agent("intake", "blocked" if blocked else "failed", msg[:240])
    await ctx.set_project(status="waiting", last_activity=f"Echo: {msg[:200]}")
    await emit_updated(ctx, f"Echo hit a problem: {msg[:200]}")
    from app.orchestrator import crewchat

    await crewchat.say(ctx.project_id, "intake", "cto", f"I hit a problem and stopped: {msg[:300]}", "issue")


# ── chat (interview) ──────────────────────────────────────────────────────────
CAPTURE = Tool(
    name="capture",
    terminal=True,
    description=("AFTER writing your reply to the user as normal text, call this exactly once to file the facts you "
                 "captured and the tap-to-answer options for your question."),
    schema=obj({
        "quick_replies": {"type": "array", "items": {"type": "string"},
                          "description": "2-5 tappable answers for the whole group of questions (under 6 words each), e.g. "
                                         "\"Use all your suggestions\""},
        "answers": {"type": "array", "description": "Topic-map answers learned from the user's input or files", "items": obj({
            "question_id": {"type": "string", "description": "Exact topic-map ID, e.g. flow.source"},
            "value": {"type": "string", "description": "The answer, in the user's words where possible"},
        })},
        "ready_to_review": {"type": "boolean", "description": (
            "True only when nothing important is open AND the user has confirmed it's all good / complete / ready to hand "
            "over: the platform then starts your review round right away (the user signs off after it)")},
    }),
)
CHAT_FORMAT = ("Write your reply to the user FIRST, as normal markdown text: one short line on what you captured (2-3 lines "
               "after a document), then the open questions of the next topic(s) as ONE numbered group (at most 6), each "
               "with your recommended answer and a one-line reason; re-ask anything still open. Then call `capture`.")

AMEND_MODE = """# AMENDMENT MODE: the requirement was signed off; the user raised a change request
You are updating the signed-off requirement for ONE change only. Rules:
- Talk only about this change and what it affects. Don't re-interview settled topics.
- Read any attached files carefully, say what you found in them, and point out gaps or problems (e.g. empty functions,
  missing pieces, things that would break other agreed rules such as "no personal data in logs").
- If something the change needs is missing (a file, a decision), ask for it. The user can attach files with the 📎 button.
- Ask everything still open about the change together (numbered), recommendation first, with tappable answers for the group.
- When the change is fully clear, give a short summary of exactly what will change in the requirement, and tell the user
  to press **Sign off amendment**.
"""


def amend_block(intake: Intake, cr, brief: str) -> dict | None:
    """Everything stable for one amendment (rules, the CR, Orion's brief, its files, the baseline document) as a
    cached system block, so each chat turn pays for it once instead of every turn."""
    if intake.status != "amending" or cr is None:
        return None
    files = "\n\n".join(f"### Attached file: {u['name']}\n{u['text'][:20000]}" for u in (intake.uploads or [])
                        if u["name"] in (cr.attachments or []))
    text = (AMEND_MODE + f"\n## The change request ({cr_label(cr)})\n{cr.text}\n"
            + (f"\n## Orion's brief for you\n{brief}\n" if brief else "") + (f"\n## Files attached to it\n{files}\n" if files else "")
            + f"\n## The signed-off requirement you are amending (baseline {cr.version_from})\n{intake.requirement_md}\n")
    return {"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}


def cr_label(cr) -> str:
    return f"CR-{cr.number:03d}"


def _turn(m: dict) -> tuple[str, str]:
    """One stored chat message → (API role, text). Orion's messages are input to Echo, clearly labelled as his."""
    if m["role"] == "echo":
        return "assistant", m["text"]
    if m["role"] == "note":  # Echo's own message to Orion
        return "assistant", f"[I told Orion: {m['text']}]"
    if m["role"] == "orion" or m.get("cr"):
        if m.get("kind") == "reply":
            return "user", f"[Orion (CTO) → Echo] {m['text']}"
        files = f" Attached: {', '.join(m.get('attachments') or [])}." if m.get("attachments") else ""
        return "user", (f"[Orion (CTO) → Echo] I'm forwarding {m.get('label', 'a change request')} from the user. "
                        f"They wrote: “{m['text']}”.{files} Please work through it with them.")
    return "user", m["text"]


def chat_window(intake: Intake) -> list[dict]:
    """Which stored messages go to the model. An amendment only needs its own conversation (the baseline document
    holds everything agreed before). A long interview uses an anchored window, so the history prefix stays
    byte-identical for 20 turns and keeps hitting the prompt cache (a sliding window never would)."""
    chat = [m for m in (intake.chat or []) if m.get("text")]
    if intake.status == "amending" and intake.active_cr:
        start = next((i for i, m in enumerate(chat) if m.get("cr") == intake.active_cr), 0)
        return chat[start:]
    if len(chat) > 40:
        return chat[(len(chat) - 40) // 20 * 20:]
    return chat


def _chat_messages(intake: Intake, volatile: str) -> list[dict]:
    """History as real turns (a stable, cacheable prefix); the current state rides on the LAST user turn only."""
    turns: list[dict] = []
    for m in chat_window(intake):
        role, text = _turn(m)
        if turns and turns[-1]["role"] == role:
            turns[-1]["content"] += "\n\n" + text
        else:
            turns.append({"role": role, "content": text})
    while turns and turns[0]["role"] != "user":
        turns = turns[1:]
    if not turns or turns[-1]["role"] != "user":
        return []
    # cache breakpoint on the last assistant turn → everything before it is reused next time
    for i in range(len(turns) - 2, -1, -1):
        if turns[i]["role"] == "assistant":
            turns[i] = {"role": "assistant", "content": [
                {"type": "text", "text": turns[i]["content"], "cache_control": {"type": "ephemeral"}}]}
            break
    last = turns[-1]
    turns[-1] = {"role": "user", "content": [
        {"type": "text", "text": last["content"]},
        {"type": "text", "text": "\n\n---\n" + volatile + "\n\n" + CHAT_FORMAT},
    ]}
    return turns


def volatile_context(intake: Intake, cr) -> str:
    """The part that changes every turn. In an amendment that's only files uploaded since the change started."""
    if intake.status == "amending" and cr is not None:
        named = " ".join(m["text"] for m in chat_window(intake) if m["role"] == "user")
        fresh = [u for u in (intake.uploads or []) if u["name"] not in (cr.attachments or []) and u["name"] in named]
        return "\n\n".join(f"# File uploaded during this change: {u['name']}\n{u['text'][:20000]}" for u in fresh) or "(no new files)"
    return context_dump(intake, include_chat=False)


MESSAGE_ORION = Tool(
    name="message_orion",
    description=("Send a message to Orion (CTO). Use it when the user asks you to tell Orion something, when a file is "
                 "replaced, or when something changes scope, the plan or another agent's work. Orion answers in this "
                 "chat. Call it in the same response as `capture`."),
    schema=obj({
        "message": {"type": "string", "description": "What Orion needs to know, 1-3 sentences"},
        "replaced_files": {"type": "array", "description": "Files the user replaced (old → new upload name), or empty",
                           "items": obj({"old": {"type": "string"}, "new": {"type": "string"}})},
    }),
)


@handler("intake.chat", resumable=False)
async def chat(ctx: JobContext) -> None:
    pid = ctx.project_id
    intake = await load_intake(pid)
    known = questions()
    draft = Draft(pid, every=0.25)
    typed: list[str] = []

    async def on_text(t: str) -> None:  # plain text streams token by token → typewriter
        typed.append(t)
        await draft.set("".join(typed))

    from dataclasses import replace as _replace

    from app.orchestrator import changes, crewchat, runner as runner_mod

    cr_obj = await changes.get(intake.active_cr) if intake.active_cr else None
    triage = (cr_obj.triage or {}) if cr_obj else {}
    brief = triage.get("brief", "") or ctx.payload.get("brief", "")
    system = system_blocks()
    block = amend_block(intake, cr_obj, brief)
    if block:
        system = system + [block]
    notes: list[str] = []

    async def tell_orion(a: dict):
        files = [r["new"] for r in a["replaced_files"]]
        await crewchat.say(pid, "intake", "cto", a["message"], "update", files=files, cr=cr_obj.id if cr_obj else None)
        if cr_obj and a["replaced_files"]:
            atts = list(cr_obj.attachments or [])
            for r in a["replaced_files"]:
                atts = [r["new"] if x == r["old"] else x for x in atts]
                if r["new"] not in atts:
                    atts.append(r["new"])
            await changes.update(cr_obj.id, attachments=atts)
        notes.append(a["message"])
        await runner_mod.runner.enqueue("cto.inbox", project_id=pid, message=a["message"],
                                        cr_id=cr_obj.id if cr_obj else None, **{"from": "intake"})
        return "Delivered. Orion's answer will appear in this chat. Tell the user what you passed on."

    await _start(ctx, f"Echo is working on {changes.label(cr_obj)}" if cr_obj else "Echo is replying…")
    try:
        res = await run_loop(project_id=pid, agent="intake", system=system,
                             messages=_chat_messages(intake, volatile_context(intake, cr_obj)),
                             tools=[CAPTURE, _replace(MESSAGE_ORION, handler=tell_orion)], max_turns=3, purpose="chat",
                             on_text=on_text, effort=llm.agent_settings("intake").get("chat_effort", "medium"),
                             narrate=False)  # her reply is posted below as Echo → You
        cap = res.terminal.get("capture") or {"quick_replies": [], "answers": []}
        message = res.text.strip() or "Sorry, could you say that again?"
        intake = await load_intake(pid)
        answers, filled = dict(intake.answers or {}), []
        for a in cap["answers"]:
            if a["question_id"] in known and a["value"].strip():
                answers[a["question_id"]] = a["value"].strip()
                filled.append(a["question_id"])
        msgs = list(intake.chat or []) + [{
            "role": "echo", "text": message, "ts": utcnow().isoformat(),
            "filled": sorted(set(filled)), "quick_replies": [q for q in cap["quick_replies"] if q.strip()][:5]}]
        msgs += [{"role": "note", "to": "cto", "text": n, "ts": utcnow().isoformat()} for n in notes]
        # the user said it's all good: review now instead of waiting for "Ask Echo to review" (user, 10-04: "ECHO showing
        # NEEDS YOU but I already gave everything… do I need to click Ask Echo to review?")
        review_next = bool(cap.get("ready_to_review")) and intake.status in ("collecting", "reviewing")
        await update_intake(pid, chat=msgs, answers=answers, busy="reviewing" if review_next else None, draft=None)
        if review_next:
            await ctx.set_agent("intake", "working", "Reviewing your requirement before you sign off")
            await ctx.set_project(status="running", last_activity="Echo is reviewing your requirement")
        else:
            await ctx.set_agent("intake", "needs_approval", "Waiting for your reply")
            await ctx.set_project(status="waiting", last_activity="Echo asked you a question")
        labels = {q["id"]: q["label"] for q in known.values()}
        await ctx.emit("intake.updated", "Echo replied" + (f" and captured {len(set(filled))} answer(s)" if filled else ""),
                       agent="intake", asked=message[:1500],
                       captured=[{"topic": labels.get(k, k), "value": answers[k][:600]} for k in sorted(set(filled))])
        await crewchat.say(pid, "intake", "user", message, "chat",
                           captured=[labels.get(k, k) for k in sorted(set(filled))])
        if review_next:
            await runner_mod.runner.enqueue("intake.review", project_id=pid, mode="review")
    except Exception as exc:
        await _fail(ctx, exc, "intake.chat", {})
        raise

# ── review rounds ─────────────────────────────────────────────────────────────
SUBMIT_REVIEW = Tool(
    name="submit_review",
    terminal=True,
    description="Submit this review round. Call it exactly once, with everything filled in, in this field order.",
    schema=obj({
        "headline": {"type": "string", "description": "One sentence: where the requirement stands"},
        "understanding": {"type": "string", "description": (
            "Markdown playback of your understanding in business words: what the flow does, its steps from beginning to "
            "end (where from and where to, if anywhere), what starts it, volume and timing, what happens when things go "
            "wrong, data rules. Written so the business analyst can confirm or correct it.")},
        "completeness": {"type": "integer", "description": "0-100: how complete the business requirement is today"},
        "services": {"type": "array", "description": "Always empty: Archie decides the technology with the technical lead",
                     "items": obj({"service": {"type": "string"}, "purpose": {"type": "string"}})},
        "gaps": {"type": "array", "description": "Questions the user must answer", "items": obj({
            "question": {"type": "string"},
            "why": {"type": "string", "description": "Why it matters for the build"},
            "section": {"type": "string", "description": "Topic this belongs to"},
            "blocking": {"type": "boolean", "description": "True if the build can't proceed without it"},
            "suggested_answer": {"type": "string", "description": "A sensible default, or empty"},
        })},
        "suggestions": {"type": "array", "description": "Improvements the user can accept or reject", "items": obj({
            "title": {"type": "string"},
            "detail": {"type": "string", "description": "What and why, 1-2 sentences"},
            "category": {"type": "string", "enum": CATEGORIES},
            "impact": {"type": "string", "enum": ["low", "medium", "high"]},
            "question_id": {"type": "string", "description": "Topic-map ID this would fill, or empty"},
            "proposed_value": {"type": "string", "description": "Value to write into that answer if accepted, or empty"},
        })},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "conflicts": {"type": "array", "items": {"type": "string"}},
        "ready_for_signoff": {"type": "boolean"},
    }),
)

MODE_BRIEF = {
    "review": ("Review the requirement as it stands. Play back your understanding, list every real gap as a specific "
               "question, and suggest improvements. For answers marked 'not sure', add a suggestion with "
               "question_id and proposed_value. Don't repeat gaps the user already answered or suggestions "
               "they already decided on."),
    "reflect": ("Reflection pass. Step back and look for what the business might have missed: unusual cases, what "
                "happens when things fail, peaks, data protection, who must be told, deadlines, and dependencies on "
                "other teams. Only business points (the specialists after you cover the technical ones); only "
                "genuinely new points; keep the understanding updated."),
}


def review_progress(snap: Any, rnd: int, mode: str) -> str:
    """Human progress line from the partially-streamed review."""
    if not isinstance(snap, dict) or not snap:
        return f"Round {rnd}: reading everything you've told me…"
    lines = [f"Round {rnd} · {'looking for anything we missed' if mode == 'reflect' else 'reviewing'}"]
    if snap.get("headline"):
        lines.append(f"✓ Verdict: {snap['headline']}")
    if "understanding" in snap and "services" not in snap:
        lines.append("✍️ Writing up what I understood…")
    if snap.get("services"):
        lines.append(f"✓ AWS services identified: {', '.join(s.get('service', '') for s in snap['services'] if isinstance(s, dict))}")
    if "gaps" in snap:
        lines.append(f"{'✍️' if 'suggestions' not in snap else '✓'} Open questions: {len(snap.get('gaps') or [])}")
    if "suggestions" in snap:
        lines.append(f"{'✍️' if 'assumptions' not in snap else '✓'} Suggestions: {len(snap.get('suggestions') or [])}")
    if "assumptions" in snap:
        lines.append("✍️ Noting assumptions and conflicts…")
    return "\n".join(lines)


@handler("intake.review", resumable=False)
async def review(ctx: JobContext) -> None:
    pid, mode = ctx.project_id, ctx.payload.get("mode", "review")
    intake = await load_intake(pid)
    rnd = len(intake.rounds or []) + 1
    draft = Draft(pid, every=0.6)

    async def on_json(snap: Any) -> None:
        await draft.set(review_progress(snap, rnd, mode))

    await _start(ctx, f"Round {rnd}: {'looking for anything we missed' if mode == 'reflect' else 'reviewing your requirement'}")
    await draft.set(review_progress({}, rnd, mode), force=True)
    await ctx.set_project(status="running")
    try:
        user = (f"{MODE_BRIEF[mode]}\n\nThis is review round {rnd}.\n\n{context_dump(intake)}\n\n"
                "Call submit_review with your results.")
        res = await run_loop(project_id=pid, agent="intake", system=system_blocks(),
                             messages=[{"role": "user", "content": user}], tools=[SUBMIT_REVIEW],
                             max_turns=3, purpose=f"review-{mode}", on_json=on_json, narrate=False)
        data = res.terminal.get("submit_review")
        if not data:
            raise AgentError("Echo finished without submitting a review.")
        for i, g in enumerate(data["gaps"]):
            g["id"] = f"g{rnd}-{i + 1}"
        for i, s in enumerate(data["suggestions"]):
            s["id"] = f"s{rnd}-{i + 1}"
        entry = {"round": rnd, "mode": mode, "at": utcnow().isoformat(), **data}
        intake = await load_intake(pid)
        await update_intake(pid, rounds=list(intake.rounds or []) + [entry], status="reviewing", busy=None, draft=None)
        await ctx.set_agent("intake", "needs_approval", f"Round {rnd} ready: {len(data['gaps'])} question(s), "
                                                        f"{len(data['suggestions'])} suggestion(s)")
        await ctx.set_project(status="waiting", last_activity=f"Echo finished review round {rnd}. Waiting for you")
        await emit_updated(ctx, f"Echo finished review round {rnd}: {data['headline']}")
        from app.orchestrator import crewchat

        await crewchat.say(pid, "intake", "user", review_line(entry), "update")
    except Exception as exc:
        await _fail(ctx, exc, "intake.review", {"mode": mode})
        raise


def review_line(r: dict) -> str:
    gaps = [g["question"] for g in r.get("gaps", [])]
    blocking = sum(1 for g in r.get("gaps", []) if g.get("blocking"))
    nxt = (" Next: answer the blocking question(s), then press **Sign off**." if blocking else
           " Next: answer what you like (optional), then press **Sign off**: I write 00_requirement.md and Orion starts the plan.")
    return (f"Review round {r['round']} ({'what did we miss?' if r['mode'] == 'reflect' else 'review'}): {r['headline']} "
            f"Completeness {r.get('completeness', '?')}%. {len(gaps)} question(s) for you ({blocking} blocking), "
            f"{len(r.get('suggestions', []))} suggestion(s)." + nxt
            + ("\n\n" + "\n".join(f"- {q}" for q in gaps[:6]) if gaps else ""))


# ── sign-off: write and freeze 00_requirement.md ─────────────────────────────
DOC_BRIEF = """Write the final, signed-off requirement document `00_requirement.md` in markdown.

It is the BUSINESS requirement: the single source of truth for what the flow must do, in business words. The
specialists after you (Atlas with the data analyst, Archie with the technical lead, then Terra, Dev and Quinn) settle
the data and technical details in their own conversations, so don't decide any technology here. Organise it as:
1. Overview (what the flow does and why, interface name/ID, scope and out of scope)
2. The flow, step by step (from beginning to end, in business words; where the information comes from and where it
   must end up, only if there is such a place)
3. What starts it, volume, timing and ordering
4. When things go wrong (what must happen to the data, who must be told and how quickly)
5. Data sensitivity, retention, audit trail, acceptable downtime
6. Deadlines, dependencies and business rules
7. Notes for the specialists (anything technical the user volunteered, e.g. samples, a mapping sheet, a preferred
   language: quote it and name who uses it, e.g. "for Atlas", "for Archie"; files by name in inputs/). Omit if none.
8. Accepted improvements (from your suggestions the user accepted)
9. Assumptions
10. Open points (only if the user explicitly accepted proceeding with them)
11. Sign-off (who, when, number of review rounds)

Apply the user's answers to your questions and their accepted suggestions. Rejected suggestions must not appear.
Do not invent anything that wasn't agreed. Reply with ONLY the markdown document, starting with the "# " title."""


AMEND_BRIEF = """Update the signed-off requirement document for the agreed change request {label}.

Rules (diff-style editing, as the organisation requires):
- Start from the baseline document below and change ONLY what this change affects. Keep every other section,
  sentence and table exactly as it is, so the version diff shows just this change.
- Put new rules where they belong (e.g. logging rules in section 8, packaging in section 9, files in a references list).
  Name attached files and say where they are stored (inputs/<name>) and how the crew must use them.
- Record any open questions about the change under Open points.
- Update the title block's version to {new_v}, and add or extend a "Change history" section at the end:
  | Version | Date | Change | Change request |   with a row for {new_v}.
- Reply with ONLY the complete updated markdown document, starting with the "# " title."""


NAMING_LATER = ("About names: you didn't set a naming convention, so the crew names everything `orkestra-<interface>-<env>-<name>` "
                "(in this sandbox every name must start with `orkestra-`; your team's convention can follow it). You can set your "
                "convention any time: on the AWS tab before anything is built (Archie and Terra use it), or rename any resource "
                "and the whole prefix there later (free before the deploy; after it, Terra replaces the renamed resources in a "
                "plan you approve).")


async def _talks_on(project_id: str) -> bool:
    """A requirement signed off from now on is business-only (10-05), so its project gets the specialists' kickoff
    conversations (agents/talk.py) unless the user turned them off in the project's settings."""
    async with SessionLocal() as db:
        p = await db.get(Project, project_id)
        settings = dict(p.settings or {})
        if "talks" not in settings:
            settings["talks"] = True
            p.settings = settings
            await db.commit()
        return bool(settings["talks"])


def naming_left_open(answer: str) -> bool:
    """No convention given (empty, "not sure", Echo's "default … rename later")."""
    a = (answer or "").strip().lower()
    return not a or a in ("not sure", "?", "n/a", "none", "no") or "default" in a or "rename later" in a or a == "__suggest__"


@handler("intake.finalize", resumable=False)
async def finalize(ctx: JobContext) -> None:
    from app.orchestrator import changes, crewchat

    pid = ctx.project_id
    intake = await load_intake(pid)
    amending = intake.status == "amending" and intake.active_cr
    cr = await changes.get(intake.active_cr) if amending else None
    async with SessionLocal() as db:
        project = await db.get(Project, pid)
        owner = await db.get(User, project.owner_id)
    stamp = f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC"
    store = ProjectStore(pid)
    version = store.manifest()["current_version"]
    signoff = (f"Signed off by {owner.display_name} (@{owner.username}) on {stamp}"
               + (f" (amendment {changes.label(cr)}, {version})." if cr else f" after {len(intake.rounds or [])} review round(s)."))
    draft = Draft(pid, every=0.5)
    typed: list[str] = []

    async def on_text(t: str) -> None:  # the document appears as it's written
        typed.append(t)
        await draft.set("".join(typed))

    await _start(ctx, f"Writing 00_requirement.md {version}" + (f" for {changes.label(cr)}" if cr else ""))
    await ctx.set_project(status="running")
    try:
        if cr:
            chat_all = intake.chat or []
            start = next((i for i, m in enumerate(chat_all) if m.get("cr") == cr.id), 0)
            who = {"echo": "ECHO", "user": "USER", "orion": "ORION (CTO)", "note": "ECHO → ORION"}
            convo = "\n".join(f"{who.get(m['role'], m['role'].upper())}: {m['text']}" for m in chat_all[start:] if m.get("text"))[-30000:]
            prompt = (AMEND_BRIEF.format(label=changes.label(cr), new_v=version) + f"\n\n{signoff}\n\n# The change request\n{cr.text}\n\n"
                      f"# The amendment conversation (latest part)\n{convo}\n\n"
                      + "\n\n".join(f"# Attached file: {u['name']}\n{u['text'][:20000]}" for u in (intake.uploads or [])
                                    if u["name"] in (cr.attachments or []) or u["name"] in convo)
                      + f"\n\n# Baseline document ({cr.version_from})\n{intake.requirement_md}")
        else:
            prompt = f"{DOC_BRIEF}\n\n{signoff}\n\n{context_dump(intake)}"
        res = await run_loop(project_id=pid, agent="intake", system=system_blocks(),
                             messages=[{"role": "user", "content": prompt}],
                             max_turns=1, purpose="amend" if cr else "finalize", on_text=on_text, narrate=False)
        md = res.text.strip()
        if "# " in md and not md.startswith("#"):
            md = md[md.index("# "):]
        if not md:
            raise AgentError("Echo finished without writing the requirement.")
        old_md = intake.requirement_md or ""
        store.write("00_requirement.md", md)
        store.append_changelog(version, [f"Requirement {'amended' if cr else 'signed off'} — {signoff}"])
        await update_intake(pid, requirement_md=md, status="signed_off", signed_off_at=utcnow(), busy=None, draft=None,
                            active_cr=None)
        try:  # Echo's sign-off document (signoff/00-requirement.md); tasks for Atlas/Archie/Echo/Orion that waited for her
            from app.services import signoff
            from app.services import tickets as tk

            await signoff.write_requirement(pid)
            if not cr:
                await tk.resume_queued(pid)
        except Exception:  # noqa: BLE001 (a document must never block the sign-off)
            pass
        from app.orchestrator import runner as runner_mod

        if cr:
            diff = changes.unified_diff(old_md, md, cr.version_from or "previous", version)
            store.write(f"changes/{changes.label(cr)}.diff", diff)
            await changes.update(cr.id, diff=diff, status="planning", version_to=version)
            added = sum(1 for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++"))
            removed = sum(1 for ln in diff.splitlines() if ln.startswith("-") and not ln.startswith("---"))
            await ctx.set_agent("intake", "done", f"Requirement {version} signed off ({changes.label(cr)}: +{added} −{removed} lines)")
            await ctx.set_project(last_activity=f"Requirement {version} signed off. Orion is re-planning for {changes.label(cr)}")
            await emit_updated(ctx, f"Requirement updated to {version} for {changes.label(cr)} (+{added} −{removed} lines). Orion is re-planning")
            await crewchat.say(pid, "intake", "cto", f"{changes.label(cr)} is signed off by the user. Requirement {version} is "
                               f"written: +{added} −{removed} lines, diff saved in changes/{changes.label(cr)}.diff. Over to you, Orion.",
                               "handoff", files=["00_requirement.md", f"changes/{changes.label(cr)}.diff"], version=version)
            affected = [a["agent"] for a in (cr.triage or {}).get("affected_agents", [])]
            await changes.inform_agents(pid, cr, affected, f"requirement is now {version}. Use {version} from here on.")
            await runner_mod.runner.enqueue("cto.plan", project_id=pid, cr_id=cr.id)
        else:
            await ctx.set_agent("intake", "done", "Requirement signed off · 00_requirement.md")
            await ctx.set_project(progress=round(1 / 6, 3), last_activity="Requirement signed off. Orion is planning")
            await emit_updated(ctx, "Requirement signed off. 00_requirement.md is frozen")
            await crewchat.say(pid, "intake", "cto", f"The user signed off the requirement after {len(intake.rounds or [])} review "
                               f"rounds. 00_requirement.md ({version}) is frozen. Over to you, Orion.", "handoff",
                               files=["00_requirement.md"], version=version)
            talks = await _talks_on(pid)  # this requirement is business-only: the specialists' kickoff conversations follow
            if talks:
                await crewchat.say(pid, "intake", "user", "Thanks! That's the business side done. Next, each specialist asks only their "
                                   "own field's questions, on their tab: Atlas asks your data analyst about the data, Archie proposes "
                                   "the architecture to your technical lead, then Terra, Dev and Quinn check in with your platform "
                                   "engineer, developer and tester.", "update")
            elif naming_left_open((intake.answers or {}).get("build.naming", "")):  # user, 10-03: "inform the user they can do it later"
                await crewchat.say(pid, "intake", "user", NAMING_LATER, "update")
            await runner_mod.runner.enqueue("cto.plan", project_id=pid)
    except Exception as exc:
        await _fail(ctx, exc, "intake.finalize", {})
        raise