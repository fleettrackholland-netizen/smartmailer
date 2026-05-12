"""
agents/quality_agent.py — AI-Powered Email QC Agent (v3)
Claude AI ile akıllı kalite kontrolü + regex fallback.
Bağlamsal spam tespiti, profesyonellik tonu, CTA etkinliği değerlendirir.
"""
import re
import json
import requests
from dataclasses import dataclass, field
from config import config
from core.logger import get_logger
from core.api_guard import api_guard

log = get_logger("quality")

CLAUDE_API_URL = "https://api.anthropic.com/v1/messages"

# ─── Regex fallback için spam listesi ─────────────────────────
SPAM_WORDS = [
    "100%", "actie nu", "bestel nu", "goedkoopste",
    "aanbieding", "direct voordeel",
    "free", "guarantee", "click here", "buy now", "limited time",
    "congratulations", "winner", "cash prize", "urgent", "act now",
    "!!!", "$$$", "€€€",
]

CTA_PATTERNS = [
    r"mag ik", r"kunt u", r"wilt u", r"bent u beschikbaar",
    r"bellen", r"afspraak", r"offerte", r"demo",
    r"vrijblijvend", r"contact", r"aanvragen",
]


@dataclass
class QCResult:
    passed: bool
    score: int          # 0-100
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    method: str = "regex"  # "ai" veya "regex"
    feedback: str = ""     # AI'dan yapıcı geri bildirim


AI_QC_PROMPT = """Je bent een email deliverability + cold-outreach QC reviewer.
Beoordeel deze KORTE persoonlijke e-mail (50-80 woorden) op 10 criteria. Score per criterium 0-10.

CRITERIA (let op: dit is 1-op-1 plain-text outreach, GEEN newsletter):
1. spam_risk: Klinkt het als marketing of als een echte persoon?
   - Onacceptabel: euro-bedragen in body, percentages, "gratis", "actie", "klik hier", ALL-CAPS, !!! , overmatig links
2. plain_feel: Voelt het hand-typed? (geen <table>, geen <img>, geen knoppen, geen kleurige spans)
3. brevity: 50-80 woorden TOTAAL inclusief aanhef en afsluiting (10 = perfect, 0 = >150 of <30)
4. personal_salutation: "Beste {firstName}," of "Beste,". NOOIT "Dag {company},".
5. one_cta_reply: EEN vraag die om reply vraagt — geen "klik op de link", geen "vraag offerte aan", geen knop.
6. subject_diversity: 3 onderwerpen MOETEN drie verschillende archetypes zijn (vraag / observatie / bedrijf+woord). 3 paraphrases = score 2.
7. subject_quality: <=45 tekens, geen euro-teken, geen %, geen "GPS-tracking voor X".
8. signature_exact: Bevat "Agah Dogan", "Eigenaar - Fleet Track Holland", telefoon "+31 6 27246429", "agah@fleettrackholland.nl". Geen "Hans van der Berg".
9. dutch_grammar: Vlekkeloos zakelijk Nederlands, formeel "u".
10. no_fake_specifics: GEEN verzonnen cijfers ("EUR 2340/maand", "23% diefstal", "87% bestelt niet meer", "300+ klanten"). Generieke value framing OK; specifieke ROI claims fail.

ANTWOORD EXACT IN DIT JSON FORMAT:
{
    "scores": {
        "spam_risk":         {"score": 8, "comment": "..."},
        "plain_feel":        {"score": 9, "comment": "..."},
        "brevity":           {"score": 9, "comment": "..."},
        "personal_salutation": {"score": 9, "comment": "..."},
        "one_cta_reply":     {"score": 8, "comment": "..."},
        "subject_diversity": {"score": 7, "comment": "..."},
        "subject_quality":   {"score": 8, "comment": "..."},
        "signature_exact":   {"score": 10, "comment": "..."},
        "dutch_grammar":     {"score": 9, "comment": "..."},
        "no_fake_specifics": {"score": 9, "comment": "..."}
    },
    "total_score": 86,
    "passed": true,
    "issues": ["lijst van serieuze problemen"],
    "improvements": ["concrete verbetersugesties"],
    "summary": "Korte samenvatting"
}

Note: total_score is sum of all 10 scores * 1 (max 100). passed = total_score >= 70."""


