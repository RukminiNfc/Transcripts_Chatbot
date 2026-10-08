"""Langfuse wiring — makes the .env settings visible to the Langfuse SDK.

WHY THIS FILE EXISTS
--------------------
The Langfuse SDK reads its credentials from the PROCESS ENVIRONMENT. This project loads .env
through pydantic-settings, which populates `settings` but does NOT export anything into
os.environ. Without this bridge the keys look correctly configured and tracing silently does
nothing — the worst kind of failure, because it looks like it worked.

HOW SERVICES USE IT
-------------------
Import the client from here instead of from `openai`:

    from app.core.observability import TracedOpenAI as OpenAI

That is the ONLY change a service needs — the constructor and every call site stay exactly as
they were. Doing the swap in this one module also removes an ordering trap: `langfuse.openai`
reads the environment when it is imported, so it must never be imported before the variables
below are set. Keeping that import in here makes the correct order structural instead of
something four separate files have to remember.

When tracing is off, this exports the plain OpenAI client, so Langfuse is not merely quiet —
it is not in the call path at all.

SCOPE — READ THIS BEFORE ASSUMING A SERVICE IS UNTRACED
-------------------------------------------------------
`langfuse.openai` does not subclass anything. It patches the openai SDK's methods in place
(wrapt), so importing it once traces EVERY OpenAI call in the process — including services
that still import the client straight from `openai`. Chat and extraction are therefore traced
too, not only the services that import from here.

The practical consequences:
  * tracing cannot be limited to one service by choosing imports; it is all or nothing per
    process, controlled by LANGFUSE_ENABLED
  * importing this module from the services on the hot path is what guarantees the patch is
    installed before any client is built

SAFETY
------
Tracing is read-only and batched on a background thread. An unreachable or misconfigured
Langfuse costs a trace, never a transcript.
"""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Any, Iterator

from app.core.config import settings

logger = logging.getLogger(__name__)

# Tracing needs BOTH keys and the explicit toggle. Any missing piece means off, so a half
# configured environment degrades to "no tracing" rather than to errors at every LLM call.
TRACING_ENABLED: bool = bool(
    settings.LANGFUSE_ENABLED
    and settings.LANGFUSE_PUBLIC_KEY
    and settings.LANGFUSE_SECRET_KEY
)

if TRACING_ENABLED:
    os.environ["LANGFUSE_PUBLIC_KEY"] = settings.LANGFUSE_PUBLIC_KEY
    os.environ["LANGFUSE_SECRET_KEY"] = settings.LANGFUSE_SECRET_KEY
    os.environ["LANGFUSE_HOST"] = settings.LANGFUSE_HOST
    os.environ["LANGFUSE_TRACING_ENABLED"] = "true"
    # Separates local experiments from server traffic in the Langfuse UI.
    os.environ.setdefault("LANGFUSE_TRACING_ENVIRONMENT", settings.APP_ENV)
else:
    # Tells the SDK to no-op rather than warn on every call.
    os.environ["LANGFUSE_TRACING_ENABLED"] = "false"
    logger.info(
        "Langfuse tracing is OFF (LANGFUSE_ENABLED=False or keys missing). "
        "LLM calls run exactly as before, untraced."
    )

# The client every service should use. Imported AFTER the environment is set above, which is
# the whole reason this indirection exists. Drop-in: same constructor, same methods, same
# responses — it only records what passes through.
if TRACING_ENABLED:
    from langfuse.openai import OpenAI as TracedOpenAI
else:
    from openai import OpenAI as TracedOpenAI


class _Span:
    """
    A span usable with BOTH `with` and `async with`.

    Both protocols are supported because the pipeline opens its session and its span together:

        async with AsyncSessionLocal() as db, trace_span("transcript_upload", ...):

    A combined `async with` requires every item to implement `__aenter__`/`__aexit__`, so a
    plain sync context manager raises TypeError there. Supporting both keeps that one-line form
    working and avoids re-indenting the pipeline body just to add tracing.

    `__exit__` always returns False, so an exception raised inside the block ALWAYS propagates.
    Suppressing one would turn a failed transcript into a silently successful one — far worse
    than losing a trace.
    """

    __slots__ = ("_name", "_metadata", "_cm")

    def __init__(self, name: str, metadata: dict[str, Any]) -> None:
        self._name = name
        self._metadata = metadata
        self._cm: Any = None

    def _start(self) -> Any:
        if not TRACING_ENABLED:
            return None
        try:
            from langfuse import get_client

            clean = {k: v for k, v in self._metadata.items() if v is not None}
            self._cm = get_client().start_as_current_observation(
                name=self._name,
                metadata=clean or None,
            )
            return self._cm.__enter__()
        except Exception as exc:
            logger.warning(
                f"Langfuse span {self._name!r} could not start ({exc}); continuing untraced."
            )
            self._cm = None
            return None

    def _stop(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        if self._cm is not None:
            try:
                self._cm.__exit__(exc_type, exc, tb)
            except Exception as close_exc:
                logger.warning(f"Langfuse span {self._name!r} failed to close ({close_exc}).")
            finally:
                self._cm = None
        return False   # never suppress

    def __enter__(self) -> Any:
        return self._start()

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return self._stop(exc_type, exc, tb)

    async def __aenter__(self) -> Any:
        return self._start()

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return self._stop(exc_type, exc, tb)


def trace_span(name: str, **metadata: Any) -> _Span:
    """
    Group everything that happens inside into one named step.

        with trace_span("extract", blocks=len(blocks)):
            ...                       # every LLM call in here nests under "extract"

    Nesting is automatic: Langfuse tracks the open span in a context variable, and the patched
    OpenAI client attaches each call to whatever is current. Nothing has to be passed down, so
    the services stay untouched. This also survives `asyncio.to_thread`, which is what makes the
    comparison service's parallel matching nest correctly rather than orphaning ~80 calls.

    Works with `with` and `async with`. A no-op when tracing is off — the block stays, costs
    nothing, and can be deleted whenever you want the instrumentation gone.
    """
    return _Span(name, metadata)


def annotate(**metadata: Any) -> None:
    """
    Attach facts to the span currently open — counts and outcomes only known part-way through,
    such as how many requirements were extracted. No-op when tracing is off; never raises.
    """
    if not TRACING_ENABLED:
        return
    try:
        from langfuse import get_client

        clean = {k: v for k, v in metadata.items() if v is not None}
        if clean:
            get_client().update_current_span(metadata=clean)
    except Exception as exc:
        logger.warning(f"Langfuse annotate failed ({exc}); continuing.")


def flush() -> None:
    """
    Send anything still buffered.

    Call this before a process or event loop ends. Langfuse batches on a background thread, so
    a Celery task that finishes and tears down its asyncio loop can otherwise drop the trace it
    just produced — the same class of problem `engine.dispose()` solves for DB connections.

    Never raises: losing a trace must not fail the work that produced it.
    """
    if not TRACING_ENABLED:
        return
    try:
        from langfuse import get_client

        get_client().flush()
    except Exception as exc:
        logger.warning(f"Langfuse flush failed ({exc}); continuing.")
