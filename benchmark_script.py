#!/usr/bin/env python3
"""
Run repeatable RAG vs Long-Context benchmarks and export results to CSV.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib import error, request


DEFAULT_QUESTIONS = [
    "Compare SEP-1 reporting requirements between FY2026 and FY2027 IQR guides. What changed and what stayed the same?",
    "Identify sepsis-related submission timing or data element differences across FY2026 IQR, FY2027 IQR, and FY2026 IPPS final rule.",
    "Do any sepsis requirements in FY2026 IPPS conflict with wording in FY2027 IQR guidance?",
    "List compliance risks if a hospital follows only FY2026 IQR guidance but misses FY2027 updates for sepsis reporting.",
    "Build a remediation checklist for aligning sepsis-related quality reporting to FY2027 expectations.",
    "Where do the documents define measure-set entry expectations for sepsis, and how should implementation teams operationalize this?",
    "Summarize evidence that CMS expects annual spec/manual addendum monitoring for quality reporting and sepsis measures.",
    "Map sepsis-related requirements to likely owner teams (quality, informatics, coding, compliance) based on cited policy language.",
    "Find any references tying sepsis reporting to payment update risk and summarize governance implications.",
    "Produce an audit-ready summary of sepsis compliance gaps with direct citations [doc|page].",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run benchmark questions against /api/benchmark and export CSV/JSONL."
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="API base URL.")
    parser.add_argument("--endpoint", default="/api/benchmark", help="Benchmark endpoint path.")
    parser.add_argument("--top-k", type=int, default=8, help="Top-k setting in request payload.")
    parser.add_argument("--timeout", type=float, default=180.0, help="HTTP timeout in seconds.")
    parser.add_argument("--sleep-seconds", type=float, default=0.0, help="Delay between questions.")
    parser.add_argument(
        "--questions-file",
        help="Optional text file with one question per line. Lines starting with '#' are ignored.",
    )
    parser.add_argument(
        "--question",
        action="append",
        help="Optional extra question. Can be supplied multiple times.",
    )
    parser.add_argument("--output-csv", help="Output CSV path.")
    parser.add_argument("--output-jsonl", help="Output JSONL path.")
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop on first request failure.",
    )
    return parser.parse_args()


def load_questions(args: argparse.Namespace) -> List[str]:
    questions: List[str] = []

    if args.questions_file:
        path = Path(args.questions_file)
        if not path.exists():
            raise FileNotFoundError("questions file not found: %s" % path)
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            questions.append(line)
    else:
        questions.extend(DEFAULT_QUESTIONS)

    if args.question:
        for q in args.question:
            text = q.strip()
            if text:
                questions.append(text)

    # De-duplicate while preserving order.
    seen = set()
    deduped: List[str] = []
    for q in questions:
        if q in seen:
            continue
        seen.add(q)
        deduped.append(q)

    if not deduped:
        raise ValueError("no questions were loaded")
    return deduped


def post_json(url: str, payload: Dict[str, Any], timeout: float) -> Dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=timeout) as resp:
        data = resp.read().decode("utf-8")
        return json.loads(data)


def unique_doc_names(citations: List[Dict[str, Any]], max_docs: int = 3) -> str:
    names: List[str] = []
    seen = set()
    for item in citations:
        name = str(item.get("doc_name", "")).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)
        if len(names) >= max_docs:
            break
    return "; ".join(names)


def answer_excerpt(text: str, limit: int = 220) -> str:
    compact = " ".join((text or "").split())
    return compact[:limit]


def build_row(index: int, question: str, result: Optional[Dict[str, Any]], err: str) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "question_id": index,
        "question": question,
        "error": err,
    }
    if not result:
        return row

    rag = result.get("rag", {}) or {}
    long_ctx = result.get("long_context", {}) or {}
    rag_answer = str(rag.get("answer", ""))
    long_answer = str(long_ctx.get("answer", ""))
    rag_citations = rag.get("citations", []) or []
    long_citations = long_ctx.get("citations", []) or []

    row.update(
        {
            "rag_latency_ms": rag.get("latency_ms", ""),
            "long_latency_ms": long_ctx.get("latency_ms", ""),
            "latency_delta_ms": _diff(long_ctx.get("latency_ms"), rag.get("latency_ms")),
            "rag_context_chunks": rag.get("context_chunks", ""),
            "long_context_chunks": long_ctx.get("context_chunks", ""),
            "rag_context_tokens": rag.get("context_tokens", ""),
            "long_context_tokens": long_ctx.get("context_tokens", ""),
            "rag_context_chars": rag.get("context_chars", ""),
            "long_context_chars": long_ctx.get("context_chars", ""),
            "rag_citation_count": len(rag_citations),
            "long_citation_count": len(long_citations),
            "rag_primary_docs": unique_doc_names(rag_citations),
            "long_primary_docs": unique_doc_names(long_citations),
            "rag_fallback": rag_answer.startswith("LLM fallback:"),
            "long_fallback": long_answer.startswith("LLM fallback:"),
            "rag_answer_excerpt": answer_excerpt(rag_answer),
            "long_answer_excerpt": answer_excerpt(long_answer),
        }
    )
    return row


def _diff(a: Any, b: Any) -> Any:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a - b
    return ""


def write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "question_id",
        "question",
        "error",
        "rag_latency_ms",
        "long_latency_ms",
        "latency_delta_ms",
        "rag_context_chunks",
        "long_context_chunks",
        "rag_context_tokens",
        "long_context_tokens",
        "rag_context_chars",
        "long_context_chars",
        "rag_citation_count",
        "long_citation_count",
        "rag_primary_docs",
        "long_primary_docs",
        "rag_fallback",
        "long_fallback",
        "rag_answer_excerpt",
        "long_answer_excerpt",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_jsonl(path: Path, items: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")


def summarize(rows: List[Dict[str, Any]]) -> str:
    ok_rows = [r for r in rows if not r.get("error")]
    if not ok_rows:
        return "No successful benchmark responses."

    rag_latencies = [int(r["rag_latency_ms"]) for r in ok_rows if str(r.get("rag_latency_ms")).isdigit()]
    long_latencies = [int(r["long_latency_ms"]) for r in ok_rows if str(r.get("long_latency_ms")).isdigit()]
    rag_fallbacks = sum(1 for r in ok_rows if str(r.get("rag_fallback")).lower() == "true")
    long_fallbacks = sum(1 for r in ok_rows if str(r.get("long_fallback")).lower() == "true")

    avg_rag = round(sum(rag_latencies) / len(rag_latencies), 2) if rag_latencies else "n/a"
    avg_long = round(sum(long_latencies) / len(long_latencies), 2) if long_latencies else "n/a"
    return (
        "Completed %s questions (%s successful). Avg latency ms: RAG=%s, LONG=%s. "
        "Fallbacks: RAG=%s, LONG=%s."
        % (len(rows), len(ok_rows), avg_rag, avg_long, rag_fallbacks, long_fallbacks)
    )


def main() -> int:
    args = parse_args()
    try:
        questions = load_questions(args)
    except Exception as exc:
        print("Failed to load questions: %s" % exc, file=sys.stderr)
        return 2

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    default_dir = Path("data/benchmark_runs")
    csv_path = Path(args.output_csv) if args.output_csv else default_dir / ("benchmark_%s.csv" % timestamp)
    jsonl_path = (
        Path(args.output_jsonl) if args.output_jsonl else default_dir / ("benchmark_%s.jsonl" % timestamp)
    )

    url = args.base_url.rstrip("/") + "/" + args.endpoint.lstrip("/")
    rows: List[Dict[str, Any]] = []
    raw_items: List[Dict[str, Any]] = []

    print("Benchmark endpoint: %s" % url)
    print("Questions: %s" % len(questions))
    print("")

    for idx, question in enumerate(questions, start=1):
        payload = {"question": question, "top_k": args.top_k}
        print("[%s/%s] %s" % (idx, len(questions), question))
        err_msg = ""
        result: Optional[Dict[str, Any]] = None
        started = time.time()
        try:
            result = post_json(url, payload, timeout=args.timeout)
            elapsed = round((time.time() - started) * 1000, 2)
            print("  -> ok (%sms)" % elapsed)
        except error.HTTPError as exc:
            err_msg = "HTTP %s" % exc.code
            try:
                body = exc.read().decode("utf-8")
                err_msg = "%s: %s" % (err_msg, body[:300])
            except Exception:
                pass
            print("  -> error: %s" % err_msg)
            if args.fail_fast:
                return 1
        except Exception as exc:
            err_msg = "%s: %s" % (type(exc).__name__, str(exc))
            print("  -> error: %s" % err_msg)
            if args.fail_fast:
                return 1

        rows.append(build_row(idx, question, result, err_msg))
        raw_items.append(
            {
                "question_id": idx,
                "question": question,
                "request": payload,
                "error": err_msg,
                "response": result,
            }
        )
        if args.sleep_seconds > 0 and idx < len(questions):
            time.sleep(args.sleep_seconds)

    write_csv(csv_path, rows)
    write_jsonl(jsonl_path, raw_items)

    print("")
    print(summarize(rows))
    print("CSV: %s" % csv_path)
    print("JSONL: %s" % jsonl_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
