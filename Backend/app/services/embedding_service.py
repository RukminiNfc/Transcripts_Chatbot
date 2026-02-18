from openai import OpenAI
from app.core.config import settings
from typing import List, Union
import logging

logger = logging.getLogger(__name__)

class EmbeddingService:
    """Generate embeddings using OpenAI API"""
    
    def __init__(self):
        self.client = OpenAI(api_key=settings.OPENAI_API_KEY)
        self.model = settings.OPENAI_EMBEDDING_MODEL
    
    def generate_embedding(self, text: str) -> List[float]:
        """
        Generate embedding for single text
        
        Args:
            text: Input text
            
        Returns:
            Embedding vector (1536 dimensions)
        """
        try:
            response = self.client.embeddings.create(
                model=self.model,
                input=text
            )
            
            embedding = response.data[0].embedding
            return embedding
            
        except Exception as e:
            logger.error(f"Error generating embedding: {e}")
            raise
    
    def generate_embeddings(self, texts: List[str]) -> List[List[float]]:
        """
        Generate embeddings for multiple texts (batch)
        
        Args:
            texts: List of input texts
            
        Returns:
            List of embedding vectors
        """
        try:
            # OpenAI allows up to 2048 inputs per request
            batch_size = 100
            all_embeddings = []
            
            for i in range(0, len(texts), batch_size):
                batch = texts[i:i+batch_size]
                
                response = self.client.embeddings.create(
                    model=self.model,
                    input=batch
                )
                
                batch_embeddings = [item.embedding for item in response.data]
                all_embeddings.extend(batch_embeddings)
                
                logger.info(f"Generated embeddings for batch {i//batch_size + 1}")
            
            return all_embeddings
            
        except Exception as e:
            logger.error(f"Error generating embeddings: {e}")
            raise