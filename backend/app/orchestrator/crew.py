"""The Orkestra crew: fixed metadata the UI and orchestrator share."""
from __future__ import annotations

CREW: list[dict] = [
    {"key": "cto", "persona": "Orion", "role": "CTO · Orchestrator", "accent": "violet",
     "model": "eu.anthropic.claude-opus-5-5"},
    {"key": "intake", "persona": "Echo", "role": "Business Analyst", "accent": "cyan",
     "model": "eu.anthropic.claude-opus-5-5"},
    {"key": "ba", "persona": "Atlas", "role": "Data Analyst", "accent": "emerald",
     "model": "eu.anthropic.claude-sonnet-5"},
    {"key": "ta", "persona": "Archie", "role": "Technical Architect", "accent": "amber",
     "model": "eu.anthropic.claude-opus-5-5"},
    {"key": "tp", "persona": "Terra", "role": "Platform Engineer", "accent": "orange",
     "model": "eu.anthropic.claude-sonnet-5"},
    {"key": "de", "persona": "Dev", "role": "Data Engineer", "accent": "blue",
     "model": "eu.anthropic.claude-sonnet-5"},
    {"key": "qa", "persona": "Quinn", "role": "QA Engineer", "accent": "rose",
     "model": "eu.anthropic.claude-sonnet-5"},
]
AGENT_KEYS = [a["key"] for a in CREW]
FLOW = AGENT_KEYS[1:]  # CTO supervises; these run in order
