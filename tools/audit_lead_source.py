"""
tools/audit_lead_source.py

Generic email-quality audit for any lead source.

Samples N leads from a given `source`, runs DNS MX validation (no SMTP
probe), and writes a JSON report with the invalid rate, top failure
reasons, and top failed domains. Used to decide whether a source
should be quarantined.

Mirrors the methodology used for the ai_discovery audit (2026-05-12).
"""
import collections
import json
import os
import random
import sqlite3
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(PROJECT_ROOT, "data", "smartmailer_ultimate.db")
REPORT_DIR = os.path.join(PROJECT_ROOT, "data")


def run(source: str, sample_size: int = 500, seed: int = 42) -> dict:
    if not os.path.exists(DB_PATH):
        raise FileNotFoundError(f"DB not found: {DB_PATH}")
    if not source or not isinstance(source, str):
        raise ValueError("source must be a non-empty string")
    if not (1 <= sample_size <= 5000):
        raise ValueError("sample_size must be between 1 and 5000")

    from core.email_validator import EmailValidator

    started_at = datetime.utcnow().isoformat() + "Z"
    conn = sqlite3.connect(DB_PATH)
    try:
        total = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE source=?", (source,)
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT email FROM leads WHERE source=? AND email IS NOT NULL AND email != ''",
            (source,),
        ).fetchall()
    finally:
        conn.close()

    emails = [r[0] for r in rows if "@" in (r[0] or "")]
    if not emails:
        return {
            "source": source,
            "total_in_source": total,
            "valid_emails_in_source": 0,
            "sample_size": 0,
            "error": "no valid emails found for source",
        }

    rng = random.Random(seed)
    actual_n = min(sample_size, len(emails))
    sample = rng.sample(emails, actual_n)

    v = EmailValidator(verify_mx=True, verify_smtp=False)
    results = []
    for em in sample:
        ok, reason = v.validate(em)
        results.append({"email": em, "valid": ok, "reason": reason})

    valid = sum(1 for r in results if r["valid"])
    invalid = actual_n - valid

    reason_hist = collections.Counter(
        r["reason"].split(":")[0].strip() for r in results if not r["valid"]
    )
    fail_domains = collections.Counter(
        r["email"].split("@")[-1].lower()
        for r in results
        if not r["valid"]
    )

    finished_at = datetime.utcnow().isoformat() + "Z"
    report = {
        "audit_at": started_at,
        "finished_at": finished_at,
        "source": source,
        "method": "DNS+MX only (no SMTP probe)",
        "total_in_source": total,
        "valid_emails_in_source": len(emails),
        "sample_size": actual_n,
        "seed": seed,
        "valid_mx": valid,
        "invalid": invalid,
        "valid_pct": round(valid / actual_n * 100, 2),
        "invalid_pct": round(invalid / actual_n * 100, 2),
        "estimated_invalid_in_full_source": int(
            len(emails) * invalid / actual_n
        ),
        "estimated_valid_in_full_source": int(
            len(emails) * valid / actual_n
        ),
        "reason_histogram": dict(reason_hist.most_common()),
        "top_failed_domains": dict(fail_domains.most_common(20)),
        "stats": v.get_stats(),
    }

    safe_source = "".join(c if c.isalnum() else "_" for c in source)
    report_path = os.path.join(
        REPORT_DIR, f"audit_source_{safe_source}_{datetime.utcnow().strftime('%Y-%m-%d')}.json"
    )
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    report["report_path"] = report_path

    return report


if __name__ == "__main__":
    import sys
    src = sys.argv[1] if len(sys.argv) > 1 else "ai_discovery"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 500
    out = run(source=src, sample_size=n)
    print(json.dumps(out, indent=2, ensure_ascii=False))
