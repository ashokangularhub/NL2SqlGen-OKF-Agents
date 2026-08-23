#!/usr/bin/env python3
"""
eval/sql_eval.py — SQL Generator Accuracy Evaluator

Measures the accuracy of the SQL generator by running natural language
queries through the full agent pipeline, then comparing the generated
SQL and its DB result against a ground-truth reference SQL.

Metrics
-------
  EM  Exact Match          : normalised generated SQL == normalised reference SQL
  EX  Execution Accuracy   : generated SQL result matches reference SQL result
  FP  First-Pass rate      : pipeline passed SQL validation on attempt 1
  FR  Failure rate         : pipeline returned an error / exhausted retries

Usage
-----
  python src/eval/sql_eval.py
  python src/eval/sql_eval.py --pipeline-url http://localhost:8081 \\
                               --db-url http://localhost:8000
  python src/eval/sql_eval.py --delay 2 --output report.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Any

import httpx


# ── Test Suite ─────────────────────────────────────────────────────────────────

@dataclass
class TestCase:
    """One NL→SQL ground-truth pair."""
    description: str     # human-readable label
    nl_query: str        # natural language question sent to the pipeline
    reference_sql: str   # known-correct SQL (ground truth)
    compare: str = "rows"
    # compare modes:
    #   "value" — compare first scalar value of first row (counts, sums, avgs)
    #   "rows"  — compare full sorted row sets (order-independent)
    #   "count" — compare only the number of rows returned (most lenient)


TEST_CASES: list[TestCase] = [
    # ── Scalar aggregates ────────────────────────────────────────────────────
    TestCase(
        description="Total customer count",
        nl_query="How many customers are in the database?",
        reference_sql="SELECT COUNT(*) AS total FROM customers",
        compare="value",
    ),
    TestCase(
        description="Active account count",
        nl_query="How many active accounts are there?",
        reference_sql="SELECT COUNT(*) AS total FROM accounts WHERE status = 'active'",
        compare="value",
    ),
    TestCase(
        description="Distinct loan type count",
        nl_query="How many different types of loans does the bank offer?",
        reference_sql="SELECT COUNT(DISTINCT loan_type) AS total FROM loans",
        compare="value",
    ),
    TestCase(
        description="Failed transaction count",
        nl_query="How many transactions have a failed status?",
        reference_sql="SELECT COUNT(*) AS total FROM transactions WHERE status = 'failed'",
        compare="value",
    ),
    TestCase(
        description="Overdue loan payment count",
        nl_query="How many loan payments are currently overdue?",
        reference_sql="SELECT COUNT(*) AS total FROM loan_payments WHERE status = 'overdue'",
        compare="value",
    ),
    TestCase(
        description="Total outstanding loan balance",
        nl_query="What is the total outstanding balance across all active and delinquent loans?",
        reference_sql=(
            "SELECT SUM(outstanding_balance) AS total "
            "FROM loans WHERE status IN ('active', 'delinquent')"
        ),
        compare="value",
    ),
    TestCase(
        description="Average transaction amount",
        nl_query="What is the average amount across all transactions?",
        reference_sql="SELECT AVG(amount) AS avg_amount FROM transactions",
        compare="value",
    ),
    TestCase(
        description="Written-off loan count",
        nl_query="How many loans have been written off?",
        reference_sql="SELECT COUNT(*) AS total FROM loans WHERE status = 'written_off'",
        compare="value",
    ),
    # ── Filtered row sets ────────────────────────────────────────────────────
    TestCase(
        description="Delinquent loans list",
        nl_query="Show all delinquent loans",
        reference_sql="SELECT * FROM loans WHERE status = 'delinquent' LIMIT 50",
        compare="count",
    ),
    TestCase(
        description="Frozen accounts list",
        nl_query="List all frozen accounts",
        reference_sql="SELECT * FROM accounts WHERE status = 'frozen' LIMIT 50",
        compare="count",
    ),
    TestCase(
        description="High risk customers list",
        nl_query="Show customers with high risk tier",
        reference_sql="SELECT * FROM customers WHERE risk_tier = 'high' LIMIT 50",
        compare="count",
    ),
    TestCase(
        description="Expired KYC customers list",
        nl_query="List customers whose KYC status is expired",
        reference_sql="SELECT * FROM customers WHERE kyc_status = 'expired' LIMIT 50",
        compare="count",
    ),
    TestCase(
        description="Open compliance flags list",
        nl_query="Show all open compliance flags",
        reference_sql="SELECT * FROM flags WHERE status = 'open' LIMIT 50",
        compare="count",
    ),
    TestCase(
        description="Pending KYC customers list",
        nl_query="List customers with pending KYC status",
        reference_sql="SELECT * FROM customers WHERE kyc_status = 'pending' LIMIT 50",
        compare="count",
    ),
    TestCase(
        description="Active loans list",
        nl_query="Show all active loans",
        reference_sql="SELECT * FROM loans WHERE status = 'active' LIMIT 50",
        compare="count",
    ),
]


# ── HTTP helpers ───────────────────────────────────────────────────────────────

def run_pipeline(nl_query: str, pipeline_url: str) -> dict[str, Any]:
    """POST to /classify and return the parsed response dict."""
    resp = httpx.post(
        f"{pipeline_url}/classify",
        json={"query": nl_query},
        timeout=90,
    )
    resp.raise_for_status()
    return resp.json()


def run_sql(sql: str, db_url: str) -> dict[str, Any]:
    """POST sql to the DB micro-service and return the result dict."""
    resp = httpx.post(
        f"{db_url}/query",
        json={"sql": sql, "max_rows": 1000},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


# ── Comparison helpers ─────────────────────────────────────────────────────────

def normalize_sql(sql: str) -> str:
    """Strip whitespace/semicolons and uppercase for string comparison."""
    sql = sql.strip().rstrip(";").upper()
    return re.sub(r"\s+", " ", sql).strip()


def compare_results(
    ref_data: dict[str, Any],
    gen_data: dict[str, Any],
    mode: str,
) -> tuple[bool, str]:
    """
    Compare reference and generated query results.
    Returns (passed: bool, note: str).
    """
    if gen_data.get("error"):
        return False, f"DB error: {gen_data['error']}"

    ref_rows: list = ref_data.get("rows", [])
    gen_rows: list = gen_data.get("rows", [])
    ref_count: int = ref_data.get("row_count", len(ref_rows))
    gen_count: int = gen_data.get("row_count", len(gen_rows))

    if mode == "value":
        if not ref_rows and not gen_rows:
            return True, "both empty"
        if not ref_rows or not gen_rows:
            return False, f"ref_rows={len(ref_rows)}, gen_rows={len(gen_rows)}"
        ref_val = ref_rows[0][0] if ref_rows[0] else None
        gen_val = gen_rows[0][0] if gen_rows[0] else None
        try:
            ref_f = float(ref_val)
            gen_f = float(gen_val)
            if ref_f == 0.0:
                match = gen_f == 0.0
                note = f"ref=0, gen={gen_val}"
            else:
                pct_diff = abs(ref_f - gen_f) / abs(ref_f)
                match = pct_diff < 0.01          # 1 % tolerance
                note = f"ref={ref_f:.4g}, gen={gen_f:.4g} ({pct_diff*100:.2f}% diff)"
        except (TypeError, ValueError):
            match = str(ref_val) == str(gen_val)
            note = f"ref={ref_val!r}, gen={gen_val!r}"
        return match, note

    elif mode == "rows":
        if ref_count != gen_count:
            return False, f"row count mismatch: ref={ref_count}, gen={gen_count}"
        # Order-independent deep comparison

        def _key(row: list) -> str:
            return json.dumps(row, sort_keys=True, default=str)
        ref_sorted = sorted(_key(r) for r in ref_rows)
        gen_sorted = sorted(_key(r) for r in gen_rows)
        match = ref_sorted == gen_sorted
        note = (
            f"{ref_count} rows match" if match
            else f"row content differs (count {ref_count} same)"
        )
        return match, note

    elif mode == "count":
        match = ref_count == gen_count
        note = f"ref={ref_count}, gen={gen_count}"
        return match, note

    return False, f"Unknown compare mode: {mode!r}"


# ── Per-test result ────────────────────────────────────────────────────────────

@dataclass
class EvalResult:
    test_case: TestCase
    generated_sql: str = ""
    sql_attempt: int = 0
    pipeline_error: str | None = None
    em: bool = False        # Exact Match
    ex: bool = False        # Execution Accuracy
    ex_note: str = ""
    elapsed_s: float = 0.0


# ── Evaluation runner ──────────────────────────────────────────────────────────

def run_evaluation(
    pipeline_url: str,
    db_url: str,
    delay: float = 1.0,
) -> list[EvalResult]:
    results: list[EvalResult] = []
    total = len(TEST_CASES)

    for idx, tc in enumerate(TEST_CASES, 1):
        label = f"[{idx:02d}/{total}] {tc.description}"
        print(f"  {label:<52}", end="", flush=True)
        res = EvalResult(test_case=tc)
        t0 = time.perf_counter()

        try:
            # ── Step 1: run through the full agent pipeline ───────────────
            pr = run_pipeline(tc.nl_query, pipeline_url)
            res.generated_sql = pr.get("generated_sql", "")
            res.sql_attempt = pr.get("sql_attempt", 0)
            res.pipeline_error = pr.get("error") or None

            if res.pipeline_error:
                res.ex_note = f"pipeline: {res.pipeline_error[:60]}"
                print(f"FAIL  pipeline error")
                results.append(res)
                time.sleep(delay)
                continue

            if not res.generated_sql.strip().upper().startswith("SELECT"):
                res.ex_note = "generated SQL is not a SELECT statement"
                res.pipeline_error = res.ex_note
                print(f"FAIL  {res.ex_note}")
                results.append(res)
                time.sleep(delay)
                continue

            # ── Step 2: Exact Match ───────────────────────────────────────
            res.em = (
                normalize_sql(res.generated_sql) == normalize_sql(
                    tc.reference_sql)
            )

            # ── Step 3: Execution Accuracy ────────────────────────────────
            ref_data = run_sql(tc.reference_sql, db_url)
            gen_data = run_sql(res.generated_sql, db_url)
            res.ex, res.ex_note = compare_results(
                ref_data, gen_data, tc.compare)

            flags = ("EX✓" if res.ex else "EX✗") + ("  EM✓" if res.em else "")
            outcome = "PASS" if res.ex else "FAIL"
            print(f"{outcome}  {flags}  att={res.sql_attempt}  {res.ex_note}")

        except httpx.ConnectError as exc:
            res.pipeline_error = str(exc)
            res.ex_note = "connection error"
            print(f"ERROR  {exc}")
        except Exception as exc:
            res.pipeline_error = str(exc)
            res.ex_note = str(exc)[:80]
            print(f"ERROR  {exc}")

        res.elapsed_s = time.perf_counter() - t0
        results.append(res)

        if delay and idx < total:
            time.sleep(delay)

    return results


# ── Report printer ─────────────────────────────────────────────────────────────

def print_report(results: list[EvalResult]) -> None:
    total = len(results)
    em_count = sum(1 for r in results if r.em)
    ex_count = sum(1 for r in results if r.ex)
    fp_count = sum(1 for r in results if r.sql_attempt ==
                   1 and not r.pipeline_error)
    fail_count = sum(1 for r in results if r.pipeline_error)
    elapsed = sum(r.elapsed_s for r in results)

    def pct(n: int) -> str:
        return f"{n/total*100:.1f}%" if total else "n/a"

    sep = "=" * 76
    print(f"\n{sep}")
    print("  SQL GENERATOR ACCURACY REPORT")
    print(sep)
    print(f"  Test cases          : {total}")
    print(f"  Exact Match  (EM)   : {em_count:>2}/{total}  {pct(em_count)}")
    print(f"  Exec Accuracy (EX)  : {ex_count:>2}/{total}  {pct(ex_count)}")
    print(f"  First-pass rate     : {fp_count:>2}/{total}  {pct(fp_count)}")
    print(
        f"  Pipeline failures   : {fail_count:>2}/{total}  {pct(fail_count)}")
    print(f"  Total time          : {elapsed:.1f}s")
    print()
    print(f"  {'#':<3} {'Description':<42} {'EM':^4} {'EX':^4} {'Att':^3}  Note")
    print("  " + "-" * 74)
    for i, r in enumerate(results, 1):
        em = "✓" if r.em else "✗"
        ex = "✓" if r.ex else "✗"
        att = str(r.sql_attempt) if r.sql_attempt else "-"
        note = (r.ex_note or r.pipeline_error or "")[:34]
        print(
            f"  {i:<3} {r.test_case.description:<42} {em:^4} {ex:^4} {att:^3}  {note}")
    print(sep)


def save_json(results: list[EvalResult], path: str) -> None:
    data = {
        "summary": {
            "total": len(results),
            "em":    sum(1 for r in results if r.em),
            "ex":    sum(1 for r in results if r.ex),
            "fp":    sum(1 for r in results if r.sql_attempt == 1 and not r.pipeline_error),
            "failures": sum(1 for r in results if r.pipeline_error),
        },
        "cases": [
            {
                "description":  r.test_case.description,
                "nl_query":     r.test_case.nl_query,
                "reference_sql": r.test_case.reference_sql,
                "generated_sql": r.generated_sql,
                "sql_attempt":  r.sql_attempt,
                "em":           r.em,
                "ex":           r.ex,
                "ex_note":      r.ex_note,
                "pipeline_error": r.pipeline_error,
                "elapsed_s":    round(r.elapsed_s, 2),
            }
            for r in results
        ],
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    print(f"\n  Report saved → {path}")


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="SQL Generator Accuracy Evaluator for the ClearBank agent pipeline"
    )
    parser.add_argument(
        "--pipeline-url",
        default="http://localhost:8081",
        help="Base URL of the agent pipeline API (default: http://localhost:8081)",
    )
    parser.add_argument(
        "--db-url",
        default="http://localhost:8000",
        help="Base URL of the DB micro-service (default: http://localhost:8000)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=1.0,
        metavar="SECONDS",
        help="Pause between test cases to avoid LLM rate limits (default: 1.0)",
    )
    parser.add_argument(
        "--output",
        metavar="FILE",
        help="Save the full report as JSON to this file path",
    )
    args = parser.parse_args()

    print(f"\nSQL Eval")
    print(f"  Pipeline : {args.pipeline_url}/classify")
    print(f"  DB       : {args.db_url}/query")
    print(f"  Cases    : {len(TEST_CASES)}")
    print(f"  Delay    : {args.delay}s between cases\n")

    results = run_evaluation(args.pipeline_url, args.db_url, args.delay)
    print_report(results)

    if args.output:
        save_json(results, args.output)

    # Exit non-zero if execution accuracy < 80 %
    ex_rate = sum(1 for r in results if r.ex) / len(results) if results else 0
    return 0 if ex_rate >= 0.80 else 1


if __name__ == "__main__":
    sys.exit(main())
