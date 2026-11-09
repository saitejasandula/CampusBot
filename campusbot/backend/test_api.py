import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

import json
from starlette.testclient import TestClient
from app.main import app
from app.config import settings

client = TestClient(app)

def test_health():
    print("\n--- 1. Testing GET /health ---")
    response = client.get("/health")
    print("Health response:", response.status_code, response.json())
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["docs_count"] == 5
    assert data["chroma_ready"] is True
    print("PASSED GET /health")

def test_greeting_stream():
    print("\n--- 2. Testing POST /chat/stream with Greeting ---")
    session_id = "test-session-001"
    response = client.post(
        "/chat/stream",
        json={"session_id": session_id, "message": "hello"}
    )
    assert response.status_code == 200
    text = response.text
    print("SSE Greeting Response snippet:", text[:200])
    assert "token" in text
    assert "final" in text
    assert "CampusBot" in text
    print("PASSED Greeting Stream")

def test_in_scope_stream():
    print("\n--- 3. Testing POST /chat/stream with College Query ---")
    session_id = "test-session-001"
    response = client.post(
        "/chat/stream",
        json={"session_id": session_id, "message": "What are the hostel fees?"}
    )
    assert response.status_code == 200
    text = response.text
    print("SSE College Query Response snippet:", text[:200])
    assert "token" in text
    assert "final" in text
    # Verify final event has sources
    assert "fees.md" in text
    print("PASSED In-scope query")

def test_out_of_scope_fallback():
    print("\n--- 4. Testing POST /chat/stream with Out-of-Scope Query ---")
    session_id = "test-session-001"
    response = client.post(
        "/chat/stream",
        json={"session_id": session_id, "message": "How do I bake a chocolate cake recipe?"}
    )
    assert response.status_code == 200
    text = response.text
    print("SSE Out-of-scope snippet:", text[:200])
    assert "couldn't find this in the college documents" in text
    assert '"is_fallback": true' in text.lower()
    print("PASSED Out-of-scope fallback")

def test_history_and_clear():
    print("\n--- 5. Testing GET & DELETE /history ---")
    session_id = "test-session-001"
    res = client.get(f"/history/{session_id}")
    assert res.status_code == 200
    data = res.json()
    messages = data["messages"]
    print(f"Total messages in history: {len(messages)}")
    assert len(messages) >= 6  # 3 queries * 2
    
    # Test DELETE
    del_res = client.delete(f"/history/{session_id}")
    assert del_res.status_code == 200
    
    # Verify empty
    res2 = client.get(f"/history/{session_id}")
    assert len(res2.json()["messages"]) == 0
    print("PASSED History and Clear")

def test_feedback():
    print("\n--- 6. Testing POST /feedback ---")
    res = client.post(
        "/feedback",
        json={"message_id": "msg-12345", "helpful": True}
    )
    assert res.status_code == 200
    print("Feedback response:", res.json())
    assert res.json()["status"] == "success"
    print("PASSED POST /feedback")

def test_escalate():
    print("\n--- 7. Testing POST /escalate ---")
    res = client.post(
        "/escalate",
        json={
            "session_id": "session-xyz",
            "name": "Jane Doe",
            "email": "jane@example.com",
            "question": "Can I transfer credits from another university?"
        }
    )
    assert res.status_code == 200
    data = res.json()
    print("Escalate response:", data)
    assert data["status"] == "success"
    assert "ticket_id" in data
    print("PASSED POST /escalate")

def test_ingest_endpoint():
    print("\n--- 8. Testing POST /ingest Auth & Trigger ---")
    # Unauthorized attempt
    res_unauth = client.post("/ingest")
    assert res_unauth.status_code == 401
    print("Unauthorized rejection check passed.")
    
    # Authorized attempt
    res_auth = client.post("/ingest", headers={"X-Admin-Key": settings.ADMIN_KEY})
    assert res_auth.status_code == 200
    data = res_auth.json()
    print("Ingest API response:", data)
    assert data["status"] == "success"
    assert data["chunks_indexed"] == 32
    print("PASSED POST /ingest")

if __name__ == "__main__":
    test_health()
    test_greeting_stream()
    test_in_scope_stream()
    test_out_of_scope_fallback()
    test_history_and_clear()
    test_feedback()
    test_escalate()
    test_ingest_endpoint()
    print("\n==========================================")
    print("ALL BACKEND API TESTS COMPLETED AND PASSED!")
    print("==========================================")
