from openai import OpenAI
from app.core.config import settings
from typing import List, Dict
import json
import logging

logger = logging.getLogger(__name__)


class Reranker:
    """
    LLM-based re-ranker. Vector search returns a WIDE pool of candidates ranked by raw
    embedding similarity — which lets a loud generic word (e.g. "documentation") pull in
    off-topic chunks. This step re-reads the pool against the user's actual question and
    keeps only the candidates that are genuinely on-subject, best-first.

    Safe by design: on ANY error, or if the model returns nothing usable, it falls back to
    the original top-N so retrieval is never worse than before.
    """

    def __init__(self):
        self.client = OpenAI(api_key=settings.OPENAI_API_KEY)
        self.model = settings.OPENAI_LLM_MODEL

    @staticmethod
    def _is_next_gen(model: str) -> bool:
        return model.lower().startswith(("gpt-5", "o1", "o3", "o4"))

    @staticmethod
    def _candidate_text(payload: Dict) -> str:
        """Compact one-line description of a candidate for the ranking prompt."""
        if 'requirement_text' in payload:
            cat = payload.get('category', '')
            sub = payload.get('sub_category', '')
            return f"[Requirement | {cat} > {sub}] {payload.get('requirement_text', '')}"
        if 'speaker' in payload:
            return f"[Transcript | {payload.get('speaker', '')}] {payload.get('text', '')}"
        return str(payload)[:300]

    def rerank(self, query: str, candidates: List[Dict], top_n: int = 12) -> List[Dict]:
        """
        Return the most relevant candidates to `query`, best-first, at most `top_n`.
        `candidates` are search results ({'payload','score',...}). Falls back to
        candidates[:top_n] on any problem.
        """
        if not candidates:
            return []
        # Nothing to trim — skip the extra call.
        if len(candidates) <= top_n:
            return candidates

        # Clip each candidate so the prompt stays bounded regardless of pool size.
        lines = []
        for i, c in enumerate(candidates):
            text = self._candidate_text(c.get('payload', {}) or {})
            lines.append(f"{i}. {text[:280]}")
        candidate_block = "\n".join(lines)

        system_prompt = (
            "You rank retrieved snippets by how well each ANSWERS the user's question.\n"
            "Judge by the ACTUAL SUBJECT of the question, not by shared generic words.\n"
            "Example: for 'Client Search documentation', a snippet about Client Search is\n"
            "relevant; a snippet that only shares the word 'documentation' but is about an\n"
            "unrelated feature is NOT relevant.\n"
            f"Return the indices of the genuinely relevant snippets, BEST FIRST, at most {top_n}.\n"
            "Keep a snippet only if it truly helps answer THIS question. If many are relevant,\n"
            "return the strongest ones. If NONE are clearly relevant, return the few closest.\n"
            'Respond with ONLY JSON: {"relevant": [<indices in best-first order>]}'
        )
        user_content = f"QUESTION:\n{query}\n\nSNIPPETS:\n{candidate_block}"

        try:
            call_kwargs = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                "response_format": {"type": "json_object"},
            }
            if self._is_next_gen(self.model):
                call_kwargs["max_completion_tokens"] = 1500
            else:
                call_kwargs["temperature"] = 0.0

            try:
                resp = self.client.chat.completions.create(**call_kwargs)
            except TypeError:
                call_kwargs.pop("max_completion_tokens", None)
                resp = self.client.chat.completions.create(**call_kwargs)

            data = json.loads(resp.choices[0].message.content)
            order = data.get("relevant", [])

            # Validate indices, drop dupes/out-of-range, preserve model order.
            seen = set()
            picked: List[Dict] = []
            for idx in order:
                if isinstance(idx, str) and idx.strip().isdigit():
                    idx = int(idx)
                if isinstance(idx, int) and 0 <= idx < len(candidates) and idx not in seen:
                    seen.add(idx)
                    picked.append(candidates[idx])
                if len(picked) >= top_n:
                    break

            if not picked:
                logger.warning("Reranker returned no usable indices; falling back to top-N by score.")
                return candidates[:top_n]

            logger.info(f"Reranked {len(candidates)} candidates -> kept {len(picked)}")
            return picked

        except Exception as e:
            logger.error(f"Rerank failed ({e}); falling back to top-N by score.")
            return candidates[:top_n]
