"""
agents/compliance_agent.py — AVG / GDPR Uyum Kontrolü
Email validator yerine regex kullanır (bağımlılık yok).
"""
import csv
import os
import re
from config import config
from core.logger import get_logger

log = get_logger("compliance")

EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")

# Consumer/personal domains — NOT hard-blocked any more. NL SMB owners often use these.
# Treated as a risk signal + counted for throttle (max 25% of daily volume).
CONSUMER_DOMAINS = {
    "gmail.com", "hotmail.com", "yahoo.com", "outlook.com",
    "live.com", "icloud.com", "me.com", "msn.com",
    "ziggo.nl", "kpnmail.nl", "home.nl", "hetnet.nl", "planet.nl",
    "upcmail.nl", "chello.nl", "tele2.nl",
}

# Subject + body anti-patterns (post-AI safety net).
# If any of these match, send must be rejected and copy regenerated.
FORBIDDEN_PATTERNS = [
    (re.compile(r"€\s?\d", re.IGNORECASE),                            "Bevat euro-bedrag (€<cijfer>) — verboden in cold body"),
    (re.compile(r"\b\d{1,3}\s?%"),                                    "Bevat percentage — verboden (vermijdt fake stat)"),
    (re.compile(r"\bgratis\b", re.IGNORECASE),                        "Bevat woord 'gratis' — spam trigger"),
    (re.compile(r"\bactie\b", re.IGNORECASE),                         "Bevat woord 'actie' — promotional trigger"),
    (re.compile(r"\bklik\s+hier\b", re.IGNORECASE),                   "Bevat 'klik hier' — promotional CTA"),
    (re.compile(r"\b100\s?%\b"),                                      "Bevat '100%' — spam pattern"),
    (re.compile(r"!{2,}"),                                            "Meerdere uitroeptekens (!!) — spam pattern"),
    (re.compile(r"\bGPS-?tracking\s+voor\s+\S", re.IGNORECASE),       "Subject begint met overused pattern 'GPS-tracking voor X'"),
    (re.compile(r"HET\s+PROBLEEM|HET\s+RESULTAAT|DE\s+OPLOSSING"),    "Caps-section headers — newsletter pattern"),
    (re.compile(r"▸"),                                                "Bevat '▸' glyph — banned brochure bullet"),
    (re.compile(r"Hans\s+van\s+der\s+Berg", re.IGNORECASE),           "Bevat oude persona naam — moet Agah Dogan zijn"),
]


class ComplianceAgent:

    def __init__(self):
        self._unsubscribe = self._load_unsubscribe()
        log.info(f"Compliance ajani hazır. Opt-out: {len(self._unsubscribe)} adres.")

    def _load_unsubscribe(self) -> set:
        path = config.UNSUBSCRIBE_FILE
        if not os.path.exists(path):
            return set()
        emails = set()
        with open(path, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("email"):
                    emails.add(row["email"].strip().lower())
        return emails

    def is_ok_to_send(self, email: str) -> tuple[bool, str]:
        email = email.strip().lower()

        if not EMAIL_RE.match(email):
            return False, f"Geçersiz format: {email}"

        if email in self._unsubscribe:
            return False, f"Opt-out listesinde: {email}"

        # Personal/consumer domain: soft signal, NOT a block. Caller (sending_strategist)
        # is responsible for throttling consumer domains to ≤25% of daily volume.
        domain = email.split("@")[-1]
        if domain in CONSUMER_DOMAINS:
            log.info(f"[Compliance] Consumer domain (toegestaan, geteld voor throttle): {email}")

        return True, ""

    def is_consumer_domain(self, email: str) -> bool:
        """True als de recipient een consumer-domain heeft (voor throttle counter)."""
        domain = email.strip().lower().split("@")[-1]
        return domain in CONSUMER_DOMAINS

    def check_content_patterns(self, subject: str, body_text: str) -> tuple[bool, list[str]]:
        """Post-AI safety net. Returns (ok, issues). Reject + regen if not ok."""
        issues: list[str] = []
        haystack = f"{subject or ''}\n{body_text or ''}"
        for pattern, reason in FORBIDDEN_PATTERNS:
            if pattern.search(haystack):
                issues.append(reason)
        return (not issues), issues

    def add_unsubscribe(self, email: str, reason: str = "user_request"):
        email = email.strip().lower()
        self._unsubscribe.add(email)
        path = config.UNSUBSCRIBE_FILE
        exists = os.path.exists(path)
        with open(path, "a", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["email", "reason", "date"])
            if not exists:
                w.writeheader()
            from datetime import datetime
            w.writerow({"email": email, "reason": reason,
                        "date": datetime.now().isoformat()})
        log.info(f"Opt-out kaydedildi: {email}")

    def ping(self) -> bool:
        return True
