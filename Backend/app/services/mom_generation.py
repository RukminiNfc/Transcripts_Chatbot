"""Minutes of Meeting (MOM) generation from a grooming-call transcript.

ONE LLM call over the whole transcript, driven entirely by
`app/prompts/mom_generation_prompt.txt`. Change the document by editing that file, not this one.

Deliberately independent of the requirement pipeline: the prompt works from the raw conversation
alone, so a MOM is produced even for a call that extracted zero requirements, and nothing here can
be affected by extraction or comparison behaviour.

This service NEVER raises. Every failure comes back as `error` in the returned dict, because MOM
generation runs after a transcript has already been successfully processed — an exception escaping
here would flip a good ingestion to "failed".
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.observability import TracedOpenAI as OpenAI
from sqlalchemy import func as sa_func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.core.config import settings
from app.models.database import ConversationLog, Customer, MeetingMinutes, Transcript

logger = logging.getLogger(__name__)

# Prompt templates live alongside every other prompt in the project.
_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"
_MOM_PROMPT_FILE = "mom_generation_prompt.txt"

_TRANSCRIPT_PLACEHOLDER = "{{TRANSCRIPT_TEXT}}"
_CLIENT_NAME_PLACEHOLDER = "{{CLIENT_NAME}}"
_DATE_PLACEHOLDER = "{{DATE}}"
_GLOSSARY_PLACEHOLDER = "{{GLOSSARY}}"

# The SAME file the chat assistant uses. One list, so a term corrected for chat is corrected for
# minutes too — two lists would drift apart within a month.
_GLOSSARY_PATH = _PROMPTS_DIR / "glossary.txt"

# Same character budget the extraction service uses — ~3.5 chars/token, kept conservative so the
# instructions and the response still fit alongside the transcript.
_MAX_TRANSCRIPT_CHARS = 320_000

# MOMs are long by design: the prompt says "length is not a constraint; omission is a defect".
# Do NOT inherit the 6000-token answer cap from llm_service — that is sized for chat replies.
_MOM_MAX_TOKENS = 16000


class MOMGenerationService:
    """Generates a formal minutes/requirements document from a parsed transcript."""

    def __init__(self) -> None:
        # max_retries lets the SDK ride out transient 429s with backoff instead of losing the call.
        self.client = OpenAI(api_key=settings.OPENAI_API_KEY, max_retries=6, timeout=180.0)
        self.model: str = settings.OPENAI_MOM_MODEL
        self._mom_prompt_template: str = self._load_prompt(_MOM_PROMPT_FILE)

    # ── prompt loading ───────────────────────────────────────────────────────

    def _load_prompt(self, filename: str) -> str:
        prompt_path = _PROMPTS_DIR / filename
        if prompt_path.exists():
            return prompt_path.read_text(encoding="utf-8")
        logger.error(f"Prompt file {filename} not found!")
        return ""

    @staticmethod
    def _load_glossary() -> str:
        """
        Canonical spellings for terms the speech-to-text mangles ("Carl" for "CARV").

        Read on every call rather than cached at construction: this is the file that gets edited
        whenever a new mis-transcription shows up, and making that require a worker restart would
        mean it quietly stops being maintained.

        Missing or comment-only file returns "" — the prompt then simply has no terminology
        section, which is the behaviour before this existed.
        """
        try:
            if not _GLOSSARY_PATH.exists():
                return ""
            lines = [
                ln.strip() for ln in _GLOSSARY_PATH.read_text(encoding="utf-8").splitlines()
                if ln.strip() and not ln.strip().startswith("#")
            ]
            if not lines:
                return ""
            return (
                "PROJECT TERMINOLOGY (authoritative — these spellings OVERRIDE whatever the "
                "transcript contains, because the transcript is speech-to-text and mis-hears "
                "them; only expand an acronym if it is listed here):\n"
                + "\n".join(lines)
            )
        except Exception as exc:
            logger.error(f"Could not load glossary ({exc}); generating without it.")
            return ""

    # ── model-family handling (mirrors requirement_extraction.py) ────────────

    @staticmethod
    def _is_next_gen(model: str) -> bool:
        """GPT-5 / o-series use newer API conventions (max_completion_tokens, fixed temperature)."""
        return model.lower().startswith(("gpt-5", "o1", "o3", "o4"))

    def _chat(self, model, messages, max_tokens=None, temperature=None):
        """
        Call chat.completions with params appropriate to the model family.
        Next-gen models (gpt-5*, o*) want max_completion_tokens and reject a custom
        temperature. On a hard failure, fall back to gpt-4o so we never produce nothing.
        """
        def build(m, include_max=True):
            kw = {"model": m, "messages": messages}
            if self._is_next_gen(m):
                if include_max and max_tokens is not None:
                    kw["max_completion_tokens"] = max_tokens
                # next-gen models accept only the default temperature → do not send it
            else:
                if max_tokens is not None:
                    kw["max_tokens"] = max_tokens
                if temperature is not None:
                    kw["temperature"] = temperature
            return kw

        try:
            return self.client.chat.completions.create(**build(model)), model
        except TypeError as exc:
            # Old SDK doesn't know max_completion_tokens — keep the model, drop the cap.
            if "max_completion_tokens" in str(exc):
                logger.warning(f"SDK lacks max_completion_tokens; calling '{model}' without a token cap.")
                try:
                    return self.client.chat.completions.create(**build(model, include_max=False)), model
                except Exception as exc2:
                    logger.error(f"Model '{model}' failed without cap ({exc2}); falling back to gpt-4o.")
                    return self.client.chat.completions.create(**build("gpt-4o", include_max=False)), "gpt-4o"
            logger.error(f"Model '{model}' TypeError ({exc}); falling back to gpt-4o.")
            return self.client.chat.completions.create(**build("gpt-4o", include_max=False)), "gpt-4o"
        except Exception as exc:
            logger.error(f"Model '{model}' call failed ({exc}); falling back to gpt-4o.")
            return self.client.chat.completions.create(**build("gpt-4o", include_max=False)), "gpt-4o"

    # ── transcript formatting ────────────────────────────────────────────────

    @staticmethod
    def _blocks_to_text(blocks: list[dict[str, Any]]) -> str:
        """`Speaker [timestamp]: text`, one line per block — the format every prompt here expects."""
        return "\n".join(
            f"{b.get('speaker', '')} [{b.get('timestamp', '')}]: {b.get('text', '')}" for b in blocks
        )

    @staticmethod
    def _format_date(session_name: str, call_date: datetime | None) -> str:
        """
        Header date for the document. Includes the session name so an emailed MOM identifies
        which call it came from without needing the database.
        """
        if call_date:
            return f"{call_date.strftime('%Y-%m-%d')} ({session_name})" if session_name \
                else call_date.strftime("%Y-%m-%d")
        return session_name or "date not recorded"

    # ── the one public method ────────────────────────────────────────────────

    def generate_mom(
        self,
        blocks: list[dict[str, Any]],
        client_speaker_name: str,
        session_name: str,
        call_date: datetime | None = None,
    ) -> dict[str, Any]:
        """
        Generate the minutes for one call.

        Returns a dict rather than a bare string so callers can distinguish "worked",
        "worked but the model ran out of room", and "failed":

            content          the generated markdown ("" on failure)
            model_used       the model that actually answered (may differ after a fallback)
            input_truncated  the transcript was too long and was cut before sending
            output_truncated the model hit the token cap and the document stops mid-way
            error            why it failed, "" when it did not

        `output_truncated` matters: this prompt asks for exhaustive output, so hitting the cap
        is plausible on a long call and would otherwise be a silently incomplete document.
        """
        result: dict[str, Any] = {
            "content": "",
            "model_used": self.model,
            "input_truncated": False,
            "output_truncated": False,
            "error": "",
        }

        if not blocks:
            result["error"] = "No conversation blocks — nothing to generate minutes from."
            logger.warning(result["error"])
            return result

        if not self._mom_prompt_template:
            result["error"] = f"Prompt template {_MOM_PROMPT_FILE} is missing or empty."
            logger.error(result["error"])
            return result

        full_text = self._blocks_to_text(blocks)
        if len(full_text) > _MAX_TRANSCRIPT_CHARS:
            logger.warning(
                f"Transcript is large ({len(full_text)} chars); truncating to "
                f"{_MAX_TRANSCRIPT_CHARS} for MOM generation."
            )
            full_text = full_text[:_MAX_TRANSCRIPT_CHARS]
            result["input_truncated"] = True

        # The prompt embeds the transcript itself (inside <transcript> tags), so the whole thing
        # goes in as one message. That is the author's structure — keep it.
        prompt = (
            self._mom_prompt_template
            .replace(_CLIENT_NAME_PLACEHOLDER, client_speaker_name or "the client")
            .replace(_DATE_PLACEHOLDER, self._format_date(session_name, call_date))
            .replace(_GLOSSARY_PLACEHOLDER, self._load_glossary())
            .replace(_TRANSCRIPT_PLACEHOLDER, full_text)
        )

        try:
            resp, model_used = self._chat(
                self.model,
                [{"role": "user", "content": prompt}],
                max_tokens=_MOM_MAX_TOKENS,
                temperature=0.2,
            )
            result["model_used"] = model_used

            choice = resp.choices[0]
            content = choice.message.content or ""
            result["content"] = content

            if getattr(choice, "finish_reason", None) == "length":
                result["output_truncated"] = True
                logger.warning(
                    f"MOM for '{session_name}' hit the {_MOM_MAX_TOKENS}-token cap and is "
                    f"incomplete. Raise _MOM_MAX_TOKENS or split generation into sections."
                )

            if len(content.strip()) < 50:
                result["error"] = "Model returned little or no content."
                logger.warning(f"MOM for '{session_name}': {result['error']}")
            else:
                logger.info(
                    f"MOM for '{session_name}': {len(content)} chars via {model_used}"
                    + (" (TRUNCATED)" if result["output_truncated"] else "")
                )

        except Exception as exc:
            result["error"] = str(exc)
            logger.error(f"MOM generation failed for '{session_name}': {exc}")

        return result


# Module-level singleton — the prompt template is read once, matching how the other services are
# instantiated once per worker/API process.
mom_service = MOMGenerationService()


def _ts_to_seconds(ts: str | None) -> int:
    """'4:49' or '1:02:33' -> seconds, for ordering blocks as they were spoken."""
    if not ts:
        return 0
    try:
        parts = [int(p) for p in str(ts).split(":")]
    except ValueError:
        return 0
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + part
    return seconds


async def generate_and_store_mom(
    db: AsyncSession,
    transcript_id: uuid.UUID,
) -> MeetingMinutes | None:
    """
    Generate minutes for one processed transcript and store them as a new version.

    Shared by the API endpoint and the Celery task so there is exactly one code path.

    A row is ALWAYS written when the transcript exists — on failure it carries
    `generation_error` instead of content, so a failure is visible in the UI rather than silent.

    Returns None only when the transcript itself cannot be found or has no conversation rows.
    """
    transcript = (await db.execute(
        select(Transcript).filter(Transcript.id == transcript_id)
    )).scalars().first()
    if not transcript:
        logger.error(f"MOM: transcript {transcript_id} not found")
        return None

    logs = (await db.execute(
        select(ConversationLog).filter(ConversationLog.transcript_id == transcript_id)
    )).scalars().all()
    if not logs:
        logger.error(
            f"MOM: transcript '{transcript.session_name}' has no conversation_logs "
            f"(status={transcript.status}); nothing to generate from."
        )
        return None

    customer = (await db.execute(
        select(Customer).filter(Customer.id == transcript.customer_id)
    )).scalars().first()
    client_speaker_name = (customer.client_speaker_name if customer else "") or ""
    if not client_speaker_name:
        logger.warning(
            "MOM: no client_speaker_name on the customer record — the prompt cannot distinguish "
            "client requirements from developer status reports."
        )

    ordered = sorted(logs, key=lambda r: _ts_to_seconds(r.call_timestamp))
    blocks = [
        {"speaker": r.speaker, "timestamp": r.call_timestamp, "text": r.text, "role": r.role}
        for r in ordered
    ]

    # Next version for this transcript. Regeneration appends, never overwrites.
    current_max = (await db.execute(
        select(sa_func.max(MeetingMinutes.version))
        .filter(MeetingMinutes.transcript_id == transcript_id)
    )).scalar()
    next_version = (current_max or 0) + 1

    # The OpenAI call is synchronous; keep it off the event loop so an API request generating a
    # MOM does not stall every other request for the duration.
    result = await asyncio.to_thread(
        mom_service.generate_mom,
        blocks=blocks,
        client_speaker_name=client_speaker_name,
        session_name=transcript.session_name,
        call_date=transcript.call_date,
    )

    mom = MeetingMinutes(
        transcript_id=transcript_id,
        customer_id=transcript.customer_id,
        session_name=transcript.session_name,
        call_date=transcript.call_date,
        version=next_version,
        content_markdown=result["content"] or None,
        status="draft",
        model_used=result["model_used"],
        prompt_name=_MOM_PROMPT_FILE,
        truncated=bool(result["output_truncated"]),
        generation_error=result["error"] or None,
    )
    db.add(mom)
    await db.commit()
    await db.refresh(mom)

    if result["error"]:
        logger.error(f"MOM v{next_version} for '{transcript.session_name}' FAILED: {result['error']}")
    else:
        logger.info(
            f"MOM v{next_version} stored for '{transcript.session_name}' "
            f"({len(result['content'])} chars)"
            + (" [TRUNCATED]" if result["output_truncated"] else "")
        )
    return mom
