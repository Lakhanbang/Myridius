import os
import sys
import uuid
import hashlib
import litellm
import pymupdf4llm
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct, Filter, FieldCondition, MatchValue, SparseVectorParams, SparseIndexParams, SparseVector
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter
from fastembed import SparseTextEmbedding

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
from litellm_router.config import AppConfig
from litellm_router.protections.budget_tracking import BudgetTracker

load_dotenv()

QDRANT_URL = os.getenv("QDRANT_URL")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")
COLLECTION_NAME = "company_knowledge"
KNOWLEDGEBASE_DIR = os.path.join(os.path.dirname(__file__), "Knowledgebase")

def setup_qdrant() -> QdrantClient:
    """Connect to Qdrant and create the collection if it doesn't exist."""
    print(f"Connecting to Qdrant Cloud at {QDRANT_URL}...")
    client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY, timeout=60.0)
    
    if not client.collection_exists(COLLECTION_NAME):
        print(f"Creating collection '{COLLECTION_NAME}' for OpenAI and BM25 Hybrid Search...")
        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config={
                "dense": VectorParams(size=1536, distance=Distance.COSINE)
            },
            sparse_vectors_config={
                "sparse": SparseVectorParams(
                    index=SparseIndexParams(
                        on_disk=False,
                    )
                )
            }
        )
        print(f"Collection '{COLLECTION_NAME}' created.")
    else:
        print(f"Collection '{COLLECTION_NAME}' already exists. We will append new data.")
        
    try:
        for field in ["source", "doc_title", "header_path", "page_number"]:
            client.create_payload_index(
                collection_name=COLLECTION_NAME,
                field_name=field,
                field_schema="keyword"
            )
    except Exception as e:
        pass
        
    return client

HEADERS_TO_SPLIT = [("#", "H1"), ("##", "H2"), ("###", "H3"), ("####", "H4")]

def chunk_markdown(pages: list[dict]) -> list[dict]:
    """Group text under its headers; only character-split oversized sections."""
    header_splitter = MarkdownHeaderTextSplitter(HEADERS_TO_SPLIT, strip_headers=True)
    char_splitter = RecursiveCharacterTextSplitter(
        chunk_size=800,
        chunk_overlap=150,
        separators=["\n\n", "\n", ". ", "? ", "! ", " ", ""]
    )
    chunks = []
    for page in pages:
        for doc in header_splitter.split_text(page["text"]):
            header_path = " > ".join(v for _, v in sorted(doc.metadata.items()))
            text = doc.page_content
            page_no = page["metadata"].get("page", 0)
            if len(text) > 800:
                for sub in char_splitter.split_text(text):
                    chunks.append({"text": sub, "header_path": header_path, "page": page_no})
            else:
                chunks.append({"text": text, "header_path": header_path, "page": page_no})
    return chunks

def embed_and_track_cost(chunk: str, tracker: BudgetTracker) -> tuple[list[float], float, int]:
    """Call LiteLLM for embeddings, track cost and tokens."""
    model_name = "text-embedding-3-small"
    response = litellm.embedding(model=model_name, input=[chunk])
    cost = litellm.completion_cost(completion_response=response)
    tokens = response.usage.total_tokens if response.usage else 0
    if tracker:
        tracker.record_cost(cost)
    vector = response.data[0]["embedding"]
    return vector, cost, tokens

def get_file_hash(filepath: str) -> str:
    """Returns the MD5 hash of the file contents."""
    hash_md5 = hashlib.md5()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()

def check_document_exists(client: QdrantClient, filename: str, file_hash: str) -> bool:
    """Check if the exact file hash exists in Qdrant for this filename."""
    results = client.scroll(
        collection_name=COLLECTION_NAME,
        scroll_filter=Filter(
            must=[FieldCondition(key="source", match=MatchValue(value=filename))]
        ),
        limit=1,
        with_payload=True,
        with_vectors=False
    )[0]
    
    if not results:
        return False
        
    # Check if the stored hash matches the current file's hash
    stored_hash = results[0].payload.get("file_hash")
    return stored_hash == file_hash

