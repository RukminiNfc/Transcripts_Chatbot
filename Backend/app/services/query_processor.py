from typing import Dict, Any, List, Optional
import json
import logging
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
  "needs_dialogue": true or false,
  "aggregate": "count" or "list" or "none",
  "search_query": "a standalone, typo-corrected version of the new question",
  "filters": {
     "speaker": "Exact Name if found",
     "session": "Exact Session Name if found",
     "call_date": "YYYY-MM-DD if found"
  }
}
"""

        user_content = f"{history_block}{dates_block}\nNEW QUESTION: {query}"

        try:
            call_kwargs = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                "response_format": {"type": "json_object"},
            }
            # GPT-5 / o-series: no custom temperature; reserve room for reasoning + JSON output.
            if self._is_next_gen(self.model):
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
            filters = data.get("filters", {}) or {}
            search_query = data.get("search_query") or query  # fall back to raw if absent
            needs_dialogue = bool(data.get("needs_dialogue", False))
            aggregate = (data.get("aggregate") or "none").strip().lower()
            if aggregate not in ("count", "list", "none"):
                aggregate = "none"

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
                "needs_dialogue": needs_dialogue,
                "aggregate": aggregate,
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
                "needs_dialogue": False,     # default: no dialogue expansion on error
                "aggregate": "none",         # default: not a count/list-all question
                "filters": {},
            }
