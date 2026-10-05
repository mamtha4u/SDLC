from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, utcnow


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("usr"))
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))
    theme: Mapped[str] = mapped_column(String(32), default="aurora")
    # Settings page (10-05): {"motion": "full"|"calm", "celebrate": bool, "default_budget_usd": float}
    prefs: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Session(Base):
    __tablename__ = "sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("prj"))
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    # draft | running | waiting | completed | failed | paused
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    current_agent: Mapped[str | None] = mapped_column(String(32), nullable=True)
    current_version: Mapped[str] = mapped_column(String(16), default="v1")
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    budget_usd: Mapped[float] = mapped_column(Float, default=20.0)
    paused: Mapped[bool] = mapped_column(Boolean, default=False)  # kill switch
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    accent: Mapped[str] = mapped_column(String(16), default="violet")
    # project settings (10-05): its own theme while you're inside it (None = your account's), and {"drift_watch": bool}
    theme: Mapped[str | None] = mapped_column(String(32), nullable=True)
    settings: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    last_activity: Mapped[str] = mapped_column(String(255), default="Project created")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    agent_states: Mapped[list["AgentState"]] = relationship(cascade="all, delete-orphan", lazy="selectin")


class AgentState(Base):
    """Live state of one agent in one project (drives the pipeline view)."""

    __tablename__ = "agent_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    agent: Mapped[str] = mapped_column(String(32))
    # waiting | working | needs_approval | done | failed | paused | blocked
    status: Mapped[str] = mapped_column(String(16), default="waiting")
    activity: Mapped[str] = mapped_column(String(255), default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retries: Mapped[int] = mapped_column(Integer, default=0)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)


