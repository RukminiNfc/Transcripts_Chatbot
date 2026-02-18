from qdrant_client import QdrantClient

client = QdrantClient(host="localhost", port=6333)

# 1. List collections (SAFE)
print("Collections:", client.get_collections())

# 2. Count points via scroll (SAFE & RELIABLE)
points, _ = client.scroll(
    collection_name="vector_documents",
    limit=10
)

print("Number of stored vectors:", len(points))
