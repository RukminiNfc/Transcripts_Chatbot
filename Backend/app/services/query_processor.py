from typing import Dict, Any, List, Optional
import json
import logging
import re
from openai import OpenAI
from app.core.config import settings

logger = logging.getLogger(__name__)

class QueryProcessor:
    """Processes user queries: routing intent, metadata filters, typo-fix, and
    history-aware rewriting of follow-up questions (so retrieval works on follow-ups)."""

    def __init__(self):
        self.client = OpenAI(api_key=settings.OPENAI_API_KEY)
        self.model = settings.OPENAI_LLM_MODEL

    @staticmethod
    def _is_next_gen(model: str) -> bool:
        """GPT-5 / o-series reject a custom 'temperature' and use 'max_completion_tokens'."""
        return model.lower().startswith(("gpt-5", "o1", "o3", "o4"))

    # Reference / continuation words that signal a FOLLOW-UP leaning on the current topic.
    _FOLLOWUP_WORDS = frozenset({
        "it", "its", "that", "this", "they", "them", "those", "these", "their",
        "there", "here", "he", "she", "his", "her", "above", "previous", "prev",
        "continue", "example", "more", "same", "another", "again",
    })

    @classmethod
    def _looks_like_followup(cls, query: str, conversation_history) -> bool:
        """Cheap gate: does this question likely lean on the current topic (a follow-up)?
        Only decides WHICH model resolves the intent (mini vs the stronger resolver) — never the
        pipeline outcome — so an imperfect guess is harmless. True when there IS prior conversation
        AND the question uses a reference/continuation word (it / that / they / example / another / ...)."""
        if not conversation_history:
            return False
        words = re.findall(r"[a-z']+", (query or "").lower())
        return any(w in cls._FOLLOWUP_WORDS for w in words)

    @staticmethod
    def _format_history(conversation_history: Optional[List[Dict]],
                        max_turns: int = 6, clip: int = 600) -> str:
        """Build a short recent-history snippet for reference resolution.
        Only the last few turns are kept, and long assistant answers are clipped."""
        if not conversation_history:
            return ""
        lines = []
        for msg in conversation_history[-max_turns:]:
            content = (msg.get("content") or "").strip()
            if not content:
                continue
            if msg.get("role") == "assistant" and len(content) > clip:
                content = content[:clip] + " ..."
            speaker = "User" if msg.get("role") == "user" else "Assistant"
            lines.append(f"{speaker}: {content}")
        return "\n".join(lines)

    def analyze_intent(self, query: str,
                       conversation_history: Optional[List[Dict]] = None,
                       available_dates: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Determines routing (conversations vs requirements), extracts metadata filters, and
        builds a standalone "search_query": typos fixed, abbreviations expanded, and — for
        follow-up questions — references ("it", "the second one", "what about X") resolved
        using the recent conversation. Safe fallback on error: raw query, requirements intent.
        """
        history_snippet = self._format_history(conversation_history)
        history_block = (
            "\nRECENT CONVERSATION (most recent last) — use ONLY to resolve references "
            f"in the new question:\n{history_snippet}\n" if history_snippet else ""
        )

        dates_block = ""
        if available_dates:
            dates_block = (
                "\nAVAILABLE CALL DATES (YYYY-MM-DD, oldest to newest) — resolve relative "
                "references like 'the last call' or 'the first meeting' against THIS list:\n"
                + ", ".join(available_dates) + "\n"
            )

        system_prompt = """You are an intent router, filter extractor, and query rewriter for a Requirement Tracking System.
Given the recent conversation (if any) and the user's NEW question, do three things:
(1) route to the correct store, (2) extract any metadata filters, (3) build a clean standalone search query.

ROUTE TO 'conversations' IF:
- The user asks what a specific person said (e.g. "What did Viswapani say?")
- The user asks about conversations, questions raised, or transcript details.
- The user mentions a specific timestamp or "who raised".
- The user asks who participated or attended a meeting/discussion.
- The user asks when a meeting/session was conducted.

ROUTE TO 'requirements' IF:
- The user asks about project requirements, rules, specifications, or changes.
- The user asks "What are the rules for..." or "What changed in..."
- The user asks "Show all requirements..."

EXTRACT FILTERS:
Look for specific entities to filter the vector search. The allowed filter keys are:
- 'speaker' (e.g., "Prasad Kadrikar", "Viswapani", "Naresh Godera" — MUST be capitalized)
- 'session' (e.g., "Grooming 35" — MUST be capitalized exactly like this, e.g. if user says "grooming 35", output "Grooming 35")
- 'call_date' (format MUST be exactly YYYY-MM-DD). Resolve BOTH explicit dates AND relative
  references to a SPECIFIC call/meeting, using the AVAILABLE CALL DATES list (if provided):
    * explicit: "June 2nd 2026" -> "2026-06-02"
    * "the last / latest / most recent call/meeting/grooming/transcript" -> the NEWEST available date
    * "the first / earliest call" -> the OLDEST available date
    * "the previous call" -> the call just before the one currently in focus, if determinable
  Only set 'call_date' when the user is referring to a SPECIFIC call/meeting. Do NOT set it for
  general phrases like "the latest requirements" that are not about a particular meeting. For a
  relative reference when no AVAILABLE CALL DATES are given, omit 'call_date'.
If no specific filter is explicitly mentioned, do NOT include the key in the filters object.

CLASSIFY "mode":
- "transform" if the user is asking you to REFORMAT / CONVERT / REWRITE content that is ALREADY
  under discussion — e.g. "convert to functional requirements", "give BRD", "write user stories",
  "make it a technical spec", "turn this into ...", "summarize the above", "simplify that",
  "give acceptance criteria". These operate on the PREVIOUS answer, NOT on new information.
- "retrieve" for everything else (information questions: what / who / when / why / which / list /
  explain a topic). When in doubt, choose "retrieve".

CLASSIFY "transform_scope" (ONLY meaningful when mode is "transform"; otherwise "none"):
- "previous_answer" — the user wants to CONDENSE / REPHRASE / SIMPLIFY the answer JUST GIVEN and does
  NOT name a new subject (e.g. "summarize that", "make it shorter", "in simple words", "key points",
  "TL;DR", "shorten it"). Reuse the previous answer directly; NO new information.
  CRITICAL: if the request NAMES A SPECIFIC TOPIC/FEATURE (e.g. "summarize candidate management",
  "summarize the deployment process"), it is NOT "previous_answer" — it is a NEW topic, so use
  "topic" and let it be retrieved fresh (never reuse the previous answer for a named topic).
- "topic" — the user wants a COMPREHENSIVE document BUILT FROM THE TOPIC (e.g. "give BRD",
  "functional requirements", "user stories", "technical spec", "acceptance criteria"). These need
  full retrieval on the topic, not just the previous answer.
- "none" — whenever mode is "retrieve".

DECIDE "needs_dialogue" (true/false) — does answering require the CONVERSATION FLOW / discussion,
not just isolated facts?
- true when the question is about: who does / owns / is responsible for something; who said or
  raised or was asked to do something; WHY something was decided; what was concluded, discussed,
  agreed, or left unresolved; action items / follow-ups. These need the back-and-forth between
  speakers to answer correctly.
- false for plain fact lookups: listing requirements, "what is the rule for X", counts, and
  metadata (how many calls, dates, latest transcript). When in doubt, choose false.

CLASSIFY "aggregate" — does the user want a COUNT or the COMPLETE LIST of requirements, rather than a
topic-specific answer?
- "count" — the user asks HOW MANY / the total (e.g. "how many requirements", "total requirements",
  "count of requirements from ...").
- "list" — the user asks for ALL / the full set (e.g. "list all requirements", "give all requirements",
  "show every requirement from the May 1 call").
- "none" — anything else: a specific topic, a discussion question, or a subset ("what are the deployment
  requirements", "requirements about X"). When in doubt, choose "none".
Use count/list ONLY when the user clearly wants a total or the entire set, not a topic subset.

CLASSIFY "changes" — is the user asking WHAT CHANGED between requirement versions (a diff / history),
rather than asking for the requirements themselves? Judge by MEANING:
- "modified" — what was modified / changed / updated / edited / revised.
- "added" — what is new / added / newly introduced.
- "removed" — what was removed / deleted / dropped.
- "all" — a general "what changed" that may span added, modified AND removed together.
- "none" — NOT a change/diff question (a normal list / explain / topic question). When in doubt, "none".
Use a non-"none" value ONLY when the user clearly asks what CHANGED, not merely to list or explain
requirements. A specific call/date ("in the last call", "on May 1") still goes in 'call_date' as usual.

DECIDE "complete_requirements" (true/false) — is the user asking for THE (FULL / COMPLETE) SET OF
REQUIREMENTS about a SPECIFIC named feature or topic, as a LIST? Judge by MEANING. TRUE ONLY for a
set-of-requirements request: "requirements for Carv", "full requirements on eRegister", "give me all
requirements for the Hospitality Queue", "what are the requirements for payroll". FALSE for: a
question about ONE requirement — singular phrasing such as "what is the <X> requirement?" or
"explain the <X> requirement" (the user wants that requirement EXPLAINED, not a list); ANY
"explain ..." / "describe ..." / "how does ... work" question, EVEN when it contains the plural word
"requirements" (an explanation is a written answer, not a list dump); a pointed single fact ("who
requested the resume endpoint", "why keep CI/CD for dev"); a who/why/when/discussion question; a
count or list-everything request (that is 'aggregate'); a change/diff request (that is 'changes');
or anything not about a feature's requirements. When in doubt, FALSE — a written explanation is the
safe default.

EXTRACT "topic" — the SPECIFIC named feature/project/subject the question is about, when one is named
(e.g. "Hospitality Queue", "Carv", "email automation", "eRegister", "handbooks"). Copy the user's
naming (typo-fixed), keep it SHORT (the subject only — no verbs, no "requirements"/"changes" words).
If the question names NO specific subject (e.g. "what changed in the last call?", "list all
requirements"), return null. For follow-ups, resolve the topic from the RECENT CONVERSATION the same
way search_query does.

DECIDE "verify" (true/false) — is the user CHALLENGING or double-checking the PREVIOUS answer (e.g.
"are you sure?", "really?", "is that correct?", "verify that", "double-check", "you sure about that?")?
Judge by MEANING. true ONLY when the user is questioning the correctness of what was JUST said; false
for a normal new question. When true, keep search_query as the TOPIC of the previous answer (the
answer is re-checked against that answer's own grounding, not a fresh search).

DECIDE "scope" (narrow/broad) — how MUCH context does answering need?
- "narrow" — a specific fact, a single reason, a who/when, a pointed question (e.g. "who requested X",
  "why keep CI/CD for dev", "what change was requested for bonus orders"). Needs FEW chunks.
- "broad" — explaining a whole topic, comparing things, a history/timeline, or listing many items
  (e.g. "explain eRegister", "compare dev and test", "bonus order history", "summarize deployment").
  Needs MANY chunks.
When unsure, choose "broad" (more context is safer than missing it).

DETECT "greeting" — did the user include a greeting / salutation in THIS message? Judge by MEANING,
not exact words (covers "hi", "hey", "hello", "yo", "gm", "good morning/afternoon/evening", etc.),
INCLUDING when the greeting comes ALONGSIDE a real question (e.g. "Hi, what are the deployment
requirements?" -> greeting present). Return the KIND:
- "morning" / "afternoon" / "evening" — a time-of-day greeting ("good morning", "good evening").
- "hi" — any other greeting ("hi", "hello", "hey", "yo", "greetings", "gm").
- "none" — the message contains NO greeting at all.

BUILD "search_query" (the standalone question used for retrieval):
- Start from the user's NEW question.
- Fix obvious spelling mistakes/typos and expand common abbreviations.
- If the NEW question is a FOLLOW-UP that refers to earlier turns (e.g. "it", "that", "those",
  "the second one", "what about X", "explain more", "in detail"), REWRITE it into a full
  standalone question by pulling the missing subject from the RECENT CONVERSATION above.
- ALSO rewrite TOPIC-DEPENDENT follow-ups that have NO explicit pronoun but only make sense in the
  current topic (e.g. "what was the unresolved question?", "what was decided?", "what's the
  timeline?", "who confirmed it?", "was it approved?"). Attach the current topic from the RECENT
  CONVERSATION, e.g. "what was the unresolved question about <current topic>?".
- A question is only "self-contained" if it names its OWN subject. If answering it correctly would
  REQUIRE knowing the current topic from the conversation, treat it as a follow-up and add the topic.
- IF "mode" is "transform" (a reformatting request like "give BRD" / "functional requirements"),
  set search_query to JUST THE TOPIC/subject being reformatted, taken from the RECENT CONVERSATION
  (e.g. "development environment automatic deployment"). Do NOT include the format keyword itself
  ("BRD", "functional requirements", "user stories", "summary", "technical spec") — that would make
  retrieval match unrelated documents about those formats instead of the actual topic.
- PRESERVE the original meaning and intent. Do NOT answer the question. Do NOT add facts that
  are not in the question or the recent conversation.

Respond with ONLY a JSON object in this exact format:
{
  "intent": "conversations" or "requirements",
  "mode": "retrieve" or "transform",
  "transform_scope": "previous_answer" or "topic" or "none",
  "needs_dialogue": true or false,
  "aggregate": "count" or "list" or "none",
  "verify": true or false,
  "scope": "narrow" or "broad",
  "greeting": "morning" or "afternoon" or "evening" or "hi" or "none",
  "changes": "modified" or "added" or "removed" or "all" or "none",
  "complete_requirements": true or false,
  "topic": "the named feature/subject" or null,
  "search_query": "a standalone, typo-corrected version of the new question",
  "filters": {
     "speaker": "Exact Name if found",
     "session": "Exact Session Name if found",
     "call_date": "YYYY-MM-DD if found"
  }
}
"""

        user_content = f"{history_block}{dates_block}\nNEW QUESTION: {query}"

        # Understanding runs on the STRONG model (config-driven, gpt-5.5) for reliable routing,
        # independent-vs-dependent judgment, and reference resolution on ANY phrasing — no keyword
        # gates. This picks the model only for THIS understanding call; the answer is written separately.
        model = settings.OPENAI_RESOLVER_MODEL

        try:
            call_kwargs = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                "response_format": {"type": "json_object"},
            }
            # GPT-5 / o-series: no custom temperature; reserve room for reasoning + JSON output.
            if self._is_next_gen(model):
                call_kwargs["max_completion_tokens"] = 2000
            else:
                call_kwargs["temperature"] = 0.0

            try:
                response = self.client.chat.completions.create(**call_kwargs)
            except TypeError:
                # Old openai SDK doesn't know 'max_completion_tokens' — retry without it
                # (gpt-5 then uses its default output budget, which is plenty for this JSON).
                call_kwargs.pop("max_completion_tokens", None)
                response = self.client.chat.completions.create(**call_kwargs)
            data = json.loads(response.choices[0].message.content)

            intent = data.get("intent", "requirements")  # default to requirements
            mode = (data.get("mode") or "retrieve").strip().lower()
            if mode not in ("retrieve", "transform"):
                mode = "retrieve"
            transform_scope = (data.get("transform_scope") or "none").strip().lower()
            if transform_scope not in ("previous_answer", "topic", "none") or mode != "transform":
                transform_scope = "none"
            filters = data.get("filters", {}) or {}
            search_query = data.get("search_query") or query  # fall back to raw if absent
            needs_dialogue = bool(data.get("needs_dialogue", False))
            aggregate = (data.get("aggregate") or "none").strip().lower()
            if aggregate not in ("count", "list", "none"):
                aggregate = "none"
            verify = bool(data.get("verify", False))
            scope = (data.get("scope") or "broad").strip().lower()
            if scope not in ("narrow", "broad"):
                scope = "broad"
            greeting = (data.get("greeting") or "none").strip().lower()
            if greeting not in ("morning", "afternoon", "evening", "hi", "none"):
                greeting = "none"
            changes = (data.get("changes") or "none").strip().lower()
            if changes not in ("modified", "added", "removed", "all", "none"):
                changes = "none"
            complete_requirements = bool(data.get("complete_requirements", False))
            # Named subject of the question ("Hospitality Queue", "Carv", ...) or "" when generic.
            topic = data.get("topic")
            topic = topic.strip() if isinstance(topic, str) else ""
            if topic.lower() in ("null", "none"):
                topic = ""

            # Clean up empty filter values
            filters = {k: v for k, v in filters.items() if v}

            logger.info(
                f"Query '{query}' -> search '{search_query}' | mode={mode} needs_dialogue={needs_dialogue} "
                f"routed to {intent} with filters: {filters}"
            )

            return {
                "original_query": query,
                "search_query": search_query,
                "cleaned_query": search_query,  # backward-compat alias
                "intent": intent,
                "mode": mode,
                "transform_scope": transform_scope,
                "needs_dialogue": needs_dialogue,
                "aggregate": aggregate,
                "verify": verify,
                "scope": scope,
                "greeting": greeting,
                "changes": changes,
                "complete_requirements": complete_requirements,
                "topic": topic,
                "filters": filters,
            }
        except Exception as e:
            logger.error(f"Error in analyze_intent: {e}")
            return {
                "original_query": query,
                "search_query": query,       # fall back to the raw query on error
                "cleaned_query": query,
                "intent": "requirements",    # Safe fallback
                "mode": "retrieve",          # default to retrieval on error
                "transform_scope": "none",   # default: not a previous-answer transform
                "needs_dialogue": False,     # default: no dialogue expansion on error
                "aggregate": "none",         # default: not a count/list-all question
                "verify": False,             # default: not a verify/challenge turn
                "scope": "broad",            # default: assume broad (more context is safer)
                "greeting": "none",          # default: no greeting on error
                "changes": "none",           # default: not a change/diff question on error
                "complete_requirements": False,  # default: not a full-requirements-list request
                "topic": "",                 # default: no named subject on error
                "filters": {},
            }