class Job(Base):
    """Background work item. Survives restarts; the runner resumes or marks it interrupted."""

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("job"))
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=True)
    kind: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    # queued | running | done | failed | interrupted | cancelled
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    checkpoint: Mapped[dict] = mapped_column(JSON, default=dict)  # lets a handler resume mid-way
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Intake(Base):
    """Echo's requirements session for one project: answers, uploads, chat, review rounds, sign-off."""

    __tablename__ = "intakes"

    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    answers: Mapped[dict] = mapped_column(JSON, default=dict)          # question_id -> value ("__suggest__" = let Echo propose)
    uploads: Mapped[list] = mapped_column(JSON, default=list)          # [{name, kind, chars, text}]
    chat: Mapped[list] = mapped_column(JSON, default=list)             # [{role, text, ts, filled?}]
    rounds: Mapped[list] = mapped_column(JSON, default=list)           # Echo's review results, one per round
    decisions: Mapped[dict] = mapped_column(JSON, default=dict)        # suggestion_id -> accepted | rejected
    gap_answers: Mapped[dict] = mapped_column(JSON, default=dict)      # gap_id -> user's answer
    status: Mapped[str] = mapped_column(String(16), default="collecting")  # collecting | reviewing | signed_off | amending
    active_cr: Mapped[str | None] = mapped_column(String(32), nullable=True)  # change request Echo is amending for
    busy: Mapped[str | None] = mapped_column(String(16), nullable=True)    # reviewing | chatting | finalizing
    draft: Mapped[str | None] = mapped_column(Text, nullable=True)         # live: Echo's reply as it's written / review progress
    last_error: Mapped[dict | None] = mapped_column(JSON, nullable=True)   # {kind, payload, message} → "Try again"
    requirement_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    plan: Mapped[dict | None] = mapped_column(JSON, nullable=True)     # Orion's delivery plan after sign-off
    signed_off_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class PhaseTalk(Base):
    """An agent's kickoff interview with the person who owns its phase (user, 10-05: "each area is handled by a different
    person"): Atlas asks the data analyst, Archie the technical lead, Terra the platform engineer, Dev the developer, Quinn
    the tester. The agent keeps asking until it understands, then starts its work (agents/talk.py)."""

    __tablename__ = "phase_talks"

    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    agent: Mapped[str] = mapped_column(String(8), primary_key=True)   # ba | ta | tp | de | qa
    chat: Mapped[list] = mapped_column(JSON, default=list)             # [{role: agent|user|note, text, ts, filled?, quick_replies?, attachments?}]
    answers: Mapped[dict] = mapped_column(JSON, default=dict)          # topic id -> value (prompts/phase_topics.yaml)
    uploads: Mapped[list] = mapped_column(JSON, default=list)          # [{name, kind, chars, text}] files given in this talk
    status: Mapped[str] = mapped_column(String(16), default="talking")  # talking | done | skipped
    busy: Mapped[str | None] = mapped_column(String(16), nullable=True)    # thinking (a turn is running)
    draft: Mapped[str | None] = mapped_column(Text, nullable=True)         # the agent's reply as it's written
    last_error: Mapped[dict | None] = mapped_column(JSON, nullable=True)   # {kind, message, at} → "Try again"
    work: Mapped[dict | None] = mapped_column(JSON, nullable=True)         # {"job": kind, "payload": {...}}: started when the talk ends
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ChangeRequest(Base):
    """A new or changed requirement (or plan/mapping feedback) raised after sign-off.
    Orion triages it and routes it: requirement → Echo amends (new version), plan → Orion, mapping → Atlas."""

    __tablename__ = "change_requests"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("cr"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer)                         # CR-001, CR-002 … per project
    text: Mapped[str] = mapped_column(Text)
    attachments: Mapped[list] = mapped_column(JSON, default=list)        # file names stored in inputs/
    source: Mapped[str] = mapped_column(String(24), default="user")      # user | gate:<stage>
    # triage | clarifying (Echo interviewing) | planning (Orion re-planning) | in_progress | done
    status: Mapped[str] = mapped_column(String(20), default="triage", index=True)
    route: Mapped[str | None] = mapped_column(String(20), nullable=True)  # requirement | plan | mapping
    triage: Mapped[dict | None] = mapped_column(JSON, nullable=True)      # Orion's analysis
    version_from: Mapped[str | None] = mapped_column(String(16), nullable=True)
    version_to: Mapped[str | None] = mapped_column(String(16), nullable=True)
    diff: Mapped[str | None] = mapped_column(Text, nullable=True)         # unified diff of 00_requirement.md
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class CrewMessage(Base):
    """The crew room: what the agents say to each other (hand-offs, acks, questions, issues, fixes).
    Written only at real hand-off points by the platform or by an agent's tool call — never invented."""

    __tablename__ = "crew_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    sender: Mapped[str] = mapped_column(String(16))          # agent key, or "user" for your decisions
    to: Mapped[str] = mapped_column(String(16), default="crew")  # agent key | crew | user
    # handoff | ack | assign | question | answer | update | work | issue | fix | done | decision
    kind: Mapped[str] = mapped_column(String(16), default="update")
    text: Mapped[str] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Ticket(Base):
    """A bug or task between the crew (and the user): raised by Quinn's live tests or by a person, assigned to the agent
    who owns the fix (Dev for code, Terra for infrastructure), resolved with a comment, verified and closed by Quinn."""

    __tablename__ = "tickets"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("tkt"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer)                          # TKT-001 … per project
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    steps: Mapped[str] = mapped_column(Text, default="")
    expected: Mapped[str] = mapped_column(Text, default="")
    actual: Mapped[str] = mapped_column(Text, default="")
    severity: Mapped[str] = mapped_column(String(12), default="major")   # critical | major | minor
    area: Mapped[str] = mapped_column(String(12), default="code")        # code | infra | design | other
    # open → in_progress → resolved (back with QA) → closed, or reopened (back with the fixer)
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    assignee: Mapped[str] = mapped_column(String(16), default="de")      # agent key | user
    reporter: Mapped[str] = mapped_column(String(16), default="qa")      # agent key | user
    check_id: Mapped[str | None] = mapped_column(String(40), nullable=True)   # Quinn's live check that found it
    version_found: Mapped[str | None] = mapped_column(String(16), nullable=True)
    version_fixed: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class TicketComment(Base):
    """One line in a ticket's history: a comment, a status change or a re-assignment (by an agent or a person)."""

    __tablename__ = "ticket_comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticket_id: Mapped[str] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), index=True)
    author: Mapped[str] = mapped_column(String(16))                       # agent key | user
    kind: Mapped[str] = mapped_column(String(12), default="comment")     # comment | status | assign | created
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AssistantMessage(Base):
    """Sage, the project's Q&A assistant: one row per question or answer (answers stream into `text`)."""

    __tablename__ = "assistant_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(12))                     # user | assistant
    text: Mapped[str] = mapped_column(Text, default="")
    refs: Mapped[list] = mapped_column(JSON, default=list)             # files Sage read to answer
    status: Mapped[str] = mapped_column(String(12), default="done")   # streaming | done | error
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Approval(Base):
    """A human gate between stages: plan → mapping → design → infra → code → QA. Nothing advances without one."""

    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("apr"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    stage: Mapped[str] = mapped_column(String(24))          # plan | mapping | design | infra | code | qa
    agent: Mapped[str] = mapped_column(String(32))          # who produced the thing being approved
    title: Mapped[str] = mapped_column(String(200))
    summary: Mapped[str] = mapped_column(Text, default="")
    artifacts: Mapped[list] = mapped_column(JSON, default=list)   # project file paths to review
    # pending | approved | changes_requested | superseded
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LlmCall(Base):
    """Ledger: one row per model call. Feeds the per-project Usage tab and the global Usage page."""

    __tablename__ = "llm_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    owner_id: Mapped[str] = mapped_column(String(32), index=True)
    agent: Mapped[str] = mapped_column(String(32), index=True)
    model_key: Mapped[str] = mapped_column(String(32))
    model_id: Mapped[str] = mapped_column(String(96))
    purpose: Mapped[str] = mapped_column(String(64), default="")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    price: Mapped[dict] = mapped_column(JSON, default=dict)  # USD/MTok used for this call
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    stop_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Event(Base):
    """Append-only activity feed. Also the replay log for SSE reconnects (Last-Event-ID)."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    type: Mapped[str] = mapped_column(String(48))
    agent: Mapped[str | None] = mapped_column(String(32), nullable=True)
    message: Mapped[str] = mapped_column(Text, default="")
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
