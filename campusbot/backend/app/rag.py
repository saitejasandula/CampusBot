import os
from typing import List, Tuple, Dict, Any, AsyncGenerator, Optional
from langchain_chroma import Chroma
from langchain_core.prompts import PromptTemplate
from app.config import settings
from app.ingest import get_embedding_function, get_collection_name
from app.memory import check_greeting_or_smalltalk, rewrite_query_with_history

# Global vectorstore reference
_vectorstore = None

def get_vectorstore() -> Chroma:
    """Retrieve or initialize persistent Chroma vectorstore."""
    global _vectorstore
    if _vectorstore is None:
        _vectorstore = Chroma(
            collection_name=get_collection_name(),
            embedding_function=get_embedding_function(),
            persist_directory=settings.CHROMA_PERSIST_DIR
        )
    return _vectorstore

def get_llm(streaming: bool = True):
    """
    Initialize ChatGoogleGenerativeAI if GOOGLE_API_KEY is available.
    Returns None if API key is not configured.
    """
    api_key = settings.GOOGLE_API_KEY or os.environ.get("GOOGLE_API_KEY", "")
    if not api_key or api_key.startswith("your_"):
        return None

    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
        llm = ChatGoogleGenerativeAI(
            model=settings.MODEL_NAME,
            google_api_key=api_key,
            temperature=0.2,
            streaming=streaming
        )
        return llm
    except Exception as e:
        print(f"[RAG] Failed to initialize Gemini LLM: {e}")
        return None

RAG_PROMPT_TEMPLATE = """You are CampusBot, the official AI helpdesk assistant for Greenfield Institute of Technology (GIT).
Answer the student's question accurately and helpfully, using ONLY the facts provided in the Context below.
- Do not speculate, guess, or extrapolate beyond what is explicitly stated in the context.
- If the context does not contain enough information to answer the question, clearly say: "I couldn't find this in the college documents."
- Provide concrete numbers, dates, requirements, or steps if present in the context.
- Speak in a polite, professional, and direct tone.

Context:
{context}

Question: {question}

Helpful Answer:"""

FALLBACK_MESSAGE = (
    "I couldn't find this in the college documents. "
    "If you need further assistance with this topic, please use the 'Talk to a human' option below to submit a ticket to our campus advisory team."
)

def retrieve_top_chunks(query: str, k: int = 4) -> List[Tuple[Any, float, float]]:
    """
    Retrieve top-k chunks from Chroma with distance and cosine similarity scores.
    Returns list of (Document, distance, cosine_similarity).
    """
    vs = get_vectorstore()
    results = vs.similarity_search_with_score(query, k=k)
    
    scored_results = []
    for doc, dist in results:
        # For normalized embeddings with squared Euclidean distance:
        # cosine_sim = 1.0 - (dist / 2.0)
        cosine_sim = max(0.0, min(1.0, 1.0 - (dist / 2.0)))
        scored_results.append((doc, dist, cosine_sim))
    
    return scored_results

def build_context_string(docs_and_scores: List[Tuple[Any, float, float]]) -> Tuple[str, List[str]]:
    """Format retrieved documents into a context block and extract unique source files."""
    context_parts = []
    sources = set()
    
    for doc, _, _ in docs_and_scores:
        src = doc.metadata.get("source", "document")
        sources.add(src)
        context_parts.append(f"--- Document: {src} ---\n{doc.page_content.strip()}")
        
    return "\n\n".join(context_parts), sorted(list(sources))

