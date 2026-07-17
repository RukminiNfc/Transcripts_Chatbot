from openai import OpenAI, AsyncOpenAI
from app.core.config import settings
from typing import List, Dict
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

    def _build_messages(self, query, search_results, conversation_history,
                        customer_metadata, has_context, transcript_map, dialogue_context=None):
        """Assemble the chat messages once, shared by the streaming and non-streaming paths."""
        context = self._build_context(search_results)
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
    
    def _build_context(self, search_results: List[Dict]) -> str:
        """Build context string from search results.
        Handles both conversation transcript payloads and requirement payloads.
        """
        context_parts = []

        for result in search_results[:15]:
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
                confirmed_by = payload.get('confirmed_by', '')
                proposed_by = payload.get('proposed_by', '')
                change_type = payload.get('change_type', '')
                session = payload.get('session', '')
                context_part = (
                    f"[Requirement — {category} > {sub_category} | {session}]\n"
                    f"Change: {change_type.upper()} | "
                    f"Proposed by: {proposed_by or 'not specified'} | "
                    f"Confirmed by: {confirmed_by or 'not specified'}\n"
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
        _REFUSAL_PHRASE = (
            "I'm sorry, but I cannot find the answer to that in the recorded transcripts "
            "or requirements database."
        )
        if has_context:
            refusal_guidance = (
                "Relevant context WAS retrieved and appears below. ANSWER the question from it. "
                "Do NOT reply with the refusal phrase just because the context is partial or does "
                "not cover every detail — answer what the context supports, and briefly note what "
                "it does not cover. Use the refusal phrase ONLY if the question is clearly general "
                f"knowledge unrelated to this project. The refusal phrase is: \"{_REFUSAL_PHRASE}\""
            )
        else:
            refusal_guidance = (
                "NO relevant context was retrieved for this question. If it is a question about the "
                "project, its requirements, transcripts, or decisions, reply with EXACTLY this "
                f"phrase and nothing else: \"{_REFUSAL_PHRASE}\" "
                "If the user is greeting you, greet them back and ask how you can help. If it is a "
                "general-knowledge question unrelated to the project, also reply with the exact "
                "refusal phrase. Do not guess or use outside knowledge."
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

        return f"""You are the intelligent Requirement Tracking Assistant.
Your sole job is to answer questions about the user's project requirements, grooming call transcripts, and decisions made in meetings.
{project_context}
{transcript_map_block}
CRITICAL RULES:
1. STRICT GROUNDING: Answer ONLY from the provided context and "PROJECT CONTEXT" below. Never use outside knowledge and never invent facts.
2. WHEN TO REFUSE: {refusal_guidance}
3. If the user greets you, politely say hello and ask how you can help them with their requirements.
4. Provide clear, accurate answers. If quoting who said what, mention the speaker's name and the date/session if available in the context.

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
- If no format is requested -> a clear, well-organised answer; bullets/bold for anything non-trivial.

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