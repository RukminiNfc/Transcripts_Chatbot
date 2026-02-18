from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct
from qdrant_client.models import Filter, FieldCondition, MatchValue
from app.core.config import settings
from typing import List, Dict, Optional
import logging
import uuid

logger = logging.getLogger(__name__)

class QdrantVectorDB:
    """Qdrant vector database client"""
    
    def __init__(self):
        self.client = QdrantClient(
            host=settings.QDRANT_HOST,
            port=settings.QDRANT_PORT
        )
        self.collection_name = settings.QDRANT_COLLECTION_NAME
        self._ensure_collection()
    
    def _ensure_collection(self):
        """Create collection if it doesn't exist"""
        try:
            collections = self.client.get_collections().collections
            collection_names = [c.name for c in collections]
            
            if self.collection_name not in collection_names:
                logger.info(f"Creating collection: {self.collection_name}")
                self.client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config=VectorParams(
                        size=1536,  # OpenAI embedding dimension
                        distance=Distance.COSINE
                    )
                )
                logger.info("Collection created successfully")
            else:
                logger.info(f"Collection {self.collection_name} already exists")
                
        except Exception as e:
            logger.error(f"Error ensuring collection: {e}")
            raise
    
    def add_vectors(
        self,
        vectors: List[List[float]],
        payloads: List[Dict],
        ids: Optional[List[str]] = None
    ) -> bool:
        """
        Add vectors to collection
        
        Args:
            vectors: List of embedding vectors
            payloads: List of metadata dictionaries
            ids: Optional list of IDs (will generate if not provided)
            
        Returns:
            Success boolean
        """
        try:
            if ids is None:
                ids = [str(uuid.uuid4()) for _ in vectors]
            
            points = [
                PointStruct(
                    id=point_id,
                    vector=vector,
                    payload=payload
                )
                for point_id, vector, payload in zip(ids, vectors, payloads)
            ]
            
            self.client.upsert(
                collection_name=self.collection_name,
                points=points
            )
            
            logger.info(f"Added {len(vectors)} vectors to Qdrant")
            return True
            
        except Exception as e:
            logger.error(f"Error adding vectors: {e}")
            return False
    
    def search(
        self,
        query_vector: List[float],
        limit: int = 5,
        filters: Optional[Dict] = None
    ) -> List[Dict]:
        """
        Search for similar vectors
        
        Args:
            query_vector: Query embedding
            limit: Number of results
            filters: Optional filters (jurisdiction, guide_type, etc.)
            
        Returns:
            List of search results
        """
        try:
            # Build filter conditions
            search_filter = None
            if filters:
                conditions = []
                
                if 'jurisdiction' in filters:
                    conditions.append(
                        FieldCondition(
                            key="jurisdiction",
                            match=MatchValue(value=filters['jurisdiction'])
                        )
                    )
                
                if 'guide_type' in filters:
                    conditions.append(
                        FieldCondition(
                            key="guide_type",
                            match=MatchValue(value=filters['guide_type'])
                        )
                    )
                
                if 'document_id' in filters:
                    conditions.append(
                        FieldCondition(
                            key="document_id",
                            match=MatchValue(value=filters['document_id'])
                        )
                    )
                
                if conditions:
                    search_filter = Filter(must=conditions)
            
            # Perform search
            results = self.client.search(
                collection_name=self.collection_name,
                query_vector=query_vector,
                limit=limit,
                query_filter=search_filter
            )
            
            # Format results
            formatted_results = []
            for result in results:
                formatted_results.append({
                    'id': result.id,
                    'score': result.score,
                    'payload': result.payload
                })
            
            return formatted_results
            
        except Exception as e:
            logger.error(f"Error searching vectors: {e}")
            return []
    
    def delete_by_document_id(self, document_id: str) -> bool:
        """Delete all vectors for a document"""
        try:
            self.client.delete(
                collection_name=self.collection_name,
                points_selector=Filter(
                    must=[
                        FieldCondition(
                            key="document_id",
                            match=MatchValue(value=document_id)
                        )
                    ]
                )
            )
            logger.info(f"Deleted vectors for document: {document_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error deleting vectors: {e}")
            return False
    
    def get_collection_info(self) -> Dict:
        """Get collection statistics"""
        try:
            info = self.client.get_collection(self.collection_name)
            return {
                'vectors_count': info.vectors_count,
                'indexed_vectors_count': info.indexed_vectors_count,
                'points_count': info.points_count
            }
        except Exception as e:
            logger.error(f"Error getting collection info: {e}")
            return {}