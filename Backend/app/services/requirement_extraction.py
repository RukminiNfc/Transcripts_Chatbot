from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from openai import OpenAI

from app.core.config import settings

logger = logging.getLogger(__name__)

# Directory where all LLM prompt templates live
_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"

_CLIENT_NAME_PLACEHOLDER = "{{CLIENT_NAME}}"
_TOPIC_TITLE_PLACEHOLDER = "{{TOPIC_TITLE}}"
_TOPIC_DESC_PLACEHOLDER = "{{TOPIC_DESCRIPTION}}"
_EXISTING_CATEGORIES_PLACEHOLDER = "{{EXISTING_CATEGORIES}}"

# Rough character budget for a single LLM context. gpt-4o handles ~128k tokens;
# ~3.5 chars/token, kept conservative to leave room for the prompt + response.
_MAX_TRANSCRIPT_CHARS = 320_000


class RequirementExtractionService:
    """
    Extracts confirmed requirements from grooming-call transcript blocks using a Two-Step Chain-of-Thought approach.

    Step 1 (Reasoning): The LLM reads the transcript chunk and outputs its findings as plain text. This allows it to "think out loud" and capture complex sub-points without the constraint of JSON formatting.
    Step 2 (Formatting): The plain text findings are fed back to the LLM to strictly format them into the required JSON schema.
    """

    def __init__(self) -> None:
        # max_retries lets the SDK ride out transient 429 (rate-limit) responses with
        # backoff instead of dropping a call's results.
        self.client = OpenAI(api_key=settings.OPENAI_API_KEY, max_retries=6, timeout=180.0)
        self.model: str = settings.OPENAI_EXTRACTION_MODEL          # notes + review (strong)
        self.format_model: str = settings.OPENAI_FORMAT_MODEL       # notes → JSON (cheap)
        self.chunk_size: int = settings.EXTRACTION_CHUNK_SIZE
        self.chunk_overlap: int = settings.EXTRACTION_CHUNK_OVERLAP
        
        self._reasoning_prompt_template: str = self._load_prompt("requirement_extraction_reasoning_prompt.txt")
        self._formatting_prompt_template: str = self._load_prompt("requirement_extraction_formatting_prompt.txt")

        # Topic-based extraction (v2) prompts
        self._segmentation_prompt_template: str = self._load_prompt("topic_segmentation_prompt.txt")
        self._topic_extraction_prompt_template: str = self._load_prompt("requirement_extraction_topic_prompt.txt")
        self._critic_prompt_template: str = self._load_prompt("completeness_critic_prompt.txt")

        # Exhaustive extraction (v3) prompts: one full-transcript pass + self-check gap-fill
        self._exhaustive_prompt_template: str = self._load_prompt("requirement_extraction_exhaustive_prompt.txt")
        self._gapfill_prompt_template: str = self._load_prompt("requirement_extraction_gapfill_prompt.txt")

        # Notes-based extraction (v4) prompts: reason into notes, review for completeness, then format
        self._notes_prompt_template: str = self._load_prompt("requirement_notes_prompt.txt")
        self._notes_review_prompt_template: str = self._load_prompt("requirement_notes_review_prompt.txt")
        self._notes_to_json_prompt_template: str = self._load_prompt("requirement_notes_to_json_prompt.txt")

    @staticmethod
    def _is_next_gen(model: str) -> bool:
        """GPT-5 / o-series use newer API conventions (max_completion_tokens, fixed temperature)."""
        return model.lower().startswith(("gpt-5", "o1", "o3", "o4"))

    def _chat(self, model, messages, max_tokens=None, temperature=None, json_mode=False):
        """
        Call chat.completions with params appropriate to the model family.
        Next-gen models (gpt-5*, o*) want max_completion_tokens and reject a custom
        temperature. On a hard failure, fall back to gpt-4o so we never produce nothing.
        """
        def build(m, include_max=True):
            kw = {"model": m, "messages": messages}
            if json_mode:
                kw["response_format"] = {"type": "json_object"}
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
            return self.client.chat.completions.create(**build(model))
        except TypeError as exc:
            # Old SDK doesn't know max_completion_tokens — keep the model, drop the cap.
            if "max_completion_tokens" in str(exc):
                logger.warning(f"SDK lacks max_completion_tokens; calling '{model}' without a token cap.")
                try:
                    return self.client.chat.completions.create(**build(model, include_max=False))
                except Exception as exc2:
                    logger.error(f"Model '{model}' failed without cap ({exc2}); falling back to gpt-4o.")
                    return self.client.chat.completions.create(**build("gpt-4o", include_max=False))
            logger.error(f"Model '{model}' TypeError ({exc}); falling back to gpt-4o.")
            return self.client.chat.completions.create(**build("gpt-4o", include_max=False))
        except Exception as exc:
            logger.error(f"Model '{model}' call failed ({exc}); falling back to gpt-4o.")
            return self.client.chat.completions.create(**build("gpt-4o", include_max=False))

    def _load_prompt(self, filename: str) -> str:
        prompt_path = _PROMPTS_DIR / filename
        if prompt_path.exists():
            return prompt_path.read_text(encoding="utf-8")
        logger.error(f"Prompt file {filename} not found!")
        return "Extract requirements from the transcript."

    def _build_prompt(self, template: str, client_speaker_name: str) -> str:
        return template.replace(_CLIENT_NAME_PLACEHOLDER, client_speaker_name)

    @staticmethod
    def _blocks_to_text(blocks: list[dict[str, Any]]) -> str:
        return "\n".join(f"{b['speaker']} [{b['timestamp']}]: {b['text']}" for b in blocks)

    @staticmethod
    def _deduplicate(requirements: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[str] = set()
        unique: list[dict[str, Any]] = []

        for req in requirements:
            key = req.get("canonical_text") or req.get("requirement_text", "")
            key = key.strip().lower()

            if key and key not in seen:
                seen.add(key)
                unique.append(req)

        return unique

    def _extract_from_chunk(self, conversation_text: str, client_speaker_name: str) -> list[dict[str, Any]]:
        """Run the two-step extraction process on a single chunk."""
        
        # Step 1: Reasoning (Plain Text)
        reasoning_system_prompt = self._build_prompt(self._reasoning_prompt_template, client_speaker_name)
        try:
            logger.info("Starting Step 1: Reasoning extraction (Plain Text)")
            reasoning_resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": reasoning_system_prompt},
                    {"role": "user", "content": f"Analyze this transcript segment:\n\n{conversation_text}"},
                ],
                temperature=0.2,
            )
            reasoning_text = reasoning_resp.choices[0].message.content
            logger.debug(f"Reasoning output: {reasoning_text[:200]}...")
            
            if not reasoning_text or len(reasoning_text.strip()) < 10:
                return []
                
        except Exception as exc:
            logger.error(f"LLM Reasoning step failed: {exc}")
            return []

        # Step 2: Formatting (JSON)
        formatting_system_prompt = self._build_prompt(self._formatting_prompt_template, client_speaker_name)
        try:
            logger.info("Starting Step 2: Formatting reasoning to JSON")
            format_resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": formatting_system_prompt},
                    {"role": "user", "content": f"Convert these notes to JSON:\n\n{reasoning_text}"},
                ],
                temperature=0.1,
                response_format={"type": "json_object"},
            )
            data = json.loads(format_resp.choices[0].message.content)
            return data.get("requirements", [])
            
        except json.JSONDecodeError as exc:
            logger.error(f"JSON parse error in formatting response: {exc}")
            return []
        except Exception as exc:
            logger.error(f"LLM Formatting step failed: {exc}")
            return []

    def extract_requirements(self, blocks: list[dict[str, Any]], client_speaker_name: str) -> list[dict[str, Any]]:
        total_blocks = len(blocks)
        if total_blocks == 0:
            return []

        if total_blocks <= self.chunk_size:
            text = self._blocks_to_text(blocks)
            return self._extract_from_chunk(text, client_speaker_name)

        step = max(1, self.chunk_size - self.chunk_overlap)
        all_results: list[dict[str, Any]] = []
        
        for start in range(0, total_blocks, step):
            chunk = blocks[start : start + self.chunk_size]
            if not chunk:
                break

            text = self._blocks_to_text(chunk)
            chunk_results = self._extract_from_chunk(text, client_speaker_name)
            all_results.extend(chunk_results)

        return self._deduplicate(all_results)

    # ──────────────────────────────────────────────────────────────────────────
    # V2: Topic-based extraction (segment → extract per topic → completeness check)
    #
    # This path mirrors the manual process that produced comprehensive notes:
    # first list every topic in the whole transcript, then extract requirements
    # one focused topic at a time, then re-check for any topic we missed.
    # It exists alongside the legacy block-chunked extract_requirements() above;
    # callers opt in explicitly.
    # ──────────────────────────────────────────────────────────────────────────

    def extract_requirements_topic_based(
        self, blocks: list[dict[str, Any]], client_speaker_name: str
    ) -> list[dict[str, Any]]:
        """
        High-recall extraction:
          Pass A — segment the full transcript into an exhaustive list of topics.
          Pass B — for each topic, extract only that topic's requirements.
          Pass C — completeness critic finds uncovered topics; re-extract those.

        Falls back to the legacy chunked extractor if segmentation yields nothing.
        """
        if not blocks:
            return []

        full_text = self._blocks_to_text(blocks)
        if len(full_text) > _MAX_TRANSCRIPT_CHARS:
            logger.warning(
                f"Transcript is large ({len(full_text)} chars); truncating to "
                f"{_MAX_TRANSCRIPT_CHARS} for topic-based extraction."
            )
            full_text = full_text[:_MAX_TRANSCRIPT_CHARS]

        topics = self._segment_topics(full_text, client_speaker_name)
        if not topics:
            logger.warning("Topic segmentation returned no topics; falling back to legacy extraction.")
            return self.extract_requirements(blocks, client_speaker_name)

        logger.info(f"Segmented transcript into {len(topics)} topics")

        all_results: list[dict[str, Any]] = []
        covered_titles: list[str] = []
        for topic in topics:
            reqs = self._extract_for_topic(full_text, topic, client_speaker_name)
            if reqs:
                covered_titles.append(topic.get("title", ""))
            all_results.extend(reqs)

        # Pass C — completeness critic
        uncovered = self._find_uncovered_topics(topics, all_results, client_speaker_name)
        if uncovered:
            logger.info(f"Completeness critic flagged {len(uncovered)} uncovered topics; re-extracting")
            by_title = {t.get("title", ""): t for t in topics}
            for title in uncovered:
                topic = by_title.get(title)
                if topic:
                    reqs = self._extract_for_topic(full_text, topic, client_speaker_name)
                    all_results.extend(reqs)

        deduped = self._deduplicate(all_results)
        logger.info(
            f"Topic-based extraction produced {len(all_results)} raw → {len(deduped)} deduplicated requirements"
        )
        return deduped

    def _segment_topics(self, full_text: str, client_speaker_name: str) -> list[dict[str, Any]]:
        """Pass A — ask the LLM for an exhaustive list of discussed topics."""
        system_prompt = self._build_prompt(self._segmentation_prompt_template, client_speaker_name)
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Transcript:\n\n{full_text}"},
                ],
                temperature=0.2,
                response_format={"type": "json_object"},
            )
            data = json.loads(resp.choices[0].message.content)
            topics = data.get("topics", [])
            # Keep only well-formed topics with a title
            return [t for t in topics if isinstance(t, dict) and t.get("title")]
        except json.JSONDecodeError as exc:
            logger.error(f"JSON parse error in topic segmentation: {exc}")
            return []
        except Exception as exc:
            logger.error(f"Topic segmentation step failed: {exc}")
            return []

    def _extract_for_topic(
        self, full_text: str, topic: dict[str, Any], client_speaker_name: str
    ) -> list[dict[str, Any]]:
        """Pass B — extract requirements for a single topic from the full transcript."""
        system_prompt = (
            self._build_prompt(self._topic_extraction_prompt_template, client_speaker_name)
            .replace(_TOPIC_TITLE_PLACEHOLDER, str(topic.get("title", "")))
            .replace(_TOPIC_DESC_PLACEHOLDER, str(topic.get("description", "")))
        )
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Transcript:\n\n{full_text}"},
                ],
                temperature=0.1,
                response_format={"type": "json_object"},
            )
            data = json.loads(resp.choices[0].message.content)
            return data.get("requirements", [])
        except json.JSONDecodeError as exc:
            logger.error(f"JSON parse error extracting topic '{topic.get('title')}': {exc}")
            return []
        except Exception as exc:
            logger.error(f"Extraction failed for topic '{topic.get('title')}': {exc}")
            return []

    def _find_uncovered_topics(
        self,
        topics: list[dict[str, Any]],
        extracted: list[dict[str, Any]],
        client_speaker_name: str,
    ) -> list[str]:
        """Pass C — ask the LLM which topics have no matching extracted requirement."""
        if not topics:
            return []

        topic_lines = "\n".join(
            f"- {t.get('title', '')}: {t.get('description', '')}" for t in topics
        )
        req_lines = "\n".join(
            f"- [{r.get('category', '')}/{r.get('sub_category', '')}] {r.get('requirement_text', '')}"
            for r in extracted
        ) or "(none extracted yet)"

        system_prompt = self._build_prompt(self._critic_prompt_template, client_speaker_name)
        user_content = (
            f"TOPICS:\n{topic_lines}\n\nEXTRACTED REQUIREMENTS:\n{req_lines}"
        )
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                temperature=0.0,
                response_format={"type": "json_object"},
            )
            data = json.loads(resp.choices[0].message.content)
            uncovered = data.get("uncovered", [])
            return [str(t) for t in uncovered if t]
        except json.JSONDecodeError as exc:
            logger.error(f"JSON parse error in completeness critic: {exc}")
            return []
        except Exception as exc:
            logger.error(f"Completeness critic step failed: {exc}")
            return []

    # ──────────────────────────────────────────────────────────────────────────
    # V3: Exhaustive extraction — one full-transcript pass + self-check gap-fill loop
    #
    # The transcript fits in a single context, so we do NOT chunk. Instead we:
    #   Pass 1 — extract everything from the whole transcript in one call.
    #   Pass 2..N — re-read the whole transcript WITH the current list and ask
    #              "what did you miss?", adding only genuinely new items, until a
    #              round finds nothing new (loop-until-dry) or the cap is reached.
    # This is high-recall like topic-based extraction but uses far fewer calls,
    # so it is faster, cheaper, and avoids rate-limit bursts.
    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _dedup_key(req: dict[str, Any]) -> str:
        key = req.get("canonical_text") or req.get("requirement_text", "")
        return key.strip().lower()

    def extract_requirements_exhaustive(
        self,
        blocks: list[dict[str, Any]],
        client_speaker_name: str,
        max_gap_fill_rounds: int = 3,
    ) -> list[dict[str, Any]]:
        """
        Extract every confirmed requirement using a whole-transcript pass followed by
        self-check gap-fill rounds. Returns the deduplicated requirement list.
        """
        if not blocks:
            return []

        full_text = self._blocks_to_text(blocks)
        if len(full_text) > _MAX_TRANSCRIPT_CHARS:
            logger.warning(
                f"Transcript is large ({len(full_text)} chars); truncating to "
                f"{_MAX_TRANSCRIPT_CHARS} for exhaustive extraction."
            )
            full_text = full_text[:_MAX_TRANSCRIPT_CHARS]

        # Pass 1 — exhaustive first pass over the whole transcript
        results = self._exhaustive_first_pass(full_text, client_speaker_name)
        logger.info(f"Exhaustive pass 1 produced {len(results)} requirements")

        seen: set[str] = {self._dedup_key(r) for r in results if self._dedup_key(r)}

        # Pass 2..N — gap-fill until a round finds nothing new
        for round_num in range(1, max_gap_fill_rounds + 1):
            missing = self._gap_fill_pass(full_text, results, client_speaker_name)

            new_items: list[dict[str, Any]] = []
            for item in missing:
                key = self._dedup_key(item)
                if key and key not in seen:
                    seen.add(key)
                    new_items.append(item)

            if not new_items:
                logger.info(f"Gap-fill round {round_num}: no new requirements; stopping")
                break

            logger.info(f"Gap-fill round {round_num}: +{len(new_items)} new requirements")
            results.extend(new_items)

        deduped = self._deduplicate(results)
        logger.info(f"Exhaustive extraction final count: {len(deduped)} requirements")
        return deduped

    def _exhaustive_first_pass(self, full_text: str, client_speaker_name: str) -> list[dict[str, Any]]:
        system_prompt = self._build_prompt(self._exhaustive_prompt_template, client_speaker_name)
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Transcript:\n\n{full_text}"},
                ],
                temperature=0.1,
                response_format={"type": "json_object"},
            )
            data = json.loads(resp.choices[0].message.content)
            return data.get("requirements", [])
        except json.JSONDecodeError as exc:
            logger.error(f"JSON parse error in exhaustive first pass: {exc}")
            return []
        except Exception as exc:
            logger.error(f"Exhaustive first pass failed: {exc}")
            return []

    def _gap_fill_pass(
        self,
        full_text: str,
        extracted: list[dict[str, Any]],
        client_speaker_name: str,
    ) -> list[dict[str, Any]]:
        system_prompt = self._build_prompt(self._gapfill_prompt_template, client_speaker_name)
        existing = "\n".join(
            f"- [{r.get('category', '')}/{r.get('sub_category', '')}] {r.get('requirement_text', '')}"
            for r in extracted
        ) or "(nothing extracted yet)"
        user_content = (
            f"ALREADY-EXTRACTED REQUIREMENTS:\n{existing}\n\n"
            f"TRANSCRIPT:\n\n{full_text}"
        )
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                temperature=0.1,
                response_format={"type": "json_object"},
            )
            data = json.loads(resp.choices[0].message.content)
            return data.get("requirements", [])
        except json.JSONDecodeError as exc:
            logger.error(f"JSON parse error in gap-fill pass: {exc}")
            return []
        except Exception as exc:
            logger.error(f"Gap-fill pass failed: {exc}")
            return []

    # ──────────────────────────────────────────────────────────────────────────
    # V4: Notes-based extraction — reason into structured notes, then format to JSON
    #
    # Mirrors how a human analyst produces good notes: ONE coherent reasoning pass
    # over the whole transcript (where intent is understood and duplicates merge
    # naturally), then a MECHANICAL conversion of those notes into JSON rows.
    # No chunking, no multi-pass re-derivation, no code-based string dedup.
    # ──────────────────────────────────────────────────────────────────────────

    def extract_requirements_notes_based(
        self,
        blocks: list[dict[str, Any]],
        client_speaker_name: str,
        existing_categories: list[str] | None = None,
        run_review: bool = True,
    ) -> list[dict[str, Any]]:
        """
        Notes-based extraction over the whole transcript:
          Pass 1 — write structured analyst notes (reasoning, plain text).
          Pass 2 — (optional) completeness review that folds missed points into the notes.
          Pass 3 — convert the notes into JSON requirement rows (mechanical).
        run_review: when False, skip the completeness review (just notes → format). With
        max_tokens set, the single notes pass is usually complete, and skipping review
        avoids the duplicate/catch-all rows it tends to introduce — and is faster.
        existing_categories: category names already used for this project, so the
        formatter can reuse them and keep categories consistent across uploads.
        """
        if not blocks:
            return []

        full_text = self._blocks_to_text(blocks)
        if len(full_text) > _MAX_TRANSCRIPT_CHARS:
            logger.warning(
                f"Transcript is large ({len(full_text)} chars); truncating to "
                f"{_MAX_TRANSCRIPT_CHARS} for notes-based extraction."
            )
            full_text = full_text[:_MAX_TRANSCRIPT_CHARS]

        # Pass 1 — reasoning into structured notes
        notes = self._generate_notes(full_text, client_speaker_name)
        if not notes or len(notes.strip()) < 20:
            logger.warning("Notes pass produced little/no output; returning no requirements.")
            return []
        logger.info(f"Notes pass produced {len(notes)} chars of structured notes")

        # Pass 2 — completeness review (optional): fold any missed points back into the
        # SAME notes. Skipped when run_review is False — with max_tokens set the single
        # notes pass is usually complete, and skipping avoids the duplicate/catch-all rows
        # the review tends to add.
        if run_review:
            notes = self._review_and_augment_notes(full_text, notes, client_speaker_name)
            logger.info(f"After completeness review: {len(notes)} chars of notes")
        else:
            logger.info("Completeness review skipped (run_review=False)")

        # Pass 3 — mechanical conversion of notes → JSON rows
        requirements = self._notes_to_json(notes, client_speaker_name, existing_categories)
        logger.info(f"Notes-based extraction produced {len(requirements)} requirements")
        return requirements

    def _generate_notes(self, full_text: str, client_speaker_name: str) -> str:
        """Pass 1 — produce structured analyst notes (plain text) from the transcript."""
        system_prompt = self._build_prompt(self._notes_prompt_template, client_speaker_name)
        try:
            resp = self._chat(
                self.model,
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Transcript:\n\n{full_text}"},
                ],
                max_tokens=16000,  # avoid silent truncation of long notes on large calls
                temperature=0.2,
            )
            return resp.choices[0].message.content or ""
        except Exception as exc:
            logger.error(f"Notes (reasoning) pass failed: {exc}")
            return ""

    def _review_and_augment_notes(self, full_text: str, notes: str, client_speaker_name: str) -> str:
        """
        Pass 2 — re-read the transcript, find points missing from the notes, and return the
        COMPLETE updated notes (existing + missed). On any failure, return the original notes
        unchanged so we never lose what pass 1 already found.
        """
        system_prompt = self._build_prompt(self._notes_review_prompt_template, client_speaker_name)
        user_content = f"EXISTING NOTES:\n\n{notes}\n\n================\n\nTRANSCRIPT:\n\n{full_text}"
        try:
            resp = self._chat(
                self.model,
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                max_tokens=16000,  # avoid silent truncation of the complete notes on large calls
                temperature=0.2,
            )
            augmented = resp.choices[0].message.content or ""
            # Guard: if the review came back suspiciously short, keep the original notes.
            if len(augmented.strip()) < len(notes.strip()) * 0.6:
                logger.warning("Completeness review returned much shorter notes; keeping original.")
                return notes
            return augmented
        except Exception as exc:
            logger.error(f"Notes completeness review failed: {exc}; keeping original notes.")
            return notes

    def _notes_to_json(
        self,
        notes: str,
        client_speaker_name: str,
        existing_categories: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Pass 2 — convert the structured notes into JSON requirement rows."""
        system_prompt = self._build_prompt(self._notes_to_json_prompt_template, client_speaker_name)
        cats = [c for c in (existing_categories or []) if c]
        cats_text = (
            "\n".join(f"- {c}" for c in sorted(set(cats)))
            if cats
            else "(none yet — this is the first upload for this project; choose clear, reusable category names)"
        )
        system_prompt = system_prompt.replace(_EXISTING_CATEGORIES_PLACEHOLDER, cats_text)
        try:
            resp = self._chat(
                self.format_model,
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Notes:\n\n{notes}"},
                ],
                max_tokens=16000,  # large JSON array must not truncate (drops end-of-call items)
                temperature=0.1,
                json_mode=True,
            )
            raw = resp.choices[0].message.content or ""
            try:
                return json.loads(raw).get("requirements", [])
            except json.JSONDecodeError as exc:
                # The reply was cut off mid-JSON (a very large call). Rather than lose EVERYTHING
                # (return 0), salvage every COMPLETE requirement object from the partial array
                # and warn loudly so a truncated call is visible, never silent.
                logger.error(f"JSON parse error converting notes to JSON: {exc}")
                salvaged = self._salvage_requirements(raw)
                if salvaged:
                    logger.warning(
                        f"Salvaged {len(salvaged)} requirements from a TRUNCATED JSON response "
                        f"(call likely too large for one reply — verify completeness)."
                    )
                else:
                    logger.error("Could not salvage any requirements from the truncated JSON.")
                return salvaged
        except Exception as exc:
            logger.error(f"Notes-to-JSON pass failed: {exc}")
            return []

    def _salvage_requirements(self, raw: str) -> list[dict[str, Any]]:
        """Recover complete requirement objects from a truncated/invalid JSON reply.

        Scans the "requirements" array and keeps every WHOLE {...} object, dropping only the
        cut-off final one. Respects strings/escapes so braces inside the text can't confuse it.
        """
        start = raw.find('"requirements"')
        bracket = raw.find('[', start) if start != -1 else -1
        if bracket == -1:
            return []
        items: list[dict[str, Any]] = []
        depth = 0
        obj_start = None
        in_str = False
        escape = False
        for i in range(bracket + 1, len(raw)):
            ch = raw[i]
            if in_str:
                if escape:
                    escape = False
                elif ch == '\\':
                    escape = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == '{':
                if depth == 0:
                    obj_start = i
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0 and obj_start is not None:
                    try:
                        items.append(json.loads(raw[obj_start:i + 1]))
                    except json.JSONDecodeError:
                        pass  # a partial object — skip it
                    obj_start = None
            elif ch == ']' and depth == 0:
                break  # end of the requirements array
        return items
