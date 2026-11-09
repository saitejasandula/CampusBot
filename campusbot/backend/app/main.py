import asyncio
import json
import uuid
from typing import Optional
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, status, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from app.config import settings
from app.db import (
    init_db,
    save_message,
    get_session_history,
    clear_session_history,
    record_feedback,
    create_escalation_ticket
)
from app.schemas import (
    ChatStreamRequest,
    FeedbackRequest,
    EscalateRequest,
    HistoryResponse,
    FeedbackResponse,
    EscalateResponse,
    IngestResponse,
    HealthResponse,
    MessageSchema
)
from app.memory import (
    get_recent_history,
    append_to_history,
    clear_history_cache
)
from app.rag import (
    generate_rag_answer_stream,
    get_vectorstore
)
from app.ingest import run_ingestion

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Initialize SQLite DB tables and ensure vectorstore exists
    print("[Startup] Initializing SQLite database...")
    init_db()
    # Check if vectorstore has documents, if empty run ingestion (handles ephemeral disk on Render)
    try:
        vs = get_vectorstore()
        ids = vs.get().get("ids", [])
        if not ids:
            print("[Startup] Chroma collection is empty (fresh or ephemeral disk). Running automatic ingestion...")
            count, files = run_ingestion()
            print(f"[Startup] Ingestion completed: {count} chunks indexed from {len(files)} documents.")
        else:
            print(f"[Startup] Vectorstore ready with {len(ids)} document chunks.")
    except Exception as e:
        print(f"[Startup] Note on vectorstore check: {e}")
    yield
    print("[Shutdown] Cleaning up...")

app = FastAPI(
    title="CampusBot AI Helpdesk API",
    description="RAG-based helpdesk assistant for Greenfield Institute of Technology",
    version="1.0.0",
    lifespan=lifespan
)

# Configure CORS for local development and deployed frontend (Vercel)
cors_origins = settings.get_cors_origins()
if "*" in cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

@app.get("/")
async def root():
    """Root endpoint providing helpful links."""
    return {
        "name": "CampusBot AI Helpdesk API",
        "status": "online",
        "version": "1.0.0",
        "health": "/health",
        "api_docs": "/docs",
        "frontend_ui": "http://localhost:5173"
    }

