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
        self.conversations_collection = "conversations"
        self.requirements_collection = "requirements"
        
        self.ensure_collection(self.conversations_collection)
        self.ensure_collection(self.requirements_collection)
    
    def ensure_collection(self, collection_name: str):
        """Create collection if it doesn't exist"""
        try:
            collections = self.client.get_collections().collections
            collection_names = [c.name for c in collections]
            
            if collection_name not in collection_names:
                logger.info(f"Creating collection: {collection_name}")
                self.client.create_collection(
                    collection_name=collection_name,
                    vectors_config=VectorParams(
                        size=1536,  # OpenAI embedding dimension
                        distance=Distance.COSINE
                    )
                )
                logger.info(f"Collection {collection_name} created successfully")
            else:
                logger.info(f"Collection {collection_name} already exists")
                
        except Exception as e:
            logger.error(f"Error ensuring collection {collection_name}: {e}")
            raise
    
    def add_vectors(
        self,
        vectors: List[List[float]],
        payloads: List[Dict],
        ids: Optional[List[str]] = None,
        collection_name: str = "requirements"
    ) -> bool:
        """
        Add vectors to collection
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
                collection_name=collection_name,
                points=points
            )
            
            logger.info(f"Added {len(vectors)} vectors to Qdrant collection {collection_name}")
            return True
            
        except Exception as e:
            logger.error(f"Error adding vectors to {collection_name}: {e}")
            return False
    
    def search(
        self,
        query_vector: List[float],
        limit: int = 5,
        filters: Optional[Dict] = None,
        collection_name: str = "requirements"
    ) -> List[Dict]:
        """
        Search for similar vectors
        """
        try:
            # Build filter conditions
            search_filter = None
            if filters:
                conditions = []
                
                # Support old fields and new fields dynamically
                for key, value in filters.items():
                    if value is not None:
                        conditions.append(
                            FieldCondition(
                                key=key,
                                match=MatchValue(value=value)
                            )
                        )
                
                if conditions:
                    search_filter = Filter(must=conditions)
            
            # Perform search
            results = self.client.search(
                collection_name=collection_name,
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
            logger.error(f"Error searching vectors in {collection_name}: {e}")
            return []
    
    def get_collection_info(self, collection_name: str = "requirements") -> Dict:
        """Get collection statistics"""
        try:
            info = self.client.get_collection(collection_name)
            return {
                'vectors_count': info.vectors_count,
                'indexed_vectors_count': info.indexed_vectors_count,
                'points_count': info.points_count
            }
        except Exception as e:
            logger.error(f"Error getting collection info for {collection_name}: {e}")
            return {}

    def delete_by_filter(self, collection_name: str, filters: Dict) -> bool:
        """Delete all vectors in a collection that match the given payload filter.
        
        Example: delete_by_filter("conversations", {"transcript_id": "abc-123"})
        """
        try:
            conditions = [
                FieldCondition(key=k, match=MatchValue(value=v))
                for k, v in filters.items()
                if v is not None
            ]
            if not conditions:
                logger.warning("delete_by_filter called with empty filters — skipping.")
                return False

            self.client.delete(
                collection_name=collection_name,
                points_selector=Filter(must=conditions)
            )
            logger.info(f"Deleted vectors matching {filters} from '{collection_name}'")
            return True
        except Exception as e:
            logger.error(f"Error deleting by filter from '{collection_name}': {e}")
            return False

    def delete_by_ids(self, collection_name: str, point_ids: List[str]) -> bool:
        """Delete specific vectors by their point IDs.
        
        Example: delete_by_ids("requirements", ["uuid-1", "uuid-2"])
        """
        try:
            if not point_ids:
                logger.info("delete_by_ids called with empty list — nothing to delete.")
                return True
            self.client.delete(
                collection_name=collection_name,
                points_selector=point_ids
            )
            logger.info(f"Deleted {len(point_ids)} vectors from '{collection_name}'")
            return True
        except Exception as e:
            logger.error(f"Error deleting point IDs from '{collection_name}': {e}")
            return False