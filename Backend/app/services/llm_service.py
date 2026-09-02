from openai import OpenAI, AsyncOpenAI
from app.core.config import settings
from typing import List, Dict, Optional
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

# Optional per-project glossary of canonical terms / acronym meanings the transcripts never
# spell out (e.g. "EWA = Employee Web Access"). Editable data file — an empty/missing file
# means no glossary is applied and the assistant stays fully generic.
_GLOSSARY_PATH = Path(__file__).parent.parent / "prompts" / "glossary.txt"

class LLMService:
    """LLM service for answer generation"""
    
    def __init__(self):
        self.client = OpenAI(api_key=settings.OPENAI_API_KEY)
        self.aclient = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)  # for streaming (non-blocking)
        self.model = settings.OPENAI_LLM_MODEL

    @staticmethod
    def _wants_attribution(query: str) -> bool:
        """True ONLY when the user asks WHO proposed/raised/suggested/confirmed/approved a
        requirement. Attribution (Proposed by / Confirmed by) is then included in the context;
        otherwise it is omitted so it never clutters a normal answer."""
        q = (query or "").lower()
        triggers = (
            "propose", "proposed", "raise", "raised", "suggest", "suggested",
            "confirm", "confirmed", "approve", "approved", "requested by", "added by",
            "who added", "who requested", "proposed by", "confirmed by",
        )
        return any(t in q for t in triggers)

    def _build_messages(self, query, search_results, conversation_history,
                        customer_metadata, has_context, transcript_map, dialogue_context=None):
        """Assemble the chat messages once, shared by the streaming and non-streaming paths."""
        context = self._build_context(search_results, self._wants_attribution(query))
        system_prompt = self._get_system_prompt(
            customer_metadata, has_context=has_context, transcript_map=transcript_map
        )
        messages = [{"role": "system", "content": system_prompt}]
        if conversation_history:
            for msg in conversation_history[-5:]:  # last 5 turns for continuity
                messages.append({"role": msg["role"], "content": msg["content"]})

        # For discussion / who-does-what / why questions, this is the contiguous conversation
        # (in order, with each speaker's name and role) — the reliable source for attributing
        # owners and decisions. 'client' = decision-maker; 'team' = the people who do the work.
        dialogue_block = ""
        if dialogue_context:
            dialogue_block = (
                "\n\nRECORDED CONVERSATION (chronological — each line is "
                "'[role] Speaker (time): text'). Use this to work out who said, committed to, or "
                "was asked to do something, and who decided. 'client' = decision-maker; 'team' = "
                "the people who do the work:\n"
                f"{dialogue_context}\n"
            )

        user_message = (
            f"Context from transcripts and requirements:\n{context}\n"
            f"{dialogue_block}\n"
            f"User Question: {query}\n\n"
            "Please provide a clear, accurate answer based on the context above."
        )
        messages.append({"role": "user", "content": user_message})
        return messages

    def _answer_call_kwargs(self, messages):
        """Model-appropriate kwargs (gpt-5/o-series use max_completion_tokens, no temperature)."""
        is_next_gen = self.model.lower().startswith(("gpt-5", "o1", "o3", "o4"))
        kw = {"model": self.model, "messages": messages}
        if is_next_gen:
            kw["max_completion_tokens"] = 6000
        else:
            kw["temperature"] = 0.5
            kw["max_tokens"] = 4000
        return kw

    async def generate_answer_stream(
        self,
        query: str,
        search_results: List[Dict],
        conversation_history: List[Dict] = None,
        customer_metadata: Dict = None,
        has_context: bool = True,
        transcript_map: str = None,
        dialogue_context: str = None,
    ):
        """Async generator yielding answer text deltas as they arrive (for SSE streaming).

        Uses AsyncOpenAI so it never blocks the event loop. On any failure it yields a single
        apology string so the client still gets a coherent message.
        """
        try:
            messages = self._build_messages(
                query, search_results, conversation_history, customer_metadata, has_context,
                transcript_map, dialogue_context
            )
            call_kwargs = self._answer_call_kwargs(messages)
            call_kwargs["stream"] = True
            try:
                stream = await self.aclient.chat.completions.create(**call_kwargs)
            except TypeError:
                # SDK without max_completion_tokens — retry the same model without the cap.
                call_kwargs.pop("max_completion_tokens", None)
                call_kwargs.pop("max_tokens", None)
                stream = await self.aclient.chat.completions.create(**call_kwargs)

            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta
        except Exception as e:
            logger.error(f"Error streaming answer: {e}")
            yield "I apologize, but I encountered an error generating a response. Please try again."
    
    def generate_answer(
        self,
        query: str,
        search_results: List[Dict],
        conversation_history: List[Dict] = None,
        customer_metadata: Dict = None,
        has_context: bool = True,
        transcript_map: str = None,
        dialogue_context: str = None
    ) -> str:
        """
        Generate answer using LLM with search results as context
        
        Args:
            query: User query
            search_results: Relevant chunks from vector search
            conversation_history: Previous messages
            customer_metadata: Dict containing project and client details
            
        Returns:
            Generated answer
        """
        try:
            messages = self._build_messages(
                query, search_results, conversation_history, customer_metadata, has_context,
                transcript_map, dialogue_context
            )
            call_kwargs = self._answer_call_kwargs(messages)

            try:
                response = self.client.chat.completions.create(**call_kwargs)
            except TypeError as te:
                # Old openai SDK doesn't know 'max_completion_tokens' — retry the SAME model
                # without a token cap (keeps gpt-5.4-mini instead of dropping to gpt-4o).
                logger.warning(f"SDK lacks token param ({te}); retrying '{self.model}' without it.")
                call_kwargs.pop("max_completion_tokens", None)
                call_kwargs.pop("max_tokens", None)
                response = self.client.chat.completions.create(**call_kwargs)
            except Exception:
                # Any other failure: fall back to gpt-4o so the user still gets an answer.
                logger.warning(f"Model '{self.model}' failed; falling back to gpt-4o")
                response = self.client.chat.completions.create(
                    model="gpt-4o",
                    messages=messages,
                    temperature=0.5,
                    max_tokens=4000,
                )
            
            answer = response.choices[0].message.content
            return answer
            
        except Exception as e:
            logger.error(f"Error generating answer: {e}")
            return "I apologize, but I encountered an error generating a response. Please try again."

    def _transform_messages(self, instruction: str, previous_answer: str):
        """Messages for reformatting the assistant's OWN previous answer (summarize/shorten/etc.).
        No retrieval — works ONLY from the given previous answer, so it condenses exactly what the
        user saw and cannot drift to a different topic."""
        system_prompt = (
            "You reformat the assistant's PREVIOUS answer according to the user's instruction "
            "(e.g. summarize, shorten, simplify, key points, bullet points, in short, TL;DR). "
            "Work ONLY from the PREVIOUS ANSWER text provided below — do NOT add new facts, do NOT "
            "use outside knowledge, and do NOT invent details. Stay faithful to the previous answer. "
            "Output clean markdown: sparse **bold** (a few key terms only), tight bullets, no fluff."
        )
        user_content = (
            f"USER INSTRUCTION:\n{instruction}\n\n"
            f"PREVIOUS ANSWER TO TRANSFORM:\n{previous_answer}"
        )
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

    def transform_previous_answer(self, instruction: str, previous_answer: str) -> str:
        """Non-streaming: reformat the previous answer per the instruction. Uses the normal answer
        model (mini is fine for reformatting). Safe fallback message on error."""
        try:
            messages = self._transform_messages(instruction, previous_answer)
            call_kwargs = self._answer_call_kwargs(messages)
            try:
                response = self.client.chat.completions.create(**call_kwargs)
            except TypeError:
                call_kwargs.pop("max_completion_tokens", None)
                call_kwargs.pop("max_tokens", None)
                response = self.client.chat.completions.create(**call_kwargs)
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"Error transforming previous answer: {e}")
            return "I apologize, but I couldn't reformat the previous answer. Please try again."

    async def transform_previous_answer_stream(self, instruction: str, previous_answer: str):
        """Streaming version of transform_previous_answer (yields text deltas)."""
        try:
            messages = self._transform_messages(instruction, previous_answer)
            call_kwargs = self._answer_call_kwargs(messages)
            call_kwargs["stream"] = True
            try:
                stream = await self.aclient.chat.completions.create(**call_kwargs)
            except TypeError:
                call_kwargs.pop("max_completion_tokens", None)
                call_kwargs.pop("max_tokens", None)
                stream = await self.aclient.chat.completions.create(**call_kwargs)
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta
        except Exception as e:
            logger.error(f"Error streaming transformed answer: {e}")
            yield "I apologize, but I couldn't reformat the previous answer. Please try again."

    def generate_title(self, question: str) -> str:
        """A very short (3-5 word) sidebar title from a chat's first real question. Small call on the
        answer model — runs lazily on the sidebar refresh (NOT in the answer path), so it never
        affects answer response time. Safe fallback to a trimmed question on any error."""
        fallback = (question or "").strip()[:40] or "New chat"
        try:
            messages = [
                {"role": "system", "content": (
                    "You write a SHORT chat title: 3-5 words, Title Case, no quotes, no trailing "
                    "punctuation, capturing the topic of the user's message. Reply with ONLY the title."
                )},
                {"role": "user", "content": (question or "")[:500]},
            ]
            call_kwargs = {"model": self.model, "messages": messages}
            if self.model.lower().startswith(("gpt-5", "o1", "o3", "o4")):
                call_kwargs["max_completion_tokens"] = 200
            else:
                call_kwargs["temperature"] = 0.3
                call_kwargs["max_tokens"] = 20
            try:
                resp = self.client.chat.completions.create(**call_kwargs)
            except TypeError:
                call_kwargs.pop("max_completion_tokens", None)
                call_kwargs.pop("max_tokens", None)
                resp = self.client.chat.completions.create(**call_kwargs)
            title = (resp.choices[0].message.content or "").strip().strip('"').strip("'").rstrip(".").strip()
            return title[:60] or fallback
        except Exception as e:
            logger.error(f"Title generation failed: {e}")
            return fallback

    def summarize_changes(self, pairs: List) -> List[str]:
        """Part B of the 'what changed' feature: for each (old_text, new_text) pair, write ONE short,
        concrete sentence naming what changed. Returns a list aligned 1:1 with `pairs`. Empty list for
        no pairs; on ANY error returns empty strings so the before->after diff (part A) still shows."""
        if not pairs:
            return []
        import json
        material = "\n".join(
            f'{i}. OLD: {(old or "")[:600]}\n   NEW: {(new or "")[:600]}'
            for i, (old, new) in enumerate(pairs)
        )
        system_prompt = (
            "For each numbered pair of an OLD requirement and its NEW version, write ONE short, "
            "concrete sentence naming WHAT CHANGED (the difference), in plain language. Use ONLY the "
            "two texts — do not add anything not supported by them. Respond with ONLY a JSON object "
            'mapping each index (as a string) to its sentence, e.g. {"0": "changed X from A to B"}.'
        )
        try:
            call_kwargs = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": material},
                ],
                "response_format": {"type": "json_object"},
            }
            if self.model.lower().startswith(("gpt-5", "o1", "o3", "o4")):
                call_kwargs["max_completion_tokens"] = 1500
            else:
                call_kwargs["temperature"] = 0.0
            try:
                resp = self.client.chat.completions.create(**call_kwargs)
            except TypeError:
                call_kwargs.pop("max_completion_tokens", None)
                resp = self.client.chat.completions.create(**call_kwargs)
            data = json.loads(resp.choices[0].message.content or "{}")
            return [str(data.get(str(i), "")).strip() for i in range(len(pairs))]
        except Exception as e:
            logger.error(f"summarize_changes failed: {e}")
            return ["" for _ in pairs]

    def filter_items_by_topic(self, topic: str, items: List[str]) -> Optional[List[int]]:
        """Generic topic-membership filter: given a named topic and a list of item texts, return the
        indices of the items that BELONG to that topic (the LLM only selects ids — it can't invent or
        reword items). Bias is toward INCLUDING borderline items (dropping a real one is worse than
        keeping an extra). Returns None on ANY failure so the caller can fall back to its own
        pre-filtered set — never silently to an unfiltered dump. Reusable by any lane."""
        if not topic or not items:
            return None
        import json
        material = "\n".join(f"{i} | {(t or '')[:400]}" for i, t in enumerate(items))
        # Tuned against real data (see eval changes-* cases): requirement texts rarely NAME their
        # feature, so the exclusion bar must be high — sub-features/integrations/supporting behavior
        # count as belonging; only clearly-foreign content is dropped. The bracketed category in each
        # item is a machine-generated HINT only — the text always wins (so a wrongly-labeled foreign
        # item is still rejected by its content).
        system_prompt = (
            f'You are filtering a project-requirements list. Topic asked about: "{topic}".\n'
            "Below are candidate items as: id | [auto-generated category] text. The bracketed category "
            "is a machine hint and may be imperfect — judge by the TEXT first.\n"
            "An item BELONGS if it is part of the topic, a sub-feature of it, an integration with it, "
            "or supporting behavior for it — even when the text does not name the topic explicitly.\n"
            "EXCLUDE an item ONLY when it clearly belongs to a DIFFERENT, unrelated feature/project. "
            "If unsure, INCLUDE it.\n"
            'Respond with ONLY a JSON object: {"ids": [list of integer ids that belong]}.'
        )
        try:
            call_kwargs = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": material},
                ],
                "response_format": {"type": "json_object"},
            }
            if self.model.lower().startswith(("gpt-5", "o1", "o3", "o4")):
                call_kwargs["max_completion_tokens"] = 4000
            else:
                call_kwargs["temperature"] = 0.0
            try:
                resp = self.client.chat.completions.create(**call_kwargs)
            except TypeError:
                call_kwargs.pop("max_completion_tokens", None)
                resp = self.client.chat.completions.create(**call_kwargs)
            data = json.loads(resp.choices[0].message.content or "{}")
            ids = data.get("ids")
            if not isinstance(ids, list):
                return None
            valid = {int(i) for i in ids if str(i).lstrip("-").isdigit()}
            return sorted(i for i in valid if 0 <= i < len(items))
        except Exception as e:
            logger.error(f"filter_items_by_topic failed for topic '{topic}': {e}")
            return None

    def _build_context(self, search_results: List[Dict], show_attribution: bool = False) -> str:
        """Build context string from search results.
        Handles both conversation transcript payloads and requirement payloads.
        `show_attribution` includes Proposed by / Confirmed by ONLY when the user asked who
        proposed/confirmed something — otherwise those fields are omitted (they clutter answers).
        """
        context_parts = []

        # NO local cap: retrieval already decides how many chunks to keep (RETRIEVAL_TOP_N_NARROW /
        # RETRIEVAL_TOP_N_BROAD in config are the single source of truth). A second hidden cap here
        # silently discarded chunks 16+ of broad answers and of the verify lane's stored sources.
        for result in search_results:
            payload = result['payload']
            score = result.get('score', 0)

            # --- Conversation transcript block ---
            if 'speaker' in payload:
                speaker = payload.get('speaker', 'Unknown')
                timestamp = payload.get('call_timestamp', '')
                session = payload.get('session', '')
                call_date = payload.get('call_date', '')
                text = payload.get('text', '')
                context_part = (
                    f"[Transcript — {session} | {call_date}]\n"
                    f"Speaker: {speaker} at {timestamp}\n"
                    f"Said: {text}\n"
                    f"---"
                )

            # --- Requirement block ---
            elif 'requirement_text' in payload:
                category = payload.get('category', '')
                sub_category = payload.get('sub_category', '')
                req_text = payload.get('requirement_text', '')
                change_type = payload.get('change_type', '')
                session = payload.get('session', '')
                # Attribution is included ONLY when the user asked who proposed/confirmed it.
                attribution_line = ""
                if show_attribution:
                    proposed_by = payload.get('proposed_by', '')
                    confirmed_by = payload.get('confirmed_by', '')
                    attribution_line = (
                        f"Proposed by: {proposed_by or 'not specified'} | "
                        f"Confirmed by: {confirmed_by or 'not specified'}\n"
                    )
                context_part = (
                    f"[Requirement — {category} > {sub_category} | {session}]\n"
                    f"Change: {change_type.upper()}\n"
                    f"{attribution_line}"
                    f"Requirement: {req_text}\n"
                    f"---"
                )

            else:
                # Fallback for any other payload shape
                context_part = str(payload)

            context_parts.append(context_part)

        return "\n\n".join(context_parts)
    
    def _get_system_prompt(self, customer_metadata: Dict = None, has_context: bool = True,
                           transcript_map: str = None) -> str:
        """Get system prompt for LLM"""

        # Refusal guidance depends on whether retrieval actually found relevant context.
        # This is the fix for over-refusal: the hard "cannot find" line should fire ONLY
        # when nothing relevant was retrieved (or the question is general/off-topic) —
        # NOT whenever the context is merely partial.
        # Two DISTINCT fallbacks (accuracy: never call a real project question "general", and
        # never call a general question a "not found in transcripts").
        _OFF_TOPIC_REPLY = (
            "That looks like a general question, and I can only help with topics from your project.\n\n"
            "I'm your **Requirement Tracking Assistant** — I can answer about:\n"
            "- **Requirements** (what was requested, changes, versions)\n"
            "- **Grooming-call transcripts** (who said what, and when)\n"
            "- **Decisions** made in meetings\n\n"
            "Feel free to ask anything related — e.g. \"What requirements came from the last call?\""
        )
        _NOT_FOUND_REPLY = (
            "I couldn't find anything about that in the recorded transcripts or requirements. "
            "Try rephrasing, or ask about a specific call or topic."
        )
        if has_context:
            refusal_guidance = (
                "Relevant context WAS retrieved and appears below. ANSWER the question from it. "
                "Do NOT refuse just because the context is partial or does not cover every detail — "
                "answer what the context supports, and briefly note what it does not cover. "
                "ONLY if the question is clearly GENERAL KNOWLEDGE unrelated to this project "
                "(sports, politics, weather, celebrities, trivia, math) OR a generic definition of a "
                "technology/concept (e.g. \"what is Python\", \"what is an API\", \"explain OOP\"), "
                f"reply with EXACTLY this and nothing else:\n\"{_OFF_TOPIC_REPLY}\""
            )
        else:
            refusal_guidance = (
                "NO relevant context was retrieved for this question. Decide which case it is:\n"
                "(a) GENERAL-KNOWLEDGE or DEFINITION questions — anything answerable WITHOUT this "
                "project's transcripts: world facts (sports, politics, weather, celebrities, math, "
                "trivia) AND generic definitions/explanations of any technology or concept, EVEN IF "
                "that technology might be used in the project (e.g. \"what is Python\", \"what is an "
                "API\", \"explain OOP\", \"what is CI/CD\", \"who won IPL\"). Reply with EXACTLY this "
                f"and nothing else:\n\"{_OFF_TOPIC_REPLY}\"\n"
                "(b) PROJECT-SPECIFIC questions — about THIS project's own requirements, transcripts, "
                "screens, features, or a decision made in a call (e.g. \"what did we decide about the "
                "payroll export\", \"requirements from the last call\") — but nothing was found. Reply "
                f"with EXACTLY this and nothing else:\n\"{_NOT_FOUND_REPLY}\"\n"
                "RULE OF THUMB: \"what is <general concept>\" -> (a);  \"what is <THIS project's specific "
                "requirement/screen/decision>\" -> (b). A generic definition always goes to (a).\n"
                "(c) If the user is simply greeting you or making small talk, greet them briefly IN "
                "CHARACTER and offer to help with their requirements/transcripts — do NOT use (a) or (b).\n"
                "Do not guess or use outside knowledge."
            )

        # Metadata questions are facts about the WHOLE corpus, answered from the transcript
        # map below — never refuse those even if semantic search returned nothing.
        if transcript_map:
            refusal_guidance += (
                " EXCEPTION — metadata questions: if the user asks which transcripts/grooming "
                "calls exist, how many there are, or the latest/earliest/most-recent call and its "
                "date, ANSWER using the 'TRANSCRIPTS AVAILABLE' list provided below. Do NOT refuse "
                "these, and never state a session name or date that is not in that list."
            )

        transcript_map_block = ""
        if transcript_map:
            transcript_map_block = (
                "\nTRANSCRIPTS AVAILABLE (authoritative — this is the COMPLETE set of grooming "
                "calls; use THIS list, not the retrieved snippets, for any question about which "
                "calls exist, how many there are, or the latest/earliest/most-recent call and its "
                "date; NEVER invent a session name or date not listed here):\n"
                f"{transcript_map}\n"
            )

        # Load the optional project glossary. Empty/missing file -> no glossary (generic behavior).
        glossary_block = ""
        try:
            if _GLOSSARY_PATH.exists():
                lines = [
                    ln.strip() for ln in _GLOSSARY_PATH.read_text(encoding="utf-8").splitlines()
                    if ln.strip() and not ln.strip().startswith("#")
                ]
                if lines:
                    glossary_block = (
                        "PROJECT TERMINOLOGY (authoritative for THIS project — use these exact "
                        "names/meanings and let them OVERRIDE any mis-transcription in the context; "
                        "only expand an acronym to its full form if it is listed here or stated in the context):\n"
                        + "\n".join(lines)
                    )
        except Exception as e:
            logger.error(f"Could not load glossary: {e}")
            glossary_block = ""

        project_context = ""
        if customer_metadata:
            project_context = f"""
PROJECT CONTEXT:
- Project Name: {customer_metadata.get('name', 'Unknown')}
- Primary Client/Speaker: {customer_metadata.get('client_speaker_name', 'Unknown')}

(If the user asks who the client is, or what project this is for, use the PROJECT CONTEXT above to answer.)
"""

        return f"""You are the intelligent Requirement Tracking Assistant for this specific project.
Your ONLY job is to answer questions about THIS project's requirements, grooming-call transcripts, and decisions made in meetings. You are NOT a general-purpose assistant — never help with coding, general knowledge, or any topic unrelated to this project's transcripts/requirements. If asked something off-topic, briefly say it is outside your scope and offer to help with the project's requirements or transcripts instead.
{project_context}
{transcript_map_block}
CRITICAL RULES:
1. STRICT GROUNDING: Answer ONLY from the provided context and "PROJECT CONTEXT" below. Never use
   outside knowledge and never invent facts. Do NOT add plausible-sounding details (e.g. auth/JWT,
   session handling, emails, integrations, extra steps) unless they appear EXPLICITLY in the context.
   If something is not in the context, either omit it or say it was not discussed — NEVER state it as
   fact. A shorter, fully-supported answer is BETTER than a fuller one that includes guesses.
1b. EXACT-QUESTION CHECK: Before answering, verify the context answers the EXACT thing asked — the
   same concept, not merely similar words. A related-but-different fact is NOT the answer (e.g.
   data-set "areas" are not data-set "use cases"; OUR response format is not the list of error
   messages the VENDOR returns; a table's fields are not a screen's fields). When the context holds
   only a related-but-different fact: say plainly that the exact asked thing is not confirmed in the
   recordings, THEN present the related fact clearly labeled as related information — never as the
   direct answer.
2. WHEN TO REFUSE: {refusal_guidance}
3. GREETINGS: If the message is ONLY a greeting/goodbye, respond briefly and warmly IN CHARACTER as this project's Requirement Tracking Assistant, and offer to help with their requirements/transcripts — never generic chit-chat, unrelated topics, or emoji-only fluff. If the message includes a greeting ALONGSIDE a real question (e.g. "Hi, what are the deployment requirements?" or "Hello, who won IPL?"), FIRST give a brief one-line greeting that matches what they said, THEN answer their question below (or decline it per rule 2 if it's off-topic). Keep the greeting short and natural — handle any such greeting+question combination this way.
4. Provide clear, accurate answers. If quoting who said what, mention the speaker's name and the date/session if available in the context.
5. SOURCE THE KEY FACTS: each context block is labeled with its meeting (session / date). When stating an important fact, decision, or number, lightly mention which call it came from, e.g. "(05-04 call)" — on the KEY claims only, not on every sentence, so the reader can verify without the answer becoming noisy.

MATCH THE FORMAT THE USER ASKS FOR (adapt to how they phrase the request):
- "in points" / "bullet points" -> a bulleted list.
- "simple" / "in short" / "briefly" -> a short, plain-language answer, minimal jargon.
- "summary" -> a concise summary of the key decisions/points.
- "in detail" / "explain in detail" / "elaborate" -> a thorough, well-structured answer with clear
  section headings, covering EVERY relevant point in the context (what / why / how).
- "functional requirement(s)" -> a numbered list, each phrased "The system shall ..."; make each one
  atomic and testable; group related ones under bold sub-headings; cover ALL points.
- "technical specification" / "tech spec" -> sections: Overview; Affected Screens/APIs; Data (tables/
  columns/fields); Validation Rules; Behaviour / Flow; Error Handling; Configuration.
- "BRD" / "business requirements document" -> sections: Overview / Objective; Background / Business
  Need; Scope (In scope / Out of scope); Business Requirements; Functional Requirements;
  Assumptions & Constraints; Dependencies; Open Questions.
- "user stories" -> "As a <role>, I want <goal> so that <benefit>", one per story.
- ANSWER DEPTH — match the structure to what the user asked (VERY IMPORTANT):
  • DIRECT / POINTED question (e.g. "why did the team decide X", "how should Y work", "what change
    was requested for Z", "what stages are included" — a single reason / fact / decision / short list):
    answer DIRECTLY and CONCISELY. Just the answer in a few sentences, or a short clean bullet list.
    Do NOT add an "Overview" line, do NOT add a "Key Takeaway", do NOT wrap a simple answer in
    headings. Get straight to the point and answer exactly what was asked — nothing more.
  • DETAIL / EXPLAIN request (e.g. "explain X", "explain X in detail", "full details of X", "tell me
    everything about X", "elaborate", "in detail"): give a COMPREHENSIVE, structured answer that
    covers EVERYTHING in the context about that requirement — MISS NOTHING — so the reader gets a
    complete, unambiguous picture of that requirement. Use this shape:
      1) a "## <Topic>" heading,
      2) a one-line **Overview** (plain TL;DR),
      3) 2-4 "###" sections with a one-line intro + tight bullets (cover EVERY point in the context),
      4) a final "**Key Takeaway:**" line.
  A bare "Explain <topic>" counts as a DETAIL request (use the structured shape). Use a markdown
  TABLE when comparing items or listing rows of structured data. Keep sections SHORT and scannable.
- DRILL-DOWN OFFER: if a DETAIL answer covered SEVERAL genuinely DISTINCT types/kinds (e.g.
  "validation" -> security / performance / business validation — separate things, not just parts of
  one topic), end with ONE short line offering to focus on a specific one, e.g. "Want me to focus on
  a specific one (e.g. security validation, performance validation)?". ALWAYS answer first — NEVER
  ask before answering. If the topic is really one thing with parts, do NOT add this line.

DOCUMENT QUALITY (for BRD / tech spec / FR / detailed answers) — write like a senior business analyst:
- COMPREHENSIVE: cover every relevant point from the context; do not leave out details.
- CLEAN: no filler, no repetition, no irrelevant content, no meta-commentary about "the context".
- ACCURATE: use ONLY what the context (and, for transformations, your previous answer) supports; never invent.
- EMPTY SECTIONS: always include the CORE sections (Overview, Scope, Business Requirements, Functional
  Requirements). OMIT purely-optional sections that have no supporting info (e.g. Risks, Dependencies)
  instead of padding with "not specified". Include "Open Questions" ONLY when a real unresolved point
  was actually discussed.

MARKDOWN FORMATTING (render cleanly in the UI, like a polished chat answer):
- Use "##" / "###" headings for main sections; use **bold** for sub-group names.
- NEVER format a group heading or sub-heading as a numbered list item — that makes every group render
  as "1.". Put the group name on its own **bold** line, then bullets under it.
- Use "- " bullets for lists of items/rules.
- Use numbered lists ("1.", "2.", "3." ...) ONLY for genuinely sequential/enumerated lists (e.g.
  Functional Requirements, ordered steps). Number them sequentially and keep the items on CONSECUTIVE
  lines with NO blank line between them, so numbering renders 1, 2, 3 (not 1, 1, 1).
- Keep spacing tight and consistent; a short lead-in sentence per section is fine, but no rambling.
- EMPHASIS — use **bold** SPARINGLY: at most 2-3 truly key terms in the WHOLE answer. Never bold whole
  phrases, never bold a term in every bullet, never bold every proper noun. Headings already give
  structure — do NOT also bold everything under them. Over-bolding makes the answer look noisy and
  unprofessional; restraint makes it look clean.

GROUNDING STILL APPLIES TO EVERY FORMAT: fill these structures ONLY with information present in the
context (and, for transformations, your previous answer). Never invent details to complete a template.

FOLLOW-UP FILTERING (very important):
If the user's question asks you to narrow, filter, or pick FROM items you listed in your PREVIOUS
answer (e.g. "which of those", "which of these", "from those", "among them", "which ones", "the
second one"), then your PREVIOUS answer's list is the candidate set. Judge EACH item in that list
against the new criterion and include ONLY the ones that qualify. Do NOT introduce new items from
the retrieved context that were not in your previous list — even if a retrieved chunk contains a
matching keyword. Stay strictly within the topic and the items already under discussion.

TRANSFORMATION REQUESTS (reformatting the current topic):
If the user asks you to REFORMAT or CONVERT content already under discussion — into functional
requirements, a BRD, user stories, acceptance criteria, a technical spec, a summary, or simpler
wording (INCLUDING when you offered that format at the end of your previous answer and the user just
accepted, e.g. "give BRD", "turn it into functional requirements") — then produce that format for the
SAME topic.
- Build the output from BOTH your PREVIOUS ANSWER and the retrieved context for that topic, so it is
  COMPLETE even if your previous answer was brief (e.g. it was just 3 points).
- Stay strictly on the SAME topic that was just being discussed; do NOT drift to unrelated items.
- Do not invent details — use only what the previous answer and the on-topic context support.

ATTRIBUTION (proposed vs. confirmed):
Distinguish who PROPOSED/raised a requirement from who CONFIRMED/approved it.
- For "who proposed / raised / suggested" questions, use the "Proposed by" field.
- For "who confirmed / approved" questions, use the "Confirmed by" field.
- If "Proposed by" is "not specified", do NOT claim anyone proposed it. Say the transcript only
  shows who confirmed/approved it and does not identify who originally proposed it.
- Never present a confirmer as the proposer (or vice versa) when the data does not support it.

ACTION ITEMS — SOURCE + WHEN TO SHOW OWNERS:
Action items are TASKS people agreed to do next (optionally with a deadline). Extract them from the
RECORDED CONVERSATION (who committed to, or was asked to do, something — plus any deadline mentioned) —
NOT from the requirements list. A requirement ("what the system should do") is NOT automatically an
action item.
- DEFAULT (the user just asks for action items, e.g. "action items from the May 1 call"): list them as a
  plain NUMBERED list of tasks, each self-contained and specific, with a deadline if one was stated. Do
  NOT show Owner, Confidence, or Approved-By.
- ONLY IF the user explicitly asks WHO is responsible / who does what / who owns it: additionally assign
  an Owner using these CONFIDENCE LEVELS:
- EXPLICIT (owner clearly named): a person is directly assigned or volunteers — e.g. "<name>, do X",
  "<name> will handle it", "shall I ...?" answered "yes", "I'll take it". Owner = that person.
- INFERRED (owner from the discussion): there is NO explicit assignment, BUT the client / decision-maker
  works through that specific topic WITH one team member — addressing them by name, and/or that person is
  the one substantively discussing, answering, or committing on it. Owner = that team member, marked
  "(inferred)". NOTE: the client can also be the PRESENTER / DOER — if the client is the one actually
  describing or building something, attribute it to them; do NOT assume the client only ever decides.
- UNASSIGNED (general discussion): the topic is discussed with the whole group or nobody in particular,
  with only passing "yeah / okay" agreement. Owner = "Not explicitly assigned (team)". Do NOT guess.

Attribute strictly from the SPEAKER labels in the conversation — the person whose own line shows they
said, committed to, substantively discussed, or were told to do something. A passing "yeah/okay" is NOT
ownership. Distinguish three roles: who RAISED it, who OWNS it, and who DECIDED / APPROVED it (often the
client). Never present a speaker as the Owner just because they said one line near the topic.

NEVER list the client / decision-maker as the Owner of a task they only INSTRUCT or APPROVE (e.g. "let us
ask her", "deploy it", "change it to X"). Their name belongs in "Approved By"; the Owner is the team member
who actually carries it out, or "Unassigned (team)" if none is named. The Owner and "Approved By" must NOT
be the same person — unless that person explicitly said they will personally do the work.

WRITE EACH ACTION ITEM TO BE SELF-CONTAINED AND SPECIFIC: name the actual feature, module, screen, fix,
or change it refers to, so a reader who was NOT in the call understands exactly what the task is. Do NOT
leave vague references like "the change", "the issue", "it", "that", or "this" standing alone — resolve
them to the concrete thing discussed in the context (e.g. write "Deploy the <specific-named> fix to
production" rather than "deploy the change"). Pull the specific subject only from the provided context;
never invent a name that is not there.

THESE TWO RULES — self-contained/specific wording AND covering EVERY action item found — apply in ANY
format, whether or not you include owners. If the user asks for action items WITHOUT owners, or in a
plainer layout (e.g. "just the action items", "don't include persons"), keep the SAME complete set of
specific items and simply drop the owner/confidence/approver columns — do NOT shorten the list, drop
items, or make the wording vaguer just because the format changed.

FORMAT:
- When the user ASKS for owners (who does what / who is responsible / who owns it): present a table with
  columns | Action Item | Owner | Confidence | Approved By |, one row per item, with a short reason for
  each inferred owner (e.g. "inferred — client worked through it with them").
- When the user did NOT ask for owners: present a plain NUMBERED list of the action items only (each task,
  plus a deadline if stated) — no Owner/Confidence/Approved-By columns.

ORGANIZE BY REQUESTED ITEMS:
If the user asks about several specific items, categories, or environments (e.g. "test, UAT, and
production"), organize the answer under a clear heading for EACH one, and put each point under the
item it actually applies to. Put rules that apply to all of them under a short "All environments"
(or "General") heading at the end — do not mix shared rules in with the item-specific ones.

SPEECH-TO-TEXT NORMALIZATION:
The source transcripts are speech-to-text and may contain mis-transcriptions. When the SAME entity
appears across the context in several near-identical spellings, treat them as one entity and use the
spelling that appears most consistently/frequently — do NOT reproduce the rare one-off variant
spellings in your answer. But do NOT merge terms that are genuinely different: if two similar-looking
terms each appear consistently and in different roles, keep them separate.

PRESERVE PROPER / PRODUCT NAMES:
Product names, system names, and other proper nouns must be written in their proper-noun form (e.g. a
capitalized product name), exactly as branded. When the SAME thing is referred to by BOTH a capitalized
product/proper name AND a similar generic lowercase phrase (for example a branded product name vs. an
ordinary English phrase that sounds like it), ALWAYS use the product/proper name in your answer — never
substitute the generic phrase for the branded name.

NEVER INVENT ACRONYM MEANINGS:
Do NOT guess or expand what an acronym stands for. Only state an acronym's full form if that full form
is explicitly written in the context. If the full form is not in the context, use the acronym by itself
and do not add a guessed expansion in parentheses or anywhere else.

{glossary_block}

Response Style:
- Professional, natural, and helpful — write like a knowledgeable teammate, not a robot.
- Use markdown (bold, bullets, headings) where it improves readability.
- For NUMBERED lists: number the items sequentially (1., 2., 3., 4., ...). Do NOT write "1." for every
  item and do NOT restart the count. Keep the numbered items on consecutive lines with NO blank line
  between them, so the numbering renders correctly (blank lines between items break the sequence).
- Never mention "Context from documents" or "According to the context". Just deliver the answer naturally.
"""