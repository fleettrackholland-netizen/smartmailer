"""
tools/quarantine_ai_discovery.py

Marks all leads with source='ai_discovery' as status='quarantine'.

Rationale: 2026-05-12 audit (data/ai_discovery_quality_audit_2026-05-12.json)
found 81.2% of a 500-sample have no MX record. AI-generated domains are
unreliable; removing them from active send pool prevents bounce-rate damage
to sender reputation.

Idempotent: rows already in 'quarantine' are skipped (still counted).
Transaction-safe: single UPDATE inside a single connection.
"""
import json
import os
import sqlite3
import sys
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(PROJECT_ROOT, "data", "smartmailer_ultimate.db")
REPORT_PATH = os.path.join(
    PROJECT_ROOT, "data", "quarantine_ai_discovery_report.json"
)


def run(dry_run: bool = False) -> dict:
    if not os.path.exists(DB_PATH):
        raise FileNotFoundError(f"DB not found: {DB_PATH}")

    started_at = datetime.utcnow().isoformat() + "Z"
    conn = sqlite3.connect(DB_PATH)
    try:
        before_total = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE source='ai_discovery'"
        ).fetchone()[0]
        before_active = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE source='ai_discovery' "
            "AND (status IS NULL OR status NOT IN ('quarantine','opted_out','invalid'))"
        ).fetchone()[0]
        before_quarantined = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE source='ai_discovery' AND status='quarantine'"
        ).fetchone()[0]

        affected = 0
        if not dry_run and before_active > 0:
            cur = conn.execute(
                "UPDATE leads SET status='quarantine', "
                "updated_at=datetime('now') "
                "WHERE source='ai_discovery' "
                "AND (status IS NULL OR status NOT IN ('quarantine','opted_out','invalid'))"
            )
            affected = cur.rowcount
            conn.commit()

        after_quarantined = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE source='ai_discovery' AND status='quarantine'"
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
        "dry_run": dry_run,
        "ai_discovery_total": before_total,
        "ai_discovery_active_before": before_active,
        "ai_discovery_quarantined_before": before_quarantined,
        "ai_discovery_quarantined_after": after_quarantined,
        "rows_affected": affected,
        "active_leads_remaining_all_sources": total_active_all_sources,
        "active_by_source": [
            {"source": s or "(null)", "count": c}
            for s, c in sources_remaining_active
        ],
        "rationale": "2026-05-12 MX audit: 81.2% invalid in 500-sample.",
    }

    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    return report


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    out = run(dry_run=dry)
    print(json.dumps(out, indent=2, ensure_ascii=False))
