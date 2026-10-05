from __future__ import annotations

import re
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.projects import owned
from app.core.security import current_user
from app.db.base import get_db, utcnow
from app.db.models import Intake, User
from app.orchestrator import runner as runner_mod
from app.orchestrator.bus import bus
from app.services.requirement_template import SUGGEST, completeness, parse_upload, questionnaire, questions
from app.services.storage import ProjectStore

router = APIRouter(prefix="/api/projects/{project_id}/intake", tags=["intake"])
MAX_UPLOAD_BYTES = 8 * 1024 * 1024
# One review round before sign-off (user, 10-04: "2 reviews is too much… cost also wasted in the 2nd review"; on 09-30
# they had asked for 2–3). "What did we miss?" and "Review again" stay available; answers to the round's questions go
# straight into 00_requirement.md, so they don't need another round.
MIN_ROUNDS = 1


async def _intake(project_id: str, user: User, db: AsyncSession) -> Intake:
    await owned(project_id, user, db)
    row = await db.get(Intake, project_id)
    if row is None:
        row = Intake(project_id=project_id)
        db.add(row)
        await db.commit()
        await db.refresh(row)
    return row


def _state(row: Intake) -> dict:
    return {
        "questionnaire": questionnaire(),
        "suggest_token": SUGGEST,
        "answers": row.answers or {},
        "completeness": completeness(row.answers or {}),
        "uploads": [{"name": u["name"], "kind": u["kind"], "chars": u["chars"]} for u in row.uploads or []],
        "chat": row.chat or [],
        "rounds": row.rounds or [],
        "decisions": row.decisions or {},
        "gap_answers": row.gap_answers or {},
        "status": row.status,
        "busy": row.busy,
        "min_rounds": 0 if row.status == "amending" else MIN_ROUNDS,
        "active_cr": row.active_cr,
        "requirement_md": row.requirement_md,
        "plan": row.plan,
        "signed_off_at": row.signed_off_at,
        "draft": row.draft,
        "last_error": row.last_error,
    }


def _editable(row: Intake) -> None:
    if row.status == "signed_off":
        raise HTTPException(409, "The requirement is signed off and frozen. Changes go through a change request.")


async def _start(row: Intake, db: AsyncSession, kind: str, busy: str, **payload) -> None:
    if row.busy:
        raise HTTPException(409, f"Echo is busy ({row.busy}). Wait for it to finish.")
    row.busy = busy
    await db.commit()
    await runner_mod.runner.enqueue(kind, project_id=row.project_id, **payload)


