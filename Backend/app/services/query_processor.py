from typing import Dict, List
import logging
import re

logger = logging.getLogger(__name__)

class QueryProcessor:
    """Process and enhance queries for better retrieval"""
    
    def __init__(self):
        # Legal/trademark domain keywords for expansion
        self.domain_keywords = {
            'mark': ['trademark', 'mark', 'brand'],
            'use': ['use', 'usage', 'utilization', 'application'],
            'cancellation': ['cancellation', 'revocation', 'invalidation'],
            'invalidity': ['invalidity', 'invalid', 'nullity', 'void'],
            'registration': ['registration', 'filing', 'application'],
            'opposition': ['opposition', 'objection', 'challenge'],
            'renewal': ['renewal', 'maintenance', 'extension'],
            'infringement': ['infringement', 'violation', 'unauthorized use'],
        }
        
        # Question type patterns
        self.question_patterns = {
            'timeframe': r'\b(how (many|long)|within|timeframe|period|years?|months?|days?)\b',
            'definition': r'\b(what (is|does|are)|define|definition|means?|meaning)\b',
            'procedure': r'\b(how to|can (i|we|one)|process|procedure|steps?)\b',
            'requirement': r'\b(must|required|requirement|necessary|need to)\b',
            'online': r'\b(online|electronically|digital|internet|web)\b',
        }
    
    def detect_question_type(self, query: str) -> List[str]:
        """Detect the type of question being asked"""
        query_lower = query.lower()
        detected_types = []
        
        for qtype, pattern in self.question_patterns.items():
            if re.search(pattern, query_lower):
                detected_types.append(qtype)
        
        return detected_types if detected_types else ['general']
    
    def expand_query(self, query: str) -> str:
        """
        Expand short queries with domain-relevant terms
        
        Args:
            query: Original user query
            
        Returns:
            Expanded query for better semantic matching
        """
        query_lower = query.lower()
        
        # Detect question types
        question_types = self.detect_question_type(query)
        
        # Check if query is very short (likely needs expansion)
        word_count = len(query.split())
        
        # For short queries (< 8 words), add context
        if word_count < 8:
            expanded_parts = [query]
            
            # Add domain context based on keywords found
            for keyword, synonyms in self.domain_keywords.items():
                if keyword in query_lower:
                    # Add related terms
                    expanded_parts.append(f"trademark {keyword}")
            
            # Add question-type specific context
            if 'timeframe' in question_types:
                expanded_parts.append("time period requirement deadline")
            
            if 'definition' in question_types:
                expanded_parts.append("definition meaning explanation")
            
            if 'procedure' in question_types:
                expanded_parts.append("process procedure steps how to")
            
            if 'online' in question_types:
                expanded_parts.append("electronic filing online submission digital")
            
            expanded_query = " ".join(expanded_parts)
            logger.info(f"Expanded query from '{query}' to '{expanded_query}'")
            return expanded_query
        
        # For longer queries, return as-is
        return query
    
    def preprocess_query(self, query: str) -> Dict[str, any]:
        """
        Preprocess query and return metadata
        
        Returns:
            Dictionary with:
            - original_query: Original query
            - expanded_query: Expanded version for search
            - question_types: Detected question types
            - is_short: Whether query is short (< 8 words)
        """
        question_types = self.detect_question_type(query)
        word_count = len(query.split())
        is_short = word_count < 8
        expanded_query = self.expand_query(query)
        
        result = {
            'original_query': query,
            'expanded_query': expanded_query,
            'question_types': question_types,
            'is_short': is_short,
            'word_count': word_count
        }
        
        logger.info(f"Query preprocessing: {result}")
        return result