def delete_document(client: QdrantClient, filename: str):
    """Delete all chunks for a specific document."""
    client.delete(
        collection_name=COLLECTION_NAME,
        points_selector=Filter(
            must=[FieldCondition(key="source", match=MatchValue(value=filename))]
        )
    )

def ingest_document(client: QdrantClient, sparse_model, tracker: BudgetTracker, filepath: str, filename: str) -> dict:
    """Ingest a single document, checking if it already exists or changed."""
    file_hash = get_file_hash(filepath)
    
    if check_document_exists(client, filename, file_hash):
        print(f"Skipping {filename}: Already exists and contents are unchanged.")
        return {"status": "skipped", "reason": "already_exists", "cost": 0.0}
        
    # If the file exists but hash changed, delete the old chunks first
    delete_document(client, filename)
    
    print(f"Processing new/updated file: {filename}...")
    try:
        pages = pymupdf4llm.to_markdown(filepath, page_chunks=True)
    except Exception as e:
        print(f"Failed to open {filename}: {e}")
        return {"status": "error", "reason": "invalid_pdf", "cost": 0.0}
        
    if not pages or not any(p["text"].strip() for p in pages):
        return {"status": "error", "reason": "empty_text", "cost": 0.0}
        
    chunks = chunk_markdown(pages)
    sparse_embeddings = list(sparse_model.embed([c["text"] for c in chunks]))
    
    points = []
    total_cost = 0.0
    total_tokens = 0
    for i, chunk in enumerate(chunks):
        vector, cost, tokens = embed_and_track_cost(chunk["text"], tracker)
        total_cost += cost
        total_tokens += tokens
        sparse_emb = sparse_embeddings[i]
        
        point_id = str(uuid.uuid4())
        payload = {
            "source": filename,
            "doc_title": filename.replace(".pdf", ""),
            "chunk_index": i,
            "text": chunk["text"],
            "header_path": chunk["header_path"],
            "page_number": chunk["page"],
            "file_hash": file_hash
        }
        
        vector_dict = {
            "dense": vector,
            "sparse": SparseVector(
                indices=sparse_emb.indices.tolist(),
                values=sparse_emb.values.tolist()
            )
        }
        
        points.append(PointStruct(id=point_id, vector=vector_dict, payload=payload))
        
    if points:
        batch_size = 10
        for i in range(0, len(points), batch_size):
            batch = points[i:i + batch_size]
            client.upsert(
                collection_name=COLLECTION_NAME,
                points=batch
            )
        print(f"Uploaded {len(points)} chunks for {filename} in batches of {batch_size}.")
        
    return {"status": "success", "chunks": len(points), "cost": total_cost, "tokens": total_tokens}


def main():
    """Default behavior: Loop over Knowledgebase directory to mimic original behavior."""
    AppConfig.initialize_environment()
    tracker = BudgetTracker()
    print("Loading BM25 Sparse Model...")
    sparse_model = SparseTextEmbedding(model_name="Qdrant/bm25")
    client = setup_qdrant()
    
    if not os.path.exists(KNOWLEDGEBASE_DIR):
        os.makedirs(KNOWLEDGEBASE_DIR, exist_ok=True)
        
    pdf_files = [f for f in os.listdir(KNOWLEDGEBASE_DIR) if f.lower().endswith(".pdf")]
    print(f"\nFound {len(pdf_files)} PDFs in Knowledgebase.")
    
    total_cost = 0.0
    for filename in pdf_files:
        filepath = os.path.join(KNOWLEDGEBASE_DIR, filename)
        result = ingest_document(client, sparse_model, tracker, filepath, filename)
        total_cost += result.get("cost", 0.0)
        
    print(f"\n--- Batch Ingestion Complete ---")
    print(f"Total Embedding Cost: ${total_cost:.6f} (Logged to Redis)")

if __name__ == "__main__":
    main()
