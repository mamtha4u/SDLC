"""Signs of life and help while an agent works (user, 10-05: "our CTO (for all) and TA (for Terra and Dev) need to
monitor all agents and ask them if all is OK; if there's no response, restart the agent so it comes out of the error
situation… self-healing, agents helping agents").

- `beat`: an agent is alive. Called on every streamed model event (llm.call) and around every tool it runs
  (base.run_loop), so silence is measured exactly: a healthy model call beats every second, a hung one never.
- `advise` / `take_advice`: notes from the agent's watcher (Archie for Terra and Dev) delivered INTO the agent's next
  step, while it works, so help arrives without stopping it.
In memory: the agents, the watcher (agents/watch.py) and this live in the one server process.
"""
from __future__ import annotations

import time

_beats: dict[tuple[str, str], dict] = {}
_advice: dict[tuple[str, str], list[dict]] = {}


def beat(project_id: str | None, agent: str, what: str = "model") -> None:
    if project_id:
        _beats[(project_id, agent)] = {"at": time.time(), "what": what}


def last(project_id: str, agent: str) -> dict | None:
    return _beats.get((project_id, agent))


def forget(project_id: str, agent: str) -> None:
    _beats.pop((project_id, agent), None)


def advise(project_id: str, agent: str, sender: str, text: str) -> None:
    _advice.setdefault((project_id, agent), []).append({"from": sender, "text": text, "at": time.time()})


def take_advice(project_id: str, agent: str) -> list[dict]:
    return _advice.pop((project_id, agent), [])
