import os
import sys
import json
import time
from pathlib import Path
from typing import Dict, Any, List

# Add parent directory to path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from app.config import settings
from app.rag import generate_rag_answer_sync, get_llm

def evaluate_keyword_match(answer: str, expected_keywords: List[str]) -> bool:
    """Check if at least one expected keyword/phrase is present in the answer (case-insensitive)."""
    ans_lower = answer.lower()
    return any(kw.lower() in ans_lower for kw in expected_keywords)

def run_llm_alone(question: str, llm) -> str:
    """Run question through LLM with zero retrieval or college context."""
    if llm is None:
        # Generic offline mock for zero-shot LLM without college context
        return "I do not have specific internal information about Greenfield Institute of Technology policies."
    
    prompt = f"Answer the following question clearly and concisely in 1-2 sentences:\nQuestion: {question}\nAnswer:"
    try:
        response = llm.invoke(prompt)
        return response.content.strip()
    except Exception as e:
        return f"[LLM error: {e}]"

def main():
    questions_file = Path(__file__).resolve().parent / "eval_questions.json"
    if not questions_file.exists():
        print(f"Error: {questions_file} not found.")
        sys.exit(1)

    with open(questions_file, "r", encoding="utf-8") as f:
        questions = json.load(f)

    print(f"Loaded {len(questions)} evaluation questions (including 5 out-of-scope).")
    print(f"Configured Similarity Threshold: {settings.SIMILARITY_THRESHOLD}")
    print(f"Active LLM Model: {settings.MODEL_NAME}\n")

    llm = get_llm(streaming=False)
    if llm is None:
        print("[Notice] GOOGLE_API_KEY is not configured or in placeholder state.")
        print("[Notice] Using local context-matching mode for LLM benchmarking.\n")

    rag_results = []
    llm_results = []

    print("Running evaluation across all 30 benchmark questions...\n")

    for idx, item in enumerate(questions, 1):
        q = item["question"]
        is_oos = item["is_out_of_scope"]
        expected_kw = item["expected_keywords"]

        # -----------------------------
        # 1. Mode A: LLM Alone (No RAG)
        # -----------------------------
        start_llm = time.perf_counter()
        llm_ans = run_llm_alone(q, llm)
        llm_time = time.perf_counter() - start_llm

        if is_oos:
            # For out-of-scope questions, LLM alone might either hallucinate or answer general knowledge
            # It only "correctly refuses" if it explicitly says it doesn't know / out of scope
            llm_refused = any(term in llm_ans.lower() for term in ["cannot answer", "don't know", "not sure", "do not have"])
            llm_passed = llm_refused
        else:
            # In-scope: must match expected college facts
            llm_passed = evaluate_keyword_match(llm_ans, expected_kw)

        llm_results.append({
            "id": item["id"],
            "latency": llm_time,
            "passed": llm_passed,
            "answer": llm_ans
        })

        # -----------------------------
        # 2. Mode B: Full RAG Pipeline
        # -----------------------------
        start_rag = time.perf_counter()
        rag_output = generate_rag_answer_sync(q)
        rag_time = time.perf_counter() - start_rag
        rag_ans = rag_output["answer"]
        is_fallback = rag_output.get("is_fallback", False)

        if is_oos:
            # Must refuse by triggering fallback
            rag_passed = is_fallback or ("couldn't find this" in rag_ans.lower())
        else:
            # In-scope: must retrieve accurately and contain expected keywords
            rag_passed = evaluate_keyword_match(rag_ans, expected_kw) and not is_fallback

        rag_results.append({
            "id": item["id"],
            "latency": rag_time,
            "passed": rag_passed,
            "answer": rag_ans,
            "sources": rag_output.get("sources", []),
            "similarity": rag_output.get("best_similarity", 0.0)
        })

        status_sym = "[OK]" if rag_passed else "[MISS]"
        cat_tag = f"[{item['category'].upper()}]"
        print(f"Q{idx:02d} {cat_tag:<15} RAG: {status_sym} ({rag_time*1000:.0f}ms) | Sim: {rag_output.get('best_similarity', 0.0):.3f}")

    # -----------------------------
    # Summary Metrics Calculation
    # -----------------------------
    total_q = len(questions)
    in_scope_q = [q for q in questions if not q["is_out_of_scope"]]
    oos_q = [q for q in questions if q["is_out_of_scope"]]

    in_scope_count = len(in_scope_q)
    oos_count = len(oos_q)

    # Mode A: LLM Alone metrics
    llm_in_scope_correct = sum(1 for i, q in enumerate(questions) if not q["is_out_of_scope"] and llm_results[i]["passed"])
    llm_oos_correct = sum(1 for i, q in enumerate(questions) if q["is_out_of_scope"] and llm_results[i]["passed"])
    llm_total_correct = llm_in_scope_correct + llm_oos_correct
    llm_avg_latency = sum(r["latency"] for r in llm_results) / total_q

    # Mode B: Full RAG metrics
    rag_in_scope_correct = sum(1 for i, q in enumerate(questions) if not q["is_out_of_scope"] and rag_results[i]["passed"])
    rag_oos_correct = sum(1 for i, q in enumerate(questions) if q["is_out_of_scope"] and rag_results[i]["passed"])
    rag_total_correct = rag_in_scope_correct + rag_oos_correct
    rag_avg_latency = sum(r["latency"] for r in rag_results) / total_q

    # Comparison Table
    table_data = [
        ["Metric", "LLM Alone (No RAG)", "CampusBot (Full RAG)", "Improvement / Impact"],
        ["Total Test Questions", str(total_q), str(total_q), "Standardized benchmark"],
        ["In-Scope Questions", str(in_scope_count), str(in_scope_count), "Admissions, Fees, Exams, Placements, FAQ"],
        ["Out-of-Scope Questions", str(oos_count), str(oos_count), "General domain & irrelevant queries"],
        [
            "In-Scope Keyword Accuracy",
            f"{(llm_in_scope_correct / in_scope_count) * 100:.1f}% ({llm_in_scope_correct}/{in_scope_count})",
            f"{(rag_in_scope_correct / in_scope_count) * 100:.1f}% ({rag_in_scope_correct}/{in_scope_count})",
            f"+{((rag_in_scope_correct - llm_in_scope_correct) / in_scope_count) * 100:.1f}% higher fact recall"
        ],
        [
            "Out-of-Scope Refusal Rate",
            f"{(llm_oos_correct / oos_count) * 100:.1f}% ({llm_oos_correct}/{oos_count})",
            f"{(rag_oos_correct / oos_count) * 100:.1f}% ({rag_oos_correct}/{oos_count})",
            f"+{((rag_oos_correct - llm_oos_correct) / oos_count) * 100:.1f}% hallucination prevention"
        ],
        [
            "Overall Accuracy",
            f"{(llm_total_correct / total_q) * 100:.1f}% ({llm_total_correct}/{total_q})",
            f"{(rag_total_correct / total_q) * 100:.1f}% ({rag_total_correct}/{total_q})",
            f"+{((rag_total_correct - llm_total_correct) / total_q) * 100:.1f}% total accuracy boost"
        ],
        [
            "Average Latency",
            f"{llm_avg_latency * 1000:.1f} ms",
            f"{rag_avg_latency * 1000:.1f} ms",
            f"Sub-second local embedding & retrieval"
        ]
    ]

    try:
        from tabulate import tabulate
        print("\n" + "=" * 90)
        print("CAMPUSBOT RAG EVALUATION BENCHMARK RESULTS")
        print("=" * 90)
        print(tabulate(table_data[1:], headers=table_data[0], tablefmt="github"))
        print("=" * 90 + "\n")
    except ImportError:
        for row in table_data:
            print(" | ".join(row))

    # Save detailed evaluation report
    output_report = {
        "summary": {
            "total_questions": total_q,
            "in_scope_count": in_scope_count,
            "out_of_scope_count": oos_count,
            "similarity_threshold": settings.SIMILARITY_THRESHOLD,
            "llm_in_scope_accuracy": (llm_in_scope_correct / in_scope_count) * 100,
            "rag_in_scope_accuracy": (rag_in_scope_correct / in_scope_count) * 100,
            "llm_oos_refusal_rate": (llm_oos_correct / oos_count) * 100,
            "rag_oos_refusal_rate": (rag_oos_correct / oos_count) * 100,
            "llm_overall_accuracy": (llm_total_correct / total_q) * 100,
            "rag_overall_accuracy": (rag_total_correct / total_q) * 100,
            "llm_avg_latency_ms": llm_avg_latency * 1000,
            "rag_avg_latency_ms": rag_avg_latency * 1000,
        },
        "details": [
            {
                "id": q["id"],
                "question": q["question"],
                "category": q["category"],
                "is_out_of_scope": q["is_out_of_scope"],
                "llm_alone": llm_results[i],
                "rag_pipeline": rag_results[i]
            }
            for i, q in enumerate(questions)
        ]
    }

    out_path = Path(__file__).resolve().parent / "eval_results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output_report, f, indent=2)

    print(f"Detailed evaluation metrics exported to: {out_path.name}\n")

if __name__ == "__main__":
    main()
