"""Terra's drift watch (user, 10-03): what changed outside Terraform, the restore that applies the source of truth
directly (no Dev or Quinn steps), and keeping the changes (a change request, a ticket for Dev). No AWS calls."""
import asyncio
import copy
import io
import json
import zipfile

import pytest
from sqlalchemy import select

from app.agents import de, drift
from app.db.base import SessionLocal
from app.db.models import ChangeRequest
from app.orchestrator import flow
from app.orchestrator.flow import STAGES
from app.orchestrator.runner import JobContext
from app.services import aws_access
from app.services import tickets as tk
from app.orchestrator import runner as runner_mod
from app.tools import terraform
from tests.test_codereview import _project


@pytest.fixture
def captured(monkeypatch):
    jobs: list[tuple[str, dict]] = []

    class Runner:
        async def enqueue(self, kind, project_id=None, **payload):
            jobs.append((kind, payload))
            return "job_x"
    monkeypatch.setattr(runner_mod, "runner", Runner())
    return jobs

REP = {
    "checked_at": "2026-10-03T10:00:00Z", "clean": False, "total": 12, "unchanged": 10,
    "settings": [{"address": "aws_sqs_queue.out", "type": "aws_sqs_queue", "name": "orkestra-x-out", "deleted": False,
                  "fields": [{"key": "visibility_timeout_seconds", "terraform": "30", "aws": "45"}]}],
    "deleted": [{"address": 'aws_lambda_layer_version.package["util"]', "type": "aws_lambda_layer_version", "name": "orkestra-x-util"}],
    "code": [{"function_name": "orkestra-x-echo", "key": "echo", "expected_version": "v1.2", "expected_sha": "s1", "live_sha": "s2",
              "last_modified": "2026-10-03T09:58:00Z", "files": [{"file": "handler.py", "change": "changed in AWS", "diff": "-a\n+b"}]}],
    "restore": {"counts": {"create": 1, "update": 2, "replace": 0, "delete": 0},
                "changes": [{"address": "aws_sqs_queue.out", "type": "aws_sqs_queue", "name": "orkestra-x-out", "action": "update",
                             "fields": ["visibility_timeout_seconds"]}]},
}


def _zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, c in files.items():
            z.writestr(n, c)
    return buf.getvalue()


def test_a_changed_policy_shows_only_the_lines_that_differ():
    pol = lambda res: [{"name": "p", "policy": json.dumps({"Statement": [{"Action": "logs:PutLogEvents", "Resource": res}]})}]  # noqa: E731
    lines = terraform.changed_lines(pol("arn:aws:logs:eu-west-1:1:log-group:/aws/lambda/x"), pol("arn:aws:logs:eu-west-1:1:log-group:/aws/lambda/x:*"))
    assert lines == ['- "Resource": "arn:aws:logs:eu-west-1:1:log-group:/aws/lambda/x"', '+ "Resource": "arn:aws:logs:eu-west-1:1:log-group:/aws/lambda/x:*"']
    assert terraform.changed_lines(30, 45) == []  # plain values: the two values say it


def test_the_drift_gate_restores_directly():
    assert STAGES["drift"]["next"] == "tp.restore" and STAGES["drift"]["agent"] == "tp"  # no Dev, no Quinn
    assert STAGES["drift"]["redo"] == "tp.drift_keep"  # "keep the changes"
    assert flow.accept_allowed("any", "drift")  # "leave it for now"


def test_the_code_diff_shows_what_changed_in_the_console():
    d = drift._diff(_zip({"handler.py": "a\nb\n", "util.py": "x"}), _zip({"handler.py": "a\nB\n", "extra.py": "y"}), "v1.2")
    assert [(f["file"], f["change"]) for f in d] == [("extra.py", "added in AWS"), ("handler.py", "changed in AWS"), ("util.py", "deleted in AWS")]
    assert "-b" in d[1]["diff"] and "+B" in d[1]["diff"] and "Dev's v1.2/handler.py" in d[1]["diff"]
    assert "isn't on the host" in drift._diff(None, _zip({"a.py": ""}), "v1")[0]["diff"]


def test_the_source_of_truth_is_devs_applied_package(tmp_path):
    pid = f"prj_drift{tmp_path.name[-6:]}"
    aws_access.save(pid, {"code": {"echo": {"fingerprint": "s1", "applied": "s1", "applied_version": "v1", "function_name": "orkestra-x-echo",
                                            "source_dir": "src/echo"},
                                   "next": {"fingerprint": "s2", "applied": None, "function_name": "orkestra-x-next"}}, "layers": {}}, de.PACKAGES)
    exp = drift._expected_code(pid, {terraform.PACKAGES_TF_PATH: terraform.PACKAGES_TF})
    assert list(exp) == ["orkestra-x-echo"] and exp["orkestra-x-echo"]["sha"] == "s1"  # handed over but not applied: not live yet
    aws_access.save(pid, {"functions": {"orkestra-x-echo": {"sha": "s9", "code_version": "v0", "key": "echo", "source_dir": "src/echo"}}}, "code")
    assert drift._expected_code(pid, {"infra/main.tf": ""})["orkestra-x-echo"]["sha"] == "s9"  # older projects: Dev's last deploy


def test_the_report_shows_changed_and_unchanged():
    md = drift.markdown(REP)
    for text in ("visibility_timeout_seconds", "`30`", "`45`", "Deleted in AWS", "Code changed in AWS: orkestra-x-echo", "What Restore does",
                 "Unchanged: 10 of 12"):
        assert text in md
    assert drift.summary_line(REP) == "1 resource(s) with changed settings, 1 deleted, code changed in orkestra-x-echo"
    other = copy.deepcopy(REP)
    other["settings"][0]["fields"][0]["aws"] = "60"
    assert drift.fingerprint(other) != drift.fingerprint(REP) == drift.fingerprint(copy.deepcopy(REP))


def test_keeping_the_changes_makes_a_change_request_and_a_ticket(client, captured):
    c, pid = _project("drift_keeper")
    aws_access.save(pid, REP, drift.DRIFT)
    asyncio.run(drift.keep(JobContext("job_keep", pid, {"feedback": "the 45 s timeout is intended"}, {}, None)))

    async def made():
        async with SessionLocal() as db:
            cr = (await db.execute(select(ChangeRequest).where(ChangeRequest.project_id == pid))).scalars().first()
        return cr, await tk.query(pid, assignee="de")
    cr, tickets = asyncio.run(made())
    assert cr.source == "drift" and cr.route == "infra" and "visibility_timeout_seconds" in cr.text and "45" in cr.text
    assert "deleted in AWS on purpose" in cr.text and "the 45 s timeout is intended" in cr.text
    assert [t.title for t in tickets] == ["Keep the console change to orkestra-x-echo's code"] and "+b" in tickets[0].description
    assert ("cto.review", {"cr_id": cr.id}) in captured
    assert aws_access.load(pid, drift.DRIFT)["status"] == "keeping"
