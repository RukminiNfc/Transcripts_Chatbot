"""
Context Inference Service - Infer context from search results metadata.
No LLM calls - purely deterministic logic based on retrieved data.
"""
from typing import List, Dict, Optional
from collections import Counter
import logging
import re

logger = logging.getLogger(__name__)


class ContextInference:
    """Infer query context (country, topics) from search result metadata"""

    # Standard INTA guide sections used as fallback topic suggestions
    STANDARD_SECTIONS = [
        "Registration Procedure",
        "Opposition",
        "Cancellation",
        "Renewal",
        "Enforcement",
        "Use Requirements",
        "Licensing",
        "Assignment & Transfer",
        "Well-Known Marks",
        "Domain Name Disputes",
    ]

    GUIDE_SUGGESTIONS = [
        {"label": "Choose a country to explore policies", "icon": "🌍", "action": "select_country"},
        {"label": "Compare trademark laws across countries", "icon": "⚖️", "action": "compare"},
        {"label": "Country-wise breakdown", "icon": "📊", "action": "breakdown"},
    ]

    def infer_context(self, search_results: List[Dict], query: str = "") -> Dict:
        """
        Analyze search results to determine context.

        Args:
            search_results: Results from Qdrant search (list of dicts with 'payload' and 'score')
            query: Original user query (for additional heuristics)

        Returns:
            {
                "query_type": "country_specific" | "general",
                "detected_country": str | None,
                "suggested_topics": list[str],
                "guide_suggestions": list[dict] | None,
                "detected_guide_type": str | None
            }
        """
        if not search_results:
            return {
                "query_type": "general",
                "detected_country": None,
                "suggested_topics": [],
                "guide_suggestions": self.GUIDE_SUGGESTIONS,
                "detected_guide_type": None,
            }

        # Extract jurisdictions from top results
        jurisdictions = self._extract_jurisdictions(search_results)
        guide_types = self._extract_guide_types(search_results)
        sections = self._extract_sections(search_results, query)

        # Determine query type based on jurisdiction distribution
        query_type, detected_country = self._classify_query(jurisdictions)

        # Build topic suggestions from section headers
        suggested_topics = self._build_topic_suggestions(sections, detected_country, query)

        # Guide suggestions only for general queries
        guide_suggestions = self.GUIDE_SUGGESTIONS if query_type == "general" else None

        # Detected guide type (most common)
        detected_guide_type = guide_types[0] if guide_types else None

        result = {
            "query_type": query_type,
            "detected_country": detected_country,
            "suggested_topics": suggested_topics,
            "guide_suggestions": guide_suggestions,
            "detected_guide_type": detected_guide_type,
        }

        logger.info(f"Context inference result: type={query_type}, country={detected_country}, "
                     f"topics={len(suggested_topics)}, guide_type={detected_guide_type}")
        return result

    def _extract_jurisdictions(self, results: List[Dict]) -> List[str]:
        """Extract jurisdiction names from search results, ordered by frequency"""
        jurisdictions = []
        for result in results[:10]:  # Top 10 results
            jurisdiction = result.get("payload", {}).get("jurisdiction")
            if jurisdiction:
                jurisdictions.append(jurisdiction)
        return jurisdictions

    def _extract_guide_types(self, results: List[Dict]) -> List[str]:
        """Extract guide types from results, ordered by frequency"""
        guide_types = []
        for result in results[:10]:
            guide_type = result.get("payload", {}).get("guide_type")
            if guide_type:
                guide_types.append(guide_type)

        # Return most common first
        counter = Counter(guide_types)
        return [gt for gt, _ in counter.most_common()]

    def _extract_sections(self, results: List[Dict], query: str) -> List[str]:
        """Extract unique, clean section titles from search results"""
        sections = []
        seen_lower = set()

        for result in results[:10]:
            payload = result.get("payload", {})

            # Try level1_section first (broader topic names)
            for key in ["level1_section", "section_title"]:
                section = payload.get(key)
                if section:
                    clean_section = self._clean_section_title(section)
                    if clean_section and clean_section.lower() not in seen_lower:
                        # Also check if this is a substring of an existing one (avoid duplicates)
                        is_duplicate = any(
                            clean_section.lower() in existing or existing in clean_section.lower()
                            for existing in seen_lower
                        )
                        if not is_duplicate:
                            seen_lower.add(clean_section.lower())
                            sections.append(clean_section)

        return sections

    # Words/phrases to filter out — these are not useful as topic suggestions
    JUNK_SECTIONS = {
        'general', 'introduction', 'overview', 'document header', 'header',
        'footer', 'table of contents', 'contents', 'index', 'appendix',
        'about', 'preface', 'foreword', 'summary', 'conclusion',
        'acknowledgments', 'references', 'bibliography', 'notes',
        'chunking', 'chunk', 'metadata', 'document', 'page',
    }

    def _clean_section_title(self, section: str) -> str:
        """Clean raw section title into a human-readable topic name"""
        if not section:
            return ""

        cleaned = section.strip()

        # Filter out obvious sentences (long and ends with period)
        if len(cleaned) > 50 and cleaned.endswith('.'):
            return ""
        
        # Filter out "Section X of the Act..." type headers which are too verbose
        if re.match(r'^Section \d+ Of The', cleaned, re.IGNORECASE):
            return ""

        # Remove "General — " or "Specific — " prefixes
        cleaned = re.sub(r'^(General|Specific)\s*[—\-–]\s*', '', cleaned)

        # Remove leading letter/number markers: "A.", "B.", "1.", "2.1", "IV.", etc.
        cleaned = re.sub(r'^[A-Z]\.\s*', '', cleaned)
        cleaned = re.sub(r'^[IVXLCDM]+\.\s*', '', cleaned)
        cleaned = re.sub(r'^\d+(\.\d+)*\.?\s*', '', cleaned)

        # Remove sub-section suffixes: " — A. Effect of Decision" → keep only the main part
        cleaned = re.sub(r'\s*[—\-–]\s*[A-Z]\.\s*.+$', '', cleaned)

        # Remove trailing section numbers/markers
        cleaned = re.sub(r'\s*\(\d+\)\s*$', '', cleaned)

        cleaned = cleaned.strip()

        # Filter out "Chunk 1", "Part 2", etc.
        if re.match(r'^(Chunk|Part)\s*\d+$', cleaned, re.IGNORECASE):
            return ""

        # Filter out junk sections
        if len(cleaned) < 3:
            return ""
        if cleaned.lower() in self.JUNK_SECTIONS:
            return ""
            
        # Hard cap on length - topics shouldn't be massive
        if len(cleaned) > 60:
            return ""

        # Title-case for consistent formatting
        # But preserve already-capitalized words (acronyms, proper nouns)
        words = cleaned.split()
        formatted_words = []
        for word in words:
            if word.isupper() and len(word) > 1:
                formatted_words.append(word)  # Keep acronyms like "TM", "IP"
            else:
                formatted_words.append(word.capitalize() if word.islower() else word)
        cleaned = ' '.join(formatted_words)

        return cleaned

    def _classify_query(self, jurisdictions: List[str]) -> tuple:
        """
        Classify query as country-specific or general based on jurisdiction distribution.

        Returns:
            (query_type, detected_country)
        """
        if not jurisdictions:
            return "general", None

        counter = Counter(jurisdictions)
        total = len(jurisdictions)
        most_common_country, most_common_count = counter.most_common(1)[0]

        # If >70% of results are from one country → country-specific
        if most_common_count / total >= 0.7:
            return "country_specific", most_common_country

        # If only 1-2 unique countries but dominant one has >50% → still country-specific
        if len(counter) <= 2 and most_common_count / total > 0.5:
            return "country_specific", most_common_country

        # Otherwise → general (multi-country)
        return "general", None

    SECTION_TO_QUESTION = {
        "registration": "What is the registration procedure",
        "procedure": "What is the procedure",
        "opposition": "What is the opposition process",
        "cancellation": "How does cancellation work",
        "renewal": "What is the renewal process",
        "enforcement": "How can rights be enforced",
        "use requirements": "What are the use requirements",
        "licensing": "What are the rules for licensing",
        "assignment": "How are assignments handled",
        "transfer": "How are transfers handled",
        "well-known": "How are well-known marks protected",
        "domain name": "How are domain name disputes resolved",
        "standing": "Who has standing",
        "grounds": "What are the grounds",
        "evidence": "What evidence is required",
        "appeal": "What is the appeal process",
        "defense": "What defenses are available",
        "remedy": "What remedies are available",
        "cost": "What are the costs",
        "fee": "What are the official fees",
        "time": "What are the timeframes",
        "deadline": "What are the deadlines",
        "document": "What documents are required"
    }

    def _build_topic_suggestions(
        self,
        sections: List[str],
        detected_country: Optional[str],
        query: str
    ) -> List[Dict]:
        """
        Build clean topic suggestions (max 4) with natural questions.
        Returns list of dicts: {'label': 'Topic Name', 'question': 'Natural question...'}
        """
        topics = []
        query_lower = query.lower() if query else ""
        
        # Combine extracted sections and standard sections (fallback)
        candidates = sections[:]
        if len(candidates) < 3:
            for standard in self.STANDARD_SECTIONS:
                if standard not in candidates:
                    candidates.append(standard)

        for topic in candidates:
            # Skip overlaps
            topic_words = set(topic.lower().split())
            query_words = set(query_lower.split())
            if len(topic_words & query_words) > len(topic_words) * 0.5:
                continue

            # Generate natural question
            question = f"Tell me about {topic}" # default
            topic_lower = topic.lower()
            
            # Match against mapping
            for key, template in self.SECTION_TO_QUESTION.items():
                if key in topic_lower:
                    question = template
                    break
            
            # If no match but it's a noun phrase, try "What is/are..."
            if question.startswith("Tell me about"):
                 if topic.endswith('s'):
                     question = f"What are the rules regarding {topic}"
                 else:
                     question = f"What is the rule regarding {topic}"

            topics.append({
                "label": topic,
                "question": question
            })
            
            if len(topics) >= 4:
                break

        return topics[:4]

