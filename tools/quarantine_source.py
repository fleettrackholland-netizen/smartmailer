"""
tools/quarantine_source.py

Marks all leads matching a given `source` as status='quarantine'.

Generic version of the ai_discovery-specific tool. Used after a
source-quality audit indicates an unacceptable bounce risk
(e.g. csv_import shown to be 77% MX-invalid on 2026-05-12).

Idempotent: rows already in 'quarantine'/'opted_out'/'invalid' are skipped.
Transaction-safe: single UPDATE inside a single connection.
"""
import json
import os
import sqlite3
import sys
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(PROJECT_ROOT, "data", "smartmailer_ultimate.db")
REPORT_DIR = os.path.join(PROJECT_ROOT, "data")


def run(source: str, dry_run: bool = False) -> dict:
    if not os.path.exists(DB_PATH):
        raise FileNotFoundError(f"DB not found: {DB_PATH}")
    if not source or not isinstance(source, str):
        raise ValueError("source must be a non-empty string")

    started_at = datetime.utcnow().isoformat() + "Z"
    conn = sqlite3.connect(DB_PATH)
    try:
        before_total = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE source=?", (source,)
        ).fetchone()[0]
        before_active = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE source=? "
            "AND (status IS NULL OR status NOT IN ('quarantine','opted_out','invalid'))",
            (source,),
        ).fetchone()[0]
        before_quarantined = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE source=? AND status='quarantine'",
            (source,),
        ).fetchone()[0]

        affected = 0
        if not dry_run and before_active > 0:
            cur = conn.execute(
                "UPDATE leads SET status='quarantine', "
                "updated_at=datetime('now') "
                "WHERE source=? "
                "AND (status IS NULL OR status NOT IN ('quarantine','opted_out','invalid'))",
                (source,),
            )
            affected = cur.rowcount
            conn.commit()

        after_quarantined = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE source=? AND status='quarantine'",
            (source,),
        ).fetchone()[0]
        total_active_all_sources = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE status IS NULL OR status NOT IN ('quarantine','opted_out','invalid')"
        ).fetchone()[0]
        sources_remaining_active = conn.execute(
            "SELECT source, COUNT(*) FROM leads "
            "WHERE status IS NULL OR status NOT IN ('quarantine','opted_out','invalid') "
            "GROUP BY source ORDER BY COUNT(*) DESC"
        ).fetchall()
    finally:
        conn.close()

    finished_at = datetime.utcnow().isoformat() + "Z"
    report = {
        "started_at": started_at,
        "finished_at": finished_at,
        "source": source,
        "dry_run": dry_run,
        "source_total": before_total,
        "source_active_before": before_active,
        "source_quarantined_before": before_quarantined,
        "source_quarantined_after": after_quarantined,
        "rows_affected": affected,
        "active_leads_remaining_all_sources": total_active_all_sources,
        "active_by_source": [
            {"source": s or "(null)", "count": c}
            for s, c in sources_remaining_active
        ],
    }

    safe_source = "".join(c if c.isalnum() else "_" for c in source)
    report_path = os.path.join(
        REPORT_DIR,
        f"quarantine_source_{safe_source}_{datetime.utcnow().strftime('%Y-%m-%d')}.json",
    )
    os.makedirs(REPORT_DIR, exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    report["report_path"] = report_path

    return report


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python tools/quarantine_source.py <source> [--dry-run]")
        sys.exit(2)
    src = sys.argv[1]
    dry = "--dry-run" in sys.argv
    out = run(source=src, dry_run=dry)
    print(json.dumps(out, indent=2, ensure_ascii=False))
