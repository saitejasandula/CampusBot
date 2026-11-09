import re
from typing import List, Dict, Optional
from app.db import get_session_history, save_message, clear_session_history

# In-memory session cache: session_id -> list of {"role": "user"|"assistant", "content": "..."}
_session_cache: Dict[str, List[Dict[str, str]]] = {}

def get_recent_history(session_id: str, max_messages: int = 6) -> List[Dict[str, str]]:
    """
    Get the last N messages for a session.
    First checks the in-memory cache; falls back to SQLite if needed.
    """
    if session_id in _session_cache:
        return _session_cache[session_id][-max_messages:]
    
    # Fallback to SQLite
    db_msgs = get_session_history(session_id, limit=max_messages)
    memory = [{"role": m["role"], "content": m["content"]} for m in db_msgs]
    _session_cache[session_id] = memory
    return memory

def append_to_history(session_id: str, role: str, content: str) -> None:
    """Append a message to the in-memory session cache."""
    if session_id not in _session_cache:
        _session_cache[session_id] = []
    _session_cache[session_id].append({"role": role, "content": content})
    # Keep last 12 messages in cache (6 turns)
    if len(_session_cache[session_id]) > 12:
        _session_cache[session_id] = _session_cache[session_id][-12:]

def clear_history_cache(session_id: str) -> None:
    """Clear memory cache for a session."""
    if session_id in _session_cache:
        del _session_cache[session_id]

def check_greeting_or_smalltalk(text: str) -> Optional[str]:
    """
    Detect greetings, pleasantries, and small talk to respond directly without retrieval.
    """
    cleaned = text.strip().lower()
    cleaned = re.sub(r"[^\w\s]", "", cleaned)

    greetings = {
        "hi", "hello", "hey", "hey there", "hi there", "greetings",
        "good morning", "good afternoon", "good evening", "howdy"
    }
    
    thanks = {
        "thanks", "thank you", "thx", "thank you so much", "many thanks"
    }
    
    goodbyes = {
        "bye", "goodbye", "see you", "see ya", "cya", "take care"
    }
    
    identities = {
        "who are you", "what are you", "what is your name", "whats your name",
        "tell me about yourself", "what can you do", "help"
    }

    if cleaned in greetings:
        return (
            "Hello! I am CampusBot, the AI helpdesk assistant for Greenfield Institute of Technology (GIT). "
            "I can assist you with admissions, tuition & hostel fees, exam regulations, campus placements, and student facilities. "
            "How can I help you today?"
        )
    
    if cleaned in thanks:
        return "You're very welcome! Feel free to ask if you have any more questions about Greenfield Institute of Technology."

    if cleaned in goodbyes:
        return "Goodbye! Best of luck with your academic journey at Greenfield Institute of Technology. Have a great day!"

    if cleaned in identities:
        return (
            "I am CampusBot, your 24/7 AI helpdesk for Greenfield Institute of Technology. "
            "You can ask me about B.Tech/M.Tech/MBA admissions, semester exams, hostel fees, placement statistics, library hours, scholarships, and more."
        )

    return None

def rewrite_query_with_history(query: str, history: List[Dict[str, str]], llm=None) -> str:
    """
    Rewrite a follow-up query into a standalone question based on previous turns.
    e.g., 'what about its fees?' after 'Tell me about B.Tech CSE' -> 'What are the fees for B.Tech CSE at Greenfield Institute of Technology?'
    """
    if not history or llm is None:
        return query
    
    # Check if query is already standalone or long enough
    if len(query.split()) > 8 and not any(w in query.lower() for w in ["it", "its", "they", "their", "that", "those", "above"]):
        return query

    try:
        from langchain_core.prompts import PromptTemplate
        template = (
            "Given the following conversation history and a follow-up question, "
            "rephrase the follow-up question into an independent, standalone search query "
            "that contains all necessary context (college name: Greenfield Institute of Technology, program, branch, or topic). "
            "Do NOT answer the question. Only output the rephrased question.\n\n"
            "Conversation History:\n{history}\n\n"
            "Follow-up Question: {query}\n\n"
            "Standalone Question:"
        )
        history_text = "\n".join([f"{m['role'].capitalize()}: {m['content']}" for m in history[-4:]])
        prompt = template.format(history=history_text, query=query)
        response = llm.invoke(prompt)
        rewritten = response.content.strip()
        # Fallback if model returned empty or long rambling
        if rewritten and len(rewritten) < 300:
            return rewritten
    except Exception as e:
        print(f"[Memory] Query rewrite skipped/failed: {e}")

    return query