@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint reporting document and vectorstore status."""
    docs_dir = Path(settings.DOCS_DIR)
    doc_count = len(list(docs_dir.glob("*.*"))) if docs_dir.exists() else 0
    chroma_ready = False
    try:
        vs = get_vectorstore()
        chroma_ready = len(vs.get().get("ids", [])) > 0
    except Exception:
        chroma_ready = False
        
    return HealthResponse(
        status="ok",
        version="1.0.0",
        docs_count=doc_count,
        chroma_ready=chroma_ready
    )

@app.post("/chat/stream")
async def chat_stream(request: ChatStreamRequest):
    """
    Stream chatbot answers token-by-token using Server-Sent Events (SSE).
    Includes a 15-second heartbeat to keep connections alive behind proxies (e.g. Render).
    Saves conversation turn to SQLite and memory.
    """
    session_id = request.session_id.strip()
    user_query = request.message.strip()
    
    if not user_query:
        raise HTTPException(status_code=400, detail="Message cannot be empty.")

    # 1. Save user message to DB and cache
    user_msg_id = save_message(session_id, role="user", content=user_query)
    append_to_history(session_id, role="user", content=user_query)

    # 2. Retrieve recent conversation history for memory and context
    history = get_recent_history(session_id, max_messages=6)

    # 3. Stream generator with 15s heartbeat
    async def sse_event_generator():
        bot_msg_id = str(uuid.uuid4())
        full_response_text = []
        final_sources = []
        is_fallback = False

        queue = asyncio.Queue()
        done_event = asyncio.Event()

        async def producer():
            try:
                async for item in generate_rag_answer_stream(user_query, session_id, history):
                    await queue.put(item)
            except Exception as e:
                await queue.put({"type": "error", "content": str(e)})
            finally:
                done_event.set()

        producer_task = asyncio.create_task(producer())

        try:
            # Immediately flush proxy buffer with initial heartbeat comment
            yield ": initial-ping\n\n"

            while not (done_event.is_set() and queue.empty()):
                try:
                    # Wait up to 15 seconds for next chunk from producer
                    item = await asyncio.wait_for(queue.get(), timeout=15.0)
                    if item["type"] == "token":
                        full_response_text.append(item["content"])
                        payload = json.dumps({"token": item["content"]})
                        yield f"event: token\ndata: {payload}\n\n"
                    elif item["type"] == "final":
                        final_sources = item.get("sources", [])
                        is_fallback = item.get("is_fallback", False)
                    elif item["type"] == "error":
                        err_text = f"An unexpected error occurred: {item['content']}"
                        full_response_text.append(err_text)
                        payload = json.dumps({"token": f"\n{err_text}"})
                        yield f"event: token\ndata: {payload}\n\n"
                        is_fallback = True
                except asyncio.TimeoutError:
                    # Heartbeat comment every 15s prevents Render proxy timeout
                    yield ": heartbeat\n\n"
        finally:
            if not producer_task.done():
                producer_task.cancel()

        # Save assistant message to DB and update memory cache
        complete_content = "".join(full_response_text).strip()
        save_message(
            session_id=session_id,
            role="assistant",
            content=complete_content,
            sources=final_sources,
            message_id=bot_msg_id
        )
        append_to_history(session_id, role="assistant", content=complete_content)

        # Final SSE event with sources, message_id, and fallback flag
        final_payload = json.dumps({
            "message_id": bot_msg_id,
            "sources": final_sources,
            "is_fallback": is_fallback
        })
        yield f"event: final\ndata: {final_payload}\n\n"

    return StreamingResponse(
        sse_event_generator(),
        media_type="text/event-stream",
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )

@app.get("/history/{session_id}", response_model=HistoryResponse)
async def get_history(session_id: str):
    """Retrieve full conversation history for a session."""
    messages = get_session_history(session_id)
    return HistoryResponse(
        session_id=session_id,
        messages=[MessageSchema(**m) for m in messages]
    )

@app.delete("/history/{session_id}")
async def clear_history(session_id: str):
    """Clear conversation history for a session."""
    deleted_count = clear_session_history(session_id)
    clear_history_cache(session_id)
    return {"status": "success", "deleted_messages": deleted_count}

@app.post("/feedback", response_model=FeedbackResponse)
async def submit_feedback(request: FeedbackRequest):
    """Record thumbs-up / thumbs-down feedback for a bot message."""
    try:
        fid = record_feedback(request.message_id, request.helpful)
        return FeedbackResponse(
            status="success",
            message=f"Feedback recorded with ID: {fid}"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to record feedback: {str(e)}")

@app.post("/escalate", response_model=EscalateResponse)
async def submit_escalation(request: EscalateRequest):
    """Create a human helpdesk ticket when question is out-of-scope or unresolved."""
    try:
        ticket_id = create_escalation_ticket(
            session_id=request.session_id,
            name=request.name.strip(),
            email=request.email.strip(),
            question=request.question.strip()
        )
        return EscalateResponse(
            status="success",
            ticket_id=ticket_id,
            message=f"Your ticket ({ticket_id}) has been submitted to student services. We will email you at {request.email} shortly."
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create escalation ticket: {str(e)}")

@app.post("/ingest", response_model=IngestResponse)
async def trigger_ingestion(x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key")):
    """
    Re-run ingestion of data/docs into ChromaDB.
    Protected by simple X-Admin-Key header.
    """
    if not x_admin_key or x_admin_key != settings.ADMIN_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized: Invalid or missing X-Admin-Key header."
        )

    try:
        count, files = run_ingestion()
        return IngestResponse(
            status="success",
            chunks_indexed=count,
            files_processed=files,
            message=f"Successfully indexed {count} chunks from {len(files)} documents."
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {str(e)}")