async def generate_rag_answer_stream(
    query: str,
    session_id: str,
    history: List[Dict[str, str]]
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Main RAG pipeline supporting streaming tokens:
    1. Detects small talk / greetings.
    2. Rewrites follow-up questions using conversation history.
    3. Retrieves top 4 chunks.
    4. Evaluates similarity against SIMILARITY_THRESHOLD.
    5. Streams answer tokens from Gemini.
    6. Yields final metadata event with sources and status.
    """
    # 1. Check for greeting or small talk
    small_talk_reply = check_greeting_or_smalltalk(query)
    if small_talk_reply:
        # Stream small talk response
        words = small_talk_reply.split(" ")
        for i, word in enumerate(words):
            token = word + (" " if i < len(words) - 1 else "")
            yield {"type": "token", "content": token}
        yield {
            "type": "final",
            "sources": [],
            "is_fallback": False,
            "standalone_query": query
        }
        return

    # 2. Query rewriting using history
    llm = get_llm(streaming=True)
    standalone_query = rewrite_query_with_history(query, history, llm=llm)

    # 3. Retrieve top-4 chunks
    scored_chunks = retrieve_top_chunks(standalone_query, k=4)
    
    if not scored_chunks:
        # Vectorstore empty
        yield {"type": "token", "content": FALLBACK_MESSAGE}
        yield {
            "type": "final",
            "sources": [],
            "is_fallback": True,
            "standalone_query": standalone_query
        }
        return

    best_doc, best_dist, best_similarity = scored_chunks[0]

    # 4. Out-of-scope fallback check
    if best_similarity < settings.SIMILARITY_THRESHOLD:
        yield {"type": "token", "content": FALLBACK_MESSAGE}
        yield {
            "type": "final",
            "sources": [],
            "is_fallback": True,
            "standalone_query": standalone_query
        }
        return

    # Filter chunks above a reasonable minimum relative to best
    valid_chunks = [c for c in scored_chunks if c[2] >= (settings.SIMILARITY_THRESHOLD - 0.15)]
    context_str, sources = build_context_string(valid_chunks)

    # 5. Call LLM for generation
    if llm is None:
        # If API key is not configured, provide a concise offline extraction
        mock_reply = (
            f"[CampusBot Offline Notice: GOOGLE_API_KEY not configured in .env]\n\n"
            f"Here is the most relevant information retrieved from {sources[0]}:\n\n"
            f"{best_doc.page_content[:450]}..."
        )
        for word in mock_reply.split(" "):
            yield {"type": "token", "content": word + " "}
        yield {
            "type": "final",
            "sources": sources,
            "is_fallback": False,
            "standalone_query": standalone_query
        }
        return

    prompt = RAG_PROMPT_TEMPLATE.format(context=context_str, question=standalone_query)
    
    try:
        async for chunk in llm.astream(prompt):
            if chunk.content:
                yield {"type": "token", "content": chunk.content}
    except Exception as e:
        error_msg = f"\n[Error generating response: {str(e)}]"
        yield {"type": "token", "content": error_msg}

    yield {
        "type": "final",
        "sources": sources,
        "is_fallback": False,
        "standalone_query": standalone_query
    }

def generate_rag_answer_sync(query: str, history: List[Dict[str, str]] = None) -> Dict[str, Any]:
    """Synchronous version for evaluation scripts and tests."""
    history = history or []
    small_talk = check_greeting_or_smalltalk(query)
    if small_talk:
        return {
            "answer": small_talk,
            "sources": [],
            "is_fallback": False,
            "best_similarity": 1.0
        }

    llm = get_llm(streaming=False)
    standalone_query = rewrite_query_with_history(query, history, llm=llm)
    scored_chunks = retrieve_top_chunks(standalone_query, k=4)
    
    if not scored_chunks:
        return {
            "answer": FALLBACK_MESSAGE,
            "sources": [],
            "is_fallback": True,
            "best_similarity": 0.0
        }

    best_doc, _, best_similarity = scored_chunks[0]
    
    if best_similarity < settings.SIMILARITY_THRESHOLD:
        return {
            "answer": FALLBACK_MESSAGE,
            "sources": [],
            "is_fallback": True,
            "best_similarity": best_similarity
        }

    valid_chunks = [c for c in scored_chunks if c[2] >= (settings.SIMILARITY_THRESHOLD - 0.15)]
    context_str, sources = build_context_string(valid_chunks)

    if llm is None:
        answer = f"Relevant information from {', '.join(sources)}:\n\n" + "\n\n".join([c[0].page_content for c in valid_chunks])
    else:
        prompt = RAG_PROMPT_TEMPLATE.format(context=context_str, question=standalone_query)
        response = llm.invoke(prompt)
        answer = response.content.strip()

    return {
        "answer": answer,
        "sources": sources,
        "is_fallback": False,
        "best_similarity": best_similarity
    }
