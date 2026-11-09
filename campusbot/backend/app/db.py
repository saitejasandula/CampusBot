import sqlite3
import json
import uuid
from datetime import datetime
from typing import List, Dict, Any, Optional
from pathlib import Path
from app.config import settings

def get_connection() -> sqlite3.Connection:
    """Create or connect to SQLite database with foreign keys enabled."""
    db_path = Path(settings.SQLITE_DB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn

def init_db() -> None:
    """Initialize SQLite database tables for history, feedback, and escalation."""
    with get_connection() as conn:
        cursor = conn.cursor()
        
        # Conversations table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                sources TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_conv_session ON conversations (session_id)")
        
        # Feedback table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS feedback (
                id TEXT PRIMARY KEY,
                message_id TEXT NOT NULL,
                helpful INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Escalations / Human tickets table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS escalations (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                name TEXT NOT NULL,
                email TEXT NOT NULL,
                question TEXT NOT NULL,
                status TEXT DEFAULT 'OPEN',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()

def save_message(session_id: str, role: str, content: str, sources: Optional[List[str]] = None, message_id: Optional[str] = None) -> str:
    """Save a chat message to SQLite."""
    mid = message_id or str(uuid.uuid4())
    sources_json = json.dumps(sources or [])
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO conversations (id, session_id, role, content, sources, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (mid, session_id, role, content, sources_json, datetime.utcnow().isoformat()))
        conn.commit()
    return mid

def get_session_history(session_id: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Retrieve message history for a given session."""
    with get_connection() as conn:
        cursor = conn.cursor()
        if limit:
            cursor.execute("""
                SELECT * FROM (
                    SELECT id, session_id, role, content, sources, created_at
                    FROM conversations
                    WHERE session_id = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                ) ORDER BY created_at ASC
            """, (session_id, limit))
        else:
            cursor.execute("""
                SELECT id, session_id, role, content, sources, created_at
                FROM conversations
                WHERE session_id = ?
                ORDER BY created_at ASC
            """, (session_id,))
        rows = cursor.fetchall()
        
    messages = []
    for r in rows:
        messages.append({
            "id": r["id"],
            "role": r["role"],
            "content": r["content"],
            "sources": json.loads(r["sources"]) if r["sources"] else [],
            "timestamp": r["created_at"]
        })
    return messages

def clear_session_history(session_id: str) -> int:
    """Clear conversation history for a session."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM conversations WHERE session_id = ?", (session_id,))
        deleted = cursor.rowcount
        conn.commit()
    return deleted

def record_feedback(message_id: str, helpful: bool) -> str:
    """Record user feedback (helpful or unhelpful)."""
    fid = str(uuid.uuid4())
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO feedback (id, message_id, helpful, created_at)
            VALUES (?, ?, ?, ?)
        """, (fid, message_id, 1 if helpful else 0, datetime.utcnow().isoformat()))
        conn.commit()
    return fid

def create_escalation_ticket(session_id: str, name: str, email: str, question: str) -> str:
    """Create a new human helpdesk ticket."""
    ticket_id = f"TICK-{uuid.uuid4().hex[:8].upper()}"
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO escalations (id, session_id, name, email, question, status, created_at)
            VALUES (?, ?, ?, ?, ?, 'OPEN', ?)
        """, (ticket_id, session_id, name, email, question, datetime.utcnow().isoformat()))
        conn.commit()
    return ticket_id
