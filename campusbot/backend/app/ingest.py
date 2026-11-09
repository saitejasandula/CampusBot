import os
import hashlib
from pathlib import Path
from typing import List, Tuple, Dict, Any
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_chroma import Chroma
from langchain_core.embeddings import Embeddings
from app.config import settings

# Force offline mode for local HF model to avoid network retry overhead
os.environ["HF_HUB_OFFLINE"] = "1"

class LocalHuggingFaceEmbeddings(Embeddings):
    """Local SentenceTransformer embeddings wrapper for LangChain."""
    _model = None

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        if LocalHuggingFaceEmbeddings._model is None:
            from sentence_transformers import SentenceTransformer
            LocalHuggingFaceEmbeddings._model = SentenceTransformer(model_name, local_files_only=True)
        self.model = LocalHuggingFaceEmbeddings._model

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        embeddings = self.model.encode(texts, show_progress_bar=False, normalize_embeddings=True)
        return embeddings.tolist()

    def embed_query(self, text: str) -> List[float]:
        embedding = self.model.encode(text, show_progress_bar=False, normalize_embeddings=True)
        return embedding.tolist()

_embedding_instance = None

def get_embedding_function() -> Embeddings:
    """
    Singleton getter for embeddings:
    - If EMBEDDING_PROVIDER == "gemini" and GOOGLE_API_KEY is available:
      uses GoogleGenerativeAIEmbeddings (0MB local RAM, fits in Render 512MB limit).
    - Otherwise (or if provider is "local"):
      uses LocalHuggingFaceEmbeddings ("all-MiniLM-L6-v2").
    """
    global _embedding_instance
    if _embedding_instance is not None:
        return _embedding_instance

    provider = settings.EMBEDDING_PROVIDER.lower().strip()
    api_key = settings.GOOGLE_API_KEY or os.environ.get("GOOGLE_API_KEY", "")

    if provider == "gemini" and api_key and not api_key.startswith("your_"):
        try:
            from langchain_google_genai import GoogleGenerativeAIEmbeddings
            model_name = settings.EMBEDDING_MODEL or "models/text-embedding-004"
            print(f"[Embeddings] Initializing Gemini Cloud Embeddings ({model_name})...")
            _embedding_instance = GoogleGenerativeAIEmbeddings(
                model=model_name,
                google_api_key=api_key
            )
            return _embedding_instance
        except Exception as e:
            print(f"[Embeddings] Failed to initialize Gemini embeddings: {e}. Falling back to local MiniLM.")

    print("[Embeddings] Initializing Local SentenceTransformer Embeddings (all-MiniLM-L6-v2)...")
    _embedding_instance = LocalHuggingFaceEmbeddings()
    return _embedding_instance

def get_collection_name() -> str:
    """Distinct collection name per provider to avoid vector dimension collisions (384 vs 768)."""
    provider = settings.EMBEDDING_PROVIDER.lower().strip()
    return f"campusbot_{provider}"

def load_documents_from_dir(docs_dir: str) -> List[Document]:
    """Load all .md, .txt, and .pdf documents from docs_dir."""
    p = Path(docs_dir)
    if not p.exists():
        p.mkdir(parents=True, exist_ok=True)
        return []

    documents: List[Document] = []
    
    for file_path in p.glob("*.*"):
        ext = file_path.suffix.lower()
        if ext in [".md", ".txt"]:
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    text = f.read()
                if text.strip():
                    documents.append(
                        Document(
                            page_content=text,
                            metadata={"source": file_path.name, "page": 1}
                        )
                    )
            except Exception as e:
                print(f"[Ingest] Error reading text file {file_path.name}: {e}")
        elif ext == ".pdf":
            try:
                from langchain_community.document_loaders import PyPDFLoader
                loader = PyPDFLoader(str(file_path))
                pdf_docs = loader.load()
                for d in pdf_docs:
                    d.metadata["source"] = file_path.name
                documents.extend(pdf_docs)
            except Exception as e:
                print(f"[Ingest] PDF loader skipped for {file_path.name}: {e}")

    return documents

def run_ingestion(docs_dir: str = None, persist_dir: str = None) -> Tuple[int, List[str]]:
    """
    Split docs and upsert chunks to ChromaDB with deterministic IDs to avoid duplicates.
    Returns (num_chunks_indexed, list_of_filenames).
    """
    docs_path = docs_dir or settings.DOCS_DIR
    chroma_path = persist_dir or settings.CHROMA_PERSIST_DIR
    
    raw_docs = load_documents_from_dir(docs_path)
    if not raw_docs:
        print("[Ingest] No documents found to ingest.")
        return 0, []

    # Text splitter with chunk_size ~800, overlap ~100
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=800,
        chunk_overlap=100,
        separators=["\n\n", "\n", " ", ""]
    )
    
    split_docs = splitter.split_documents(raw_docs)
    
    # Generate deterministic IDs based on source file and chunk index
    ids: List[str] = []
    file_counters: Dict[str, int] = {}
    processed_files = set()
    
    for doc in split_docs:
        source = doc.metadata.get("source", "unknown")
        processed_files.add(source)
        file_counters[source] = file_counters.get(source, 0) + 1
        chunk_idx = file_counters[source]
        doc.metadata["chunk_index"] = chunk_idx
        # Deterministic unique ID prevents duplicate entries on re-ingestion
        doc_id = f"{source}#chunk_{chunk_idx}"
        ids.append(doc_id)

    embedding_fn = get_embedding_function()
    collection_name = get_collection_name()
    
    # Initialize persistent Chroma collection
    Path(chroma_path).mkdir(parents=True, exist_ok=True)
    
    vectorstore = Chroma(
        collection_name=collection_name,
        embedding_function=embedding_fn,
        persist_directory=chroma_path
    )
    
    # Reset existing collection documents if re-running to ensure perfect mirror of docs folder
    try:
        existing = vectorstore.get()
        if existing and existing.get("ids"):
            vectorstore.delete(ids=existing["ids"])
    except Exception as e:
        print(f"[Ingest] Note during collection reset: {e}")

    vectorstore.add_documents(documents=split_docs, ids=ids)
    print(f"[Ingest] Successfully indexed {len(split_docs)} chunks from {len(processed_files)} files into '{collection_name}'.")
    return len(split_docs), sorted(list(processed_files))

if __name__ == "__main__":
    count, files = run_ingestion()
    print(f"Ingestion complete. Total chunks: {count}, Files: {files}")