@router.get("")
async def get_state(project_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    return _state(await _intake(project_id, user, db))


class AnswersIn(BaseModel):
    answers: dict[str, str]


@router.put("/answers")
async def save_answers(project_id: str, body: AnswersIn, user: User = Depends(current_user),
                       db: AsyncSession = Depends(get_db)):
    row = await _intake(project_id, user, db)
    _editable(row)
    known = questions()
    merged = dict(row.answers or {})
    for k, v in body.answers.items():
        if k in known:
            if v.strip():
                merged[k] = v.strip()[:20_000]
            else:
                merged.pop(k, None)
    row.answers = merged
    await db.commit()
    return {"answers": merged, "completeness": completeness(merged)}


@router.get("/draft")
async def draft(project_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """Tiny payload polled every ~0.6 s while Echo works: the live reply / progress text."""
    row = await _intake(project_id, user, db)
    return {"busy": row.busy, "draft": row.draft, "chat_len": len(row.chat or []), "rounds": len(row.rounds or []),
            "last_error": row.last_error}


@router.post("/retry", status_code=202)
async def retry(project_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    row = await _intake(project_id, user, db)
    err = row.last_error
    if not err:
        raise HTTPException(409, "Nothing to retry")
    busy = {"intake.chat": "chatting", "intake.review": "reviewing", "intake.finalize": "finalizing"}[err["kind"]]
    row.last_error = None
    await _start(row, db, err["kind"], busy, **(err.get("payload") or {}))
    return _state(row)


# The Word template download was removed (user, 10-03: "that word template idea is unnecessary; let the user upload any
# document they created as the requirement"). A filled copy of the old template still uploads and fills the answers.
@router.post("/upload")
async def upload(project_id: str, file: UploadFile = File(...), user: User = Depends(current_user),
                 db: AsyncSession = Depends(get_db)):
    return await _store_upload(project_id, file.filename or "upload", await file.read(), user, db)


class UploadIn(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    data: str = Field(description="the file, base64")


@router.post("/upload-json")
async def upload_json(project_id: str, body: UploadIn, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """The same upload as JSON (base64). The UI uses this one: company proxies block some files sent as multipart
    uploads by their type (user, 10-04: `logger (2).py` never reached the server), "Echo must accept any kind of file"."""
    import base64
    import binascii

    try:
        data = base64.b64decode(body.data, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(422, "The file didn't arrive intact; try again") from None
    return await _store_upload(project_id, body.name, data, user, db)


async def _store_upload(project_id: str, filename: str, data: bytes, user: User, db: AsyncSession) -> dict:
    row = await _intake(project_id, user, db)
    _editable(row)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File is larger than 8 MB")
    if not data:
        raise HTTPException(422, f"{filename} is empty")
    name = re.sub(r"[^A-Za-z0-9._ ()-]+", "_", filename)[:120]
    try:
        parsed = parse_upload(name, data)
    except Exception as exc:  # noqa: BLE001 - a damaged document is still kept (any kind of file is accepted)
        parsed = {"text": f"[{name}: couldn't be read as a document ({str(exc)[:120]}); kept in inputs/{name}]"}
    ProjectStore(project_id).write(f"inputs/{name}", data)
    if "answers" in parsed:
        row.answers = {**(row.answers or {}), **parsed["answers"]}
        result = {"kind": "template", "filled": len(parsed["answers"])}
    else:
        text = parsed["text"].strip() or f"[{name}: no readable text in it; kept in inputs/{name}]"
        row.uploads = [u for u in (row.uploads or []) if u["name"] != name] + [
            {"name": name, "kind": "document", "chars": len(text), "text": text}]
        result = {"kind": "document", "chars": len(text)}
    await db.commit()
    await bus.publish("intake.updated", project_id=project_id, user_id=user.id, agent="intake",
                      message=f"Uploaded {name}" + (f": filled {result['filled']} answers" if result["kind"] == "template" else ""))
    return {**result, "name": name, "state": _state(row)}


@router.delete("/uploads/{name}")
async def remove_upload(project_id: str, name: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    row = await _intake(project_id, user, db)
    _editable(row)
    row.uploads = [u for u in (row.uploads or []) if u["name"] != name]
    await db.commit()
    return _state(row)


class ChatIn(BaseModel):
    message: str = Field(default="", max_length=20000)
    attachments: list[str] = []


@router.post("/chat", status_code=202)
async def chat(project_id: str, body: ChatIn, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    row = await _intake(project_id, user, db)
    _editable(row)
    text = body.message.strip()
    if body.attachments:
        text = (text + "\n\n" if text else "") + "📎 Attached: " + ", ".join(body.attachments)
    if not text:
        raise HTTPException(422, "Type a message or attach a file")
    row.chat = list(row.chat or []) + [{"role": "user", "text": text, "ts": utcnow().isoformat(),
                                        "attachments": body.attachments}]
    await _start(row, db, "intake.chat", "chatting")
    from app.orchestrator import crewchat

    await crewchat.say(project_id, "user", "intake", text, "chat", files=body.attachments)
    return _state(row)


class ReviewIn(BaseModel):
    mode: Literal["review", "reflect"] = "review"


@router.post("/review", status_code=202)
async def review(project_id: str, body: ReviewIn, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    row = await _intake(project_id, user, db)
    _editable(row)
    c = completeness(row.answers or {})
    if c["answered"] == 0 and not row.uploads and not row.chat:
        raise HTTPException(422, "Give Echo something to work with first: answer some questions, upload a document or chat.")
    await _start(row, db, "intake.review", "reviewing", mode=body.mode)
    return _state(row)


class DecisionsIn(BaseModel):
    decisions: dict[str, Literal["accepted", "rejected"]] = {}
    gap_answers: dict[str, str] = {}


@router.post("/decisions")
async def decisions(project_id: str, body: DecisionsIn, user: User = Depends(current_user),
                    db: AsyncSession = Depends(get_db)):
    row = await _intake(project_id, user, db)
    _editable(row)
    row.decisions = {**(row.decisions or {}), **body.decisions}
    row.gap_answers = {**(row.gap_answers or {}), **{k: v.strip() for k, v in body.gap_answers.items() if v.strip()}}
    # An accepted suggestion that targets a questionnaire answer is applied to it straight away.
    known, answers = questions(), dict(row.answers or {})
    for r in row.rounds or []:
        for s in r["suggestions"]:
            if body.decisions.get(s["id"]) == "accepted" and s.get("question_id") in known and s.get("proposed_value"):
                answers[s["question_id"]] = s["proposed_value"]
    row.answers = answers
    await db.commit()
    return _state(row)


class SignoffIn(BaseModel):
    accept_open_gaps: bool = False


@router.post("/signoff", status_code=202)
async def signoff(project_id: str, body: SignoffIn, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    row = await _intake(project_id, user, db)
    _editable(row)
    rounds = row.rounds or []
    if row.status == "amending":  # an amendment is confirmed in the conversation; no review rounds required
        await _start(row, db, "intake.finalize", "finalizing")
        return _state(row)
    if len(rounds) < MIN_ROUNDS:
        raise HTTPException(409, f"Echo confirms the requirement at least {MIN_ROUNDS} times before sign-off. "
                                 f"Run {MIN_ROUNDS - len(rounds)} more review round(s).")
    open_blocking = [g for g in rounds[-1]["gaps"] if g["blocking"] and not (row.gap_answers or {}).get(g["id"])]
    if open_blocking and not body.accept_open_gaps:
        raise HTTPException(409, f"{len(open_blocking)} blocking question(s) from the last round are unanswered.")
    await _start(row, db, "intake.finalize", "finalizing")
    return _state(row)
