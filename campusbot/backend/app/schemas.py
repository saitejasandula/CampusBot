from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any

class ChatStreamRequest(BaseModel):
    session_id: str = Field(..., description="Unique identifier for chat session")
    message: str = Field(..., min_length=1, max_length=2000, description="User query message")

class FeedbackRequest(BaseModel):
    message_id: str = Field(..., description="ID of the assistant message being rated")
    helpful: bool = Field(..., description="True if helpful, False if unhelpful")

class EscalateRequest(BaseModel):
    session_id: str = Field(..., description="Active session ID")
    name: str = Field(..., min_length=1, max_length=100, description="Student full name")
    email: str = Field(..., pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", description="Valid email address for follow-up")
    question: str = Field(..., min_length=5, max_length=2000, description="Unresolved question or concern")

class MessageSchema(BaseModel):
    id: str
    role: str
    content: str
    sources: List[str] = []
    timestamp: Optional[str] = None

class HistoryResponse(BaseModel):
    session_id: str
    messages: List[MessageSchema]

class FeedbackResponse(BaseModel):
    status: str = "success"
    message: str

class EscalateResponse(BaseModel):
    status: str = "success"
    ticket_id: str
    message: str

class IngestResponse(BaseModel):
    status: str = "success"
    chunks_indexed: int
    files_processed: List[str]
    message: str

class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "1.0.0"
    docs_count: int
    chroma_ready: bool
