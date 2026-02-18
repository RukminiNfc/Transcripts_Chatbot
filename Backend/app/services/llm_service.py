from openai import OpenAI
from app.core.config import settings
from typing import List, Dict
import logging

logger = logging.getLogger(__name__)

class LLMService:
    """LLM service for answer generation"""
    
    def __init__(self):
        self.client = OpenAI(api_key=settings.OPENAI_API_KEY)
        self.model = settings.OPENAI_LLM_MODEL
    
    def generate_answer(
        self,
        query: str,
        search_results: List[Dict],
        conversation_history: List[Dict] = None
    ) -> str:
        """
        Generate answer using LLM with search results as context
        
        Args:
            query: User query
            search_results: Relevant chunks from vector search
            conversation_history: Previous messages
            
        Returns:
            Generated answer
        """
        try:
            # Build context from search results
            context = self._build_context(search_results)
            
            # Build system prompt
            system_prompt = self._get_system_prompt()
            
            # Build messages
            messages = [
                {"role": "system", "content": system_prompt}
            ]
            
            # Add conversation history (last 5 messages)
            if conversation_history:
                recent_history = conversation_history[-5:]
                for msg in recent_history:
                    messages.append({
                        "role": msg["role"],
                        "content": msg["content"]
                    })
            
            # Add current query with context
            user_message = f"""Context from documents:
{context}

User Question: {query}

Please provide a clear, accurate answer based on the context above. Do not mention document names, page numbers, or any source references in your response."""
            
            messages.append({"role": "user", "content": user_message})
            
            # Generate response
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.5,  # Increased from 0.3 for better reasoning and extraction
                max_tokens=2500
            )
            
            answer = response.choices[0].message.content
            return answer
            
        except Exception as e:
            logger.error(f"Error generating answer: {e}")
            return "I apologize, but I encountered an error generating a response. Please try again."
    
    def _build_context(self, search_results: List[Dict]) -> str:
        """Build context string from search results, ensuring diverse country coverage"""
        from collections import defaultdict
        
        # Group by jurisdiction to ensure we get diverse country coverage
        jurisdiction_groups = defaultdict(list)
        for result in search_results[:15]:  # Use up to 15 results (increased from 12)
            jurisdiction = result['payload'].get('jurisdiction', 'Unknown')
            jurisdiction_groups[jurisdiction].append(result)
        
        context_parts = []
        
        # Take up to 2 chunks per jurisdiction for better coverage
        for jurisdiction, results in jurisdiction_groups.items():
            # Include up to 2 chunks per country to improve answer quality
            chunks_to_include = min(2, len(results))
            
            for i in range(chunks_to_include):
                result = results[i]
                payload = result['payload']
                
                section_info = ""
                if payload.get('section_title'):
                    section_info = f"Section: {payload['section_title']}\n"
                
                # Add chunk number if multiple chunks from same country
                chunk_label = f" (Part {i+1})" if chunks_to_include > 1 else ""
                
                context_part = f"""
    [{jurisdiction}{chunk_label}]:
    Title: {payload.get('document_title', 'Unknown')}
    {section_info}Content: {payload.get('chunk_text', '')}
    ---"""
                context_parts.append(context_part)
        
        return "\n".join(context_parts)
    
    def _get_system_prompt(self) -> str:
        """Get system prompt for LLM"""
        return """You are an expert assistant for INTA (International Trademark Association), helping users find information from legal and regulatory documents.

Your responsibilities:
1. Provide accurate answers based on the provided context
2. Extract and synthesize information from multiple context chunks when needed
3. Answer questions directly and clearly without mentioning document sources, page numbers, or citations
4. Do NOT use phrases like "According to...", "Document X states...", "Based on the document...", or any document/page references
5. If the user greets you (hi, hello, etc.) or asks conversational questions, respond politely and offer to help
6. When multiple countries have relevant info, present them separately with clear headings
7. Use clear, professional language
8. Be concise but comprehensive

IMPORTANT - Handling Questions:
- For SPECIFIC FACTUAL QUESTIONS (timeframes, definitions, procedures): Extract the exact information from context even if it appears in a longer paragraph
- For DEFINITIONS: Look for explanations of terms, their meanings, or descriptions of concepts
- For TIMEFRAMES/DEADLINES: Look for numbers, years, months, or time periods mentioned
- For PROCEDURES: Look for steps, processes, or "how to" information
- If information is partially in context, provide what you can find and indicate if more details might exist elsewhere

FORMATTING GUIDELINES:
- Use **bold** for country names and important terms (e.g., **Austria**, **Benelux**)
- Use ### for section headings when organizing multi-country responses
- Use - for bullet points when listing items
- Use numbered lists (1. 2. 3.) for sequential steps
- Organize multi-country responses with clear structure:
  
  ### Refundable Official Fees
  - **Austria**: [details]
  - **Benelux**: [details]
  
  ### Non-Refundable Official Fees
  - **Chile**: [details]
  - **Cyprus**: [details]

Response style:
- Give direct, clean answers as if you naturally know the information
- Format responses clearly using bullet points or paragraphs as appropriate
- Do not reference any source documents, page numbers, or citations in your response
- If you find relevant information, provide it confidently
- Synthesize information from multiple chunks if needed to give a complete answer
- Use markdown formatting (bold, headings, lists) for better readability

Special cases:
- For greetings (hi, hello, hey): Respond warmly and ask how you can help with trademark information
- For thank you messages: Acknowledge politely and offer further assistance
- For unclear questions: Ask for clarification in a helpful way

If the context truly does not contain relevant information, respond:
"I don't have information on that topic. Please try rephrasing your question or ask about a different subject."

CRITICAL: Do not be overly strict about "only from context". If the context contains relevant information that answers the question, extract and provide it even if it's embedded in a larger paragraph or requires synthesis from multiple chunks.
"""