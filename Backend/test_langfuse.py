"""
PHASE 1 CHECK — is Langfuse wired up correctly?

Proves the whole chain end to end before any service is touched:
    .env  ->  settings  ->  os.environ  ->  Langfuse SDK  ->  a real traced LLM call

Makes NO database, Qdrant or file changes. It does make ONE small LLM call (a few tokens,
fractions of a cent) because a trace with real token counts and a real cost is the only way to
prove the integration actually works — an auth check alone would pass even if tracing were
silently disabled.

Usage (from Backend/):
    python test_langfuse.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.core.config import settings                       # noqa: E402
from app.core.observability import TRACING_ENABLED, flush  # noqa: E402  MUST precede langfuse.openai


def _masked(value: str) -> str:
    """Show enough of a key to confirm WHICH key is loaded, never enough to leak it."""
    if not value:
        return "(empty)"
    return f"{value[:8]}…{value[-4:]}" if len(value) > 14 else "(set)"


def main() -> None:
    print("=" * 68)
    print("PHASE 1 — Langfuse connection check")
    print("=" * 68)

    # ── 1. Did .env actually reach the Settings object? ──────────────────────
    # config.py sets extra="ignore", so a key that is not declared in Settings is dropped
    # silently. This is the step that catches that.
    print("\n1. Settings loaded from .env")
    print(f"   LANGFUSE_PUBLIC_KEY   {_masked(settings.LANGFUSE_PUBLIC_KEY)}")
    print(f"   LANGFUSE_SECRET_KEY   {_masked(settings.LANGFUSE_SECRET_KEY)}")
    print(f"   LANGFUSE_HOST         {settings.LANGFUSE_HOST}")
    print(f"   LANGFUSE_ENABLED      {settings.LANGFUSE_ENABLED}")

    if not TRACING_ENABLED:
        print("\n   RESULT: tracing is OFF — nothing will be sent.")
        print("   Set LANGFUSE_ENABLED=True and both keys in .env, then re-run.")
        raise SystemExit(1)

    # ── 2. Did observability.py export them to the environment? ──────────────
    print("\n2. Exported to the process environment (what the SDK reads)")
    for var in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_HOST", "LANGFUSE_TRACING_ENABLED"):
        print(f"   {var:<26} {'set' if os.environ.get(var) else 'MISSING'}")

    # ── 3. Do the credentials actually work? ─────────────────────────────────
    print("\n3. Authenticating with Langfuse")
    from langfuse import get_client

    client = get_client()
    if not client.auth_check():
        print("   FAILED — Langfuse rejected the credentials.")
        print("   Check the keys and that LANGFUSE_HOST matches the region the project")
        print("   lives in (cloud.langfuse.com vs us.cloud.langfuse.com).")
        raise SystemExit(1)
    print("   OK — credentials accepted.")

    # ── 4. One real traced call. ─────────────────────────────────────────────
    # Imported here, AFTER observability set the environment, and used instead of the plain
    # openai client — that single swap is the whole Phase 2 change, tried once here first.
    print("\n4. Making one traced LLM call")
    from langfuse.openai import OpenAI

    openai_client = OpenAI(api_key=settings.OPENAI_API_KEY, timeout=60.0)

    # No token cap is passed on purpose: gpt-5/o-series reject `max_tokens` and want
    # `max_completion_tokens`. Omitting it keeps this check model-agnostic.
    response = openai_client.chat.completions.create(
        model=settings.OPENAI_LLM_MODEL,
        messages=[{"role": "user", "content": "Reply with exactly: Langfuse is connected."}],
        name="phase1-connection-check",   # the label this call carries in the Langfuse UI
    )

    answer = (response.choices[0].message.content or "").strip()
    usage = response.usage
    print(f"   Model     {settings.OPENAI_LLM_MODEL}")
    print(f"   Reply     {answer!r}")
    if usage:
        print(f"   Tokens    in={usage.prompt_tokens} out={usage.completion_tokens} "
              f"total={usage.total_tokens}")

    # ── 5. Flush, or the process may exit before the batch is sent. ──────────
    print("\n5. Flushing to Langfuse")
    flush()
    print("   Sent.")

    print("\n" + "=" * 68)
    print("RESULT: PASSED")
    print("=" * 68)
    print(f"\nOpen {settings.LANGFUSE_HOST} and look for a trace named")
    print("'phase1-connection-check'. It should show the prompt, the reply,")
    print("the token counts above, a cost in dollars, and the latency.")
    print("\nIf the trace is there with a COST, Phase 1 is done and the same")
    print("client swap can be applied to the upload pipeline in Phase 2.")


if __name__ == "__main__":
    main()
