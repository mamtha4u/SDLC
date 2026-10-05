"""Probe how often EU cross-region routing lands in a region without model access. Not collected by pytest."""
import asyncio

from anthropic import AsyncAnthropicBedrock, PermissionDeniedError


async def one(c, model, detail):
    try:
        await c.messages.create(model=model, max_tokens=5, messages=[{"role": "user", "content": "Reply OK"}])
        return "ok"
    except PermissionDeniedError as e:
        if detail:
            print("  detail:", str(e)[:400])
        return "403"
    except Exception as e:  # noqa: BLE001
        return f"{type(e).__name__}: {str(e)[:120]}"


async def main():
    c = AsyncAnthropicBedrock(aws_region="eu-west-1", max_retries=0)
    for model in ["eu.anthropic.claude-opus-5-5", "eu.anthropic.claude-sonnet-5", "eu.anthropic.claude-haiku-4-5-20251001-v1:0"]:
        res = [await one(c, model, i == 0) for i in range(4)]
        print(model, res)


asyncio.run(main())
