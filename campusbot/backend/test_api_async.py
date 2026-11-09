import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

log_file = Path(__file__).resolve().parent / "test_run.log"

def log(msg):
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(str(msg) + "\n")
    print(msg, flush=True)

with open(log_file, "w", encoding="utf-8") as f:
    f.write("Log started\n")

import asyncio
import json
import traceback
import httpx

try:
    from app.config import settings
    from app.db import init_db
    from app.main import app

    # Ensure DB tables are initialized
    init_db()
    log("DB initialized.")
except Exception as e:
    log(f"Import error: {traceback.format_exc()}")
    sys.exit(1)

async def run_tests():
    try:
        log("Starting AsyncClient tests...")
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Health
            res = await client.get("/health")
            log(f"1. Health: {res.status_code} {res.json()}")
            assert res.status_code == 200
            assert res.json()["status"] == "ok"

            # 2. Greeting stream
            res_greet = await client.post("/chat/stream", json={"session_id": "s1", "message": "hello"})
            log(f"2. Greeting status: {res_greet.status_code} text: {res_greet.text[:80]}")
            assert res_greet.status_code == 200

            # 3. In-scope query stream
            res_rag = await client.post("/chat/stream", json={"session_id": "s1", "message": "What are the hostel fees?"})
            log(f"3. RAG status: {res_rag.status_code} snippet: {res_rag.text[:100]}")
            assert res_rag.status_code == 200

            # 4. Out-of-scope fallback
            res_fall = await client.post("/chat/stream", json={"session_id": "s1", "message": "How do I bake a chocolate cake?"})
            log(f"4. Out-of-scope status: {res_fall.status_code}")
            assert res_fall.status_code == 200

            # 5. History
            res_hist = await client.get("/history/s1")
            log(f"5. History count: {len(res_hist.json()['messages'])}")
            assert len(res_hist.json()["messages"]) == 6

            # 6. Feedback
            res_fb = await client.post("/feedback", json={"message_id": "msg-001", "helpful": True})
            log(f"6. Feedback: {res_fb.status_code} {res_fb.json()}")
            assert res_fb.status_code == 200

            # 7. Escalate
            res_esc = await client.post("/escalate", json={
                "session_id": "s1",
                "name": "Jane Doe",
                "email": "jane@greenfield.edu",
                "question": "Can I transfer credits?"
            })
            log(f"7. Escalate: {res_esc.status_code} {res_esc.json()}")
            assert res_esc.status_code == 200

            # 8. Clear history
            res_del = await client.delete("/history/s1")
            log(f"8. Clear history: {res_del.status_code}")
            assert res_del.status_code == 200

            # 9. Ingest unauthorized
            res_unauth = await client.post("/ingest")
            log(f"9. Ingest unauth: {res_unauth.status_code}")
            assert res_unauth.status_code == 401

            # 10. Ingest authorized
            res_auth = await client.post("/ingest", headers={"X-Admin-Key": settings.ADMIN_KEY})
            log(f"10. Ingest auth: {res_auth.status_code} {res_auth.json()}")
            assert res_auth.status_code == 200

        log(">>> ALL 10 TESTS PASSED SUCCESSFULLY! <<<")
    except Exception as e:
        log(f"TEST RUN FAILED: {traceback.format_exc()}")

if __name__ == "__main__":
    asyncio.run(run_tests())