class QualityAgent:

    MAX_WORDS = 100   # was 220 — new style is 50-80
    MIN_WORDS = 35    # was 40 — keep slight buffer

    def __init__(self):
        self._headers = {
            "x-api-key": config.ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

    # ─── AI-POWERED CHECK (PRIMARY) ───────────────────────────────

    def check(self, subject: str, body_text: str,
              company_name: str, body_html: str = "") -> QCResult:
        """
        Ana kalite kontrolü. Önce AI dener, başarısız olursa regex'e düşer.
        """
        try:
            return self._ai_check(subject, body_text, company_name, body_html)
        except Exception as e:
            log.warning(f"[QC] AI kontrol başarısız ({e}), regex'e düşüyor...")
            return self._regex_check(subject, body_text, company_name)

    def _ai_check(self, subject: str, body_text: str,
                  company_name: str, body_html: str = "") -> QCResult:
        """Claude AI ile akıllı kalite kontrolü."""

        user_prompt = f"""Beoordeel deze zakelijke cold e-mail:

ONDERWERP: {subject}

BEDRIJF: {company_name}

E-MAIL TEKST:
{body_text}

{"HTML VERSIE:" + chr(10) + body_html[:2000] if body_html else ""}
"""

        payload = {
            "model": config.CLAUDE_MODEL,
            "max_tokens": 800,
            "system": AI_QC_PROMPT,
            "messages": [{"role": "user", "content": user_prompt}],
        }

        resp = api_guard.call(payload, self._headers, timeout=30)
        if not resp or not resp.ok:
            raise Exception(f"Claude QC API hatası: {resp.status_code if resp else 'guard blocked'}")

        raw = resp.json()["content"][0]["text"]

        # JSON parse (Claude bazen markdown wrapping yapar)
        json_str = raw
        if "```json" in raw:
            json_str = raw.split("```json")[1].split("```")[0]
        elif "```" in raw:
            json_str = raw.split("```")[1].split("```")[0]

        data = json.loads(json_str.strip())

        total_score = data.get("total_score", 0)
        passed = total_score >= config.QC_MIN_SCORE
        issues = data.get("issues", [])
        improvements = data.get("improvements", [])
        summary = data.get("summary", "")

        # Log sonuçları
        scores_detail = data.get("scores", {})
        low_scores = {k: v for k, v in scores_detail.items()
                      if isinstance(v, dict) and v.get("score", 10) < 6}

        if passed:
            log.info(f"[QC AI] ✅ Geçti — Skor: {total_score}/100 | {company_name}")
        else:
            log.warning(f"[QC AI] ❌ Başarısız — Skor: {total_score}/100 | "
                        f"Düşük: {list(low_scores.keys())} | {company_name}")

        return QCResult(
            passed=passed,
            score=total_score,
            issues=issues,
            warnings=improvements,
            method="ai",
            feedback=summary,
        )

    # ─── REGEX FALLBACK ───────────────────────────────────────────

    def _regex_check(self, subject: str, body_text: str,
                     company_name: str) -> QCResult:
        """Regex tabanlı yedek kontrol (AI ulaşılamadığında)."""
        issues = []
        warnings = []
        score = 100

        body_lower = body_text.lower()
        subj_lower = subject.lower()

        # 1. Uzunluk
        word_count = len(body_text.split())
        if word_count > self.MAX_WORDS:
            issues.append(f"Çok uzun: {word_count} kelime (max {self.MAX_WORDS})")
            score -= 20
        if word_count < self.MIN_WORDS:
            issues.append(f"Çok kısa: {word_count} kelime (min {self.MIN_WORDS})")
            score -= 15

        # 2. Spam kelimeleri
        found_spam = [w for w in SPAM_WORDS
                      if w.lower() in body_lower or w.lower() in subj_lower]
        if found_spam:
            issues.append(f"Spam kelimeleri: {found_spam}")
            score -= 25

        # 3. Konu ALL CAPS?
        caps_ratio = sum(1 for c in subject if c.isupper()) / max(len(subject), 1)
        if caps_ratio > 0.5:
            issues.append("Konu satırı çok büyük harf içeriyor")
            score -= 15

        # 4. Uzun konu
        if len(subject) > 70:
            warnings.append(f"Konu {len(subject)} karakter — bazı istemcilerde kısalabilir")
            score -= 5

        # 5. Şirket ismi
        company_words = company_name.lower().split()
        if not any(w in body_lower for w in company_words if len(w) > 3):
            warnings.append("Şirket ismi email gövdesinde geçmiyor")
            score -= 10

        # 6. CTA
        has_cta = any(re.search(p, body_lower) for p in CTA_PATTERNS)
        if not has_cta:
            issues.append("Net bir CTA bulunamadı")
            score -= 20

        # 7. Afmelden — soft check. Template footer + Brevo List-Unsubscribe header zorlu.
        if "afmelden" not in body_lower and "unsubscribe" not in body_lower:
            warnings.append("Afmelden in body afwezig — template/footer moet header List-Unsubscribe leveren")
            score -= 5

        # 8. Link sayısı
        link_count = body_lower.count("http")
        if link_count > 3:
            warnings.append(f"{link_count} link — spam filtresi tetiklenebilir")
            score -= 5

        passed = len(issues) == 0 and score >= config.QC_MIN_SCORE

        if passed:
            log.info(f"[QC regex] ✅ Geçti — Skor: {score}/100 | {company_name}")
        else:
            log.warning(f"[QC regex] ❌ Başarısız — Skor: {score}/100 | "
                        f"Sorunlar: {issues} | {company_name}")

        return QCResult(passed=passed, score=score,
                        issues=issues, warnings=warnings, method="regex")

    def ping(self) -> bool:
        return True
