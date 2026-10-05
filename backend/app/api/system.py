from __future__ import annotations

import asyncio
import time
from functools import lru_cache

import boto3
from fastapi import APIRouter, Depends

from app.core.config import get_settings
from app.core.security import current_user
from app.db.models import User
from app.orchestrator.crew import CREW

router = APIRouter(prefix="/api/system", tags=["system"])


@lru_cache
def _identity() -> dict:
    try:
        ident = boto3.client("sts", region_name=get_settings().aws_region).get_caller_identity()
        return {"account": ident["Account"], "arn": ident["Arn"], "ok": True}
    except Exception as exc:  # noqa: BLE001
        return {"account": get_settings().aws_account_id, "arn": None, "ok": False, "error": type(exc).__name__}


@lru_cache
def _build() -> str:
    """This release's frontend fingerprint (index.html names the hashed assets): an open tab compares it to offer a reload."""
    import hashlib

    idx = get_settings().frontend_dist / "index.html"
    return hashlib.sha1(idx.read_bytes()).hexdigest()[:12] if idx.is_file() else "dev"


@router.get("/health")
async def health():
    return {"status": "ok", "app": get_settings().app_name, "build": _build()}


@router.get("/info")
async def info(_: User = Depends(current_user)):
    s = get_settings()
    ident = await asyncio.to_thread(_identity)
    return {"environment": s.environment, "region": s.aws_region, "account": ident["account"],
            "identity_ok": ident["ok"], "role": (ident["arn"] or "").split("/")[1] if ident["arn"] else None,
            "crew": CREW}


@router.post("/bedrock-check")
async def bedrock_check(_: User = Depends(current_user)):
    """Tiny live call per distinct model — proves the host role can reach Claude."""

    def call(model: str) -> dict:
        t0 = time.perf_counter()
        try:
            r = boto3.client("bedrock-runtime", region_name=get_settings().aws_region).converse(
                modelId=model, messages=[{"role": "user", "content": [{"text": "Reply with just: OK"}]}],
                inferenceConfig={"maxTokens": 5})
            return {"model": model, "ok": True, "reply": r["output"]["message"]["content"][0]["text"],
                    "ms": int((time.perf_counter() - t0) * 1000)}
        except Exception as exc:  # noqa: BLE001
            return {"model": model, "ok": False, "error": str(exc)[:200]}

    models = sorted({a["model"] for a in CREW})
    return await asyncio.gather(*(asyncio.to_thread(call, m) for m in models))
