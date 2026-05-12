"""
agents/copywriter_agent.py — Elite B2B Sales Copywriter (v3 — Master Edition)
30 yıllık B2B satış deneyimi. Cialdini'nin 6 ikna prensibi, Kahneman'ın
Prospect Theory'si, altın oran HTML tasarım, ve self-learning mekanizmasıyla
mükemmel satış e-postaları üretir.
"""
import re
import json
import requests
from dataclasses import dataclass
from config import config
from core.logger import get_logger
from core.api_guard import api_guard

log = get_logger("copywriter")

CLAUDE_API_URL = "https://api.anthropic.com/v1/messages"


@dataclass
class EmailDraft:
    subject_a: str
    subject_b: str
    subject_c: str
    body_html: str
    body_text: str
    chosen_subject: str = ""

    def __post_init__(self):
        if not self.chosen_subject:
            self.chosen_subject = self.subject_a


# ─── SEKTÖR BAĞLAMI — DERİN PAZAR BİLGİSİ ──────────────────────

SECTOR_CONTEXT = {
    "transport": {
        "pain_points": "chauffeurs die niet opnemen, klanten die bellen voor ETA, "
                       "ritregistratie bijhouden, brandstofkosten bewaken, "
                       "naleving rij- en rusttijden, privégebruik bedrijfswagens",
        "hook_hint": "ETA-calls van klanten, twee vestigingen beheren, groeiende vloot",
        "urgency": "Met een groeiende vloot worden routes complexer — elk uur telt",
        "visual_suggestion": "vrachtwagen op de snelweg met GPS-indicator, routekaart",
        "roi_example": "Een transportbedrijf met 15 vrachtwagens bespaarde €2.340/maand op brandstof",
        "psychological_angle": "authority + social_proof",
        "accent_color": "#1a73e8",
    },
    "bouw": {
        "pain_points": "diefstal van bouwvoertuigen buiten werktijd, "
                       "projectlocaties bewaken, materieel traceren, "
                       "uren op locatie vastleggen, ongeautoriseerd gebruik",
        "hook_hint": "voertuigdiefstal, nachtelijk alarm, bouwplaats beveiliging",
        "urgency": "Diefstal van bouwmaterieel steeg 23% in het afgelopen jaar",
        "visual_suggestion": "bouwvoertuig met beveiligingsschild, nachtelijke bewaking",
        "roi_example": "Een bouwbedrijf voorkwam €45.000 aan diefstal in 6 maanden",
        "psychological_angle": "loss_aversion + scarcity",
        "accent_color": "#e8a31a",
    },
    "schoonmaak": {
        "pain_points": "privégebruik van bedrijfsbusjes, routes optimaliseren, "
                       "medewerkers bijhouden op meerdere locaties, "
                       "klachten over te late aankomst",
        "hook_hint": "privégebruik busjes, routes niet efficiënt, locatiecontrole",
        "urgency": "Ongeautoriseerd gebruik kost gemiddeld €380/maand per voertuig",
        "visual_suggestion": "bedrijfsbusje met route-optimalisatie overlay",
        "roi_example": "Een schoonmaakbedrijf bespaarde 4.2 uur per dag door route-optimalisatie",
        "psychological_angle": "reciprocity + commitment",
        "accent_color": "#34a853",
    },
    "thuiszorg": {
        "pain_points": "veiligheid van zorgmedewerkers, ritregistratie voor "
                       "zorgverzekeraars, routes efficiënt plannen, "
                       "aanrijtijden verkorten bij spoed",
        "hook_hint": "medewerkersveiligheid, declaratie ritregistratie",
        "urgency": "Zorgverzekeraars eisen nauwkeurige ritregistratie — boetes bij afwijking",
        "visual_suggestion": "zorgmedewerker op pad met veiligheidsoverzicht",
        "roi_example": "Een thuiszorgorganisatie bespaarde €1.800/maand op ritdeclaraties",
        "psychological_angle": "authority + liking",
        "accent_color": "#4285f4",
    },
    "catering": {
        "pain_points": "bezorgers op tijd laten aankomen, klanten informeren "
                       "over bezorgtijd, routes optimaliseren, koude keten bewaken",
        "hook_hint": "late bezorgingen, klachten over timing, routeplanning",
        "urgency": "87% van klanten bestelt niet meer na twee late bezorgingen",
        "visual_suggestion": "bezorgwagen met live tracking indicator op kaart",
        "roi_example": "Een cateringbedrijf verhoogde klanttevredenheid met 34% door live tracking",
        "psychological_angle": "social_proof + scarcity",
        "accent_color": "#ea4335",
    },
    "logistiek": {
        "pain_points": "laad- en lostijden optimaliseren, chauffeurs aansturen, "
                       "klanten real-time informeren, brandstofkosten beheersen",
        "hook_hint": "wachttijden bij klanten, brandstofverspilling, ETA-beloftes",
        "urgency": "Elke minuut onnodig stilstaan kost €0,80 aan operationele kosten",
        "visual_suggestion": "logistiek dashboard met vlootoverzicht",
        "roi_example": "Een logistiek bedrijf verminderde wachttijden met 40%",
        "psychological_angle": "authority + commitment",
        "accent_color": "#1a73e8",
    },
    "koerier": {
        "pain_points": "bezorgtijden halen, pakketten traceren, rijgedrag bewaken, "
                       "klachten over gemiste afleveringen",
        "hook_hint": "vertraagde bezorging, klachten, ritregistratie voor fiscus",
        "urgency": "Elke mislukte bezorgpoging kost gemiddeld €4,50 aan extra kosten",
        "visual_suggestion": "koerierswagen met pakkettracking dashboard",
        "roi_example": "Een koeriersdienst verminderde mislukte bezorgingen met 62%",
        "psychological_angle": "loss_aversion + reciprocity",
        "accent_color": "#ff6d01",
    },
}

DEFAULT_CONTEXT = {
    "pain_points": "voertuigen bijhouden, ritregistratie, brandstof besparen, "
                   "privégebruik voorkomen, onderhoud plannen",
    "hook_hint": "efficiëntie verbeteren, kosten verlagen, overzicht behouden",
    "urgency": "Bedrijven die GPS-tracking gebruiken besparen gemiddeld 15-25% op vlootkosten",
    "visual_suggestion": "bedrijfswagen met GPS tracking interface",
    "roi_example": "Bedrijven besparen gemiddeld €200 per voertuig per maand",
    "psychological_angle": "social_proof + authority",
    "accent_color": "#e8600a",
}


# ═══════════════════════════════════════════════════════════════════
# MASTER SYSTEM PROMPT — 30 JAAR B2B SALES EXPERTISE
# ═══════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """Je bent Agah Dogan, eigenaar van Fleet Track Holland (Rotterdam). Je stuurt EEN persoonlijke e-mail naar een ondernemer — geen campagne, geen marketing. Schrijf zoals je een collega zou tippen: kort, direct, menselijk.

═══ DOEL ═══
Een KORTE reactie uitlokken — geen klik, geen demo-aanvraag. Eén zinnetje terug ("ja", "nee", "stuur info") is winst.

═══ TOON ═══
- Hand-typed gevoel: 50-80 woorden TOTAAL (inclusief aanhef en afsluiting)
- Geen marketing-jargon, geen Cialdini-stapeling, geen "vergelijkbare bedrijven", geen "bespaar"-claims
- Zoals een mens 's ochtends typt voor de koffie koud wordt
- Formeel Nederlands: "u" niet "je", maar warm

═══ STRUCTUUR (5 onderdelen, ALLE kort) ═══
1. AANHEF — `Beste {firstName},` als naam bekend; anders `Beste,` (NOOIT `Dag {bedrijfsnaam},`)
2. EEN-ZIN OPENING — sectorspecifieke observatie of vraag, ZONDER cijfers
3. EEN-ZIN VOORSTEL — wat Fleet Track Holland doet, in normaal Nederlands, ZONDER prijs of percentage
4. EEN-VRAAG CTA — laagdrempelig, vraagt om reply (niet klik). Voorbeeld: "Past het komende week kort schakelen (10 min)?"
5. ONDERTEKENING — exact als hieronder

═══ ONDERTEKENING (verplicht, exact format) ═══
Vriendelijke groet,
Agah Dogan
Eigenaar — Fleet Track Holland
+31 6 27246429 · sales@fleettrackholland.nl

═══ VERBODEN — DEAL-BREAKERS ═══
- Geen `€`-bedragen, geen percentages, geen `300+ klanten`, geen verzonnen ROI-cijfers
- Geen woorden: "gratis", "actie", "klik hier", "garantie", "100%", "!!!", "exclusief aanbod"
- Geen zin begint met "GPS-tracking voor" of "Hoe X ... ?"
- Geen HOOFDLETTER-secties zoals "HET PROBLEEM" of "RESULTAAT"
- Geen `▸` glyph, geen bullet-lijst, geen em-dash spam
- Geen `<img>`, geen `<table>`, geen kleurige spans, geen inline backgrounds
- Geen CTA-knop, geen logo-strip
- Geen `Hans van der Berg` — die persona bestaat niet meer

═══ HTML-OUTPUT-FORMAAT ═══
Het body_html bestaat UITSLUITEND uit een reeks `<p>`-tags met deze minimale styling:
`<p style="margin:0 0 12px;font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#222;line-height:1.55;">…</p>`
Geen tabellen, geen images, geen knoppen, geen gradiënten. Zwart op wit.

═══ SUBJECT — 3 VERSCHILLENDE ARCHETYPEN (verplicht, niet 3 paraphrases) ═══
- SUBJECT_A — KORTE VRAAG (3-5 woorden, eindigt met `?`)
- SUBJECT_B — TWEE-WOORD OBSERVATIE (geen leestekens)
- SUBJECT_C — BEDRIJF + EEN WOORD (≤5 woorden, bedrijfsnaam erin)
- Alle ≤45 tekens, geen `€`, geen percentage, geen "GPS-tracking voor".

═══ ANTWOORD FORMAT — EXACT DIT ═══
SUBJECT_A: [korte vraag, eindigt met ?]
SUBJECT_B: [twee-woord observatie]
SUBJECT_C: [bedrijf + een woord]
---HTML---
[reeks <p>-tags zoals hierboven beschreven]
---TEXT---
[platte tekst, identiek qua zinnen, zonder HTML-tags]"""


class CopywriterAgent:

    def __init__(self):
        self._headers = {
            "x-api-key": config.ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        self._winning_style_cache = None
        self._cache_time = 0
        log.info("Copywriter ajani hazır (v3 — Master Edition, 30yr expertise).")

    # ─── SELF-LEARNING: KAZANAN STİLDEN ÖĞREN ──────────────────

    def _get_winning_style(self) -> str:
        """DB'den yanıt gelen maillerin stilini analiz eder — self-learning."""
        import time
        # Cache 30 dakika
        if self._winning_style_cache and (time.time() - self._cache_time) < 1800:
            return self._winning_style_cache

        try:
            from core.database import db
            # Cevap gelen mailleri çek (interested + question)
            interested = []
            try:
                with db._conn() as conn:
                    rows = conn.execute("""
                        SELECT s.subject, s.ab_variant, r.classification,
                               d.body_text, l.sector
                        FROM responses r
                        JOIN sent_log s ON r.email = s.email
                        LEFT JOIN drafts d ON r.email = d.email
                        LEFT JOIN leads l ON r.email = l.email
                        WHERE r.classification IN ('interested', 'question')
                        ORDER BY r.classified_at DESC LIMIT 10
                    """).fetchall()
                    interested = [dict(r) for r in rows]
            except Exception:
                pass

            if not interested:
                self._winning_style_cache = ""
                self._cache_time = time.time()
                return ""

            # Kazanan tarzı özetle
            subjects = [r.get("subject", "") for r in interested if r.get("subject")]
            variants = [r.get("ab_variant", "") for r in interested if r.get("ab_variant")]
            sectors = [r.get("sector", "") for r in interested if r.get("sector")]

            style_info = f"""
═══ SELF-LEARNING DATA (succesvolle e-mails die reactie opleveren) ═══
Aantal succesvolle e-mails: {len(interested)}
Winnende onderwerplijnen: {'; '.join(subjects[:5])}
Winnende A/B varianten: {', '.join(variants)}
Sectoren met respons: {', '.join(set(sectors))}
INSTRUCTIE: Leer van deze succesvolle patronen. Gebruik vergelijkbare toon,
lengte en onderwerpstijl. Pas je aan op basis van wat WERKT.
═══════════════════════════════════════════════════════════════════════"""

            self._winning_style_cache = style_info
            self._cache_time = time.time()
            log.info(f"[Copywriter] Self-learning: {len(interested)} succesvolle e-mails geanalyseerd")
            return style_info

        except Exception as e:
            log.warning(f"[Copywriter] Self-learning data fout: {e}")
            return ""

    # ─── ANA YAZIM METODU ───────────────────────────────────────

    def write(self, lead: dict, intel_context: str = "") -> EmailDraft:
        company  = lead.get("Company", lead.get("company", "")) or ""
        sector   = (lead.get("Sector") or lead.get("sector") or "transport").lower()
        location = lead.get("Location", lead.get("location", "Nederland"))
        vehicles = lead.get("Vehicles", lead.get("vehicles", 0))
        contact_person = lead.get("contact_person") or lead.get("ContactPerson") or ""
        first_name = ""
        if contact_person:
            first_name = str(contact_person).strip().split()[0] if str(contact_person).strip() else ""

        ctx = SECTOR_CONTEXT.get(sector, DEFAULT_CONTEXT)
        # Accent retained for downstream compatibility only — new prompt produces black-on-white
        accent_color = ctx.get("accent_color", "#222222")

        try:
            v_count = int(vehicles)
        except (ValueError, TypeError):
            v_count = 0

        # --- ECONOMIC MODE CHECK ---
        if not config.USE_AI_COPYWRITER:
            log.info(f"[Copywriter] Economic Mode: Using static template for {company}")
            return self._static_fallback(company, sector, v_count, accent_color, ctx)

        # Self-learning data (sector-specific tone winners only)
        winning_style = self._get_winning_style()

        # Intel context — recon report, if any
        intel_section = ""
        if intel_context:
            intel_section = (
                "\n═══ DEEP INTELLIGENCE (ReconAgent — gebruik voor 1 zin personalisatie) ═══\n"
                f"{intel_context}\n"
                "═══════════════════════════════════════════════════════════════\n"
            )

        # Sector hook — one fragment only, no fabricated stats
        sector_pain_map = {
            "transport":  "transport met dagelijkse routes en ETA's",
            "bouw":       "bouw met materieel op meerdere locaties",
            "schoonmaak": "schoonmaak met busjes op route",
            "logistiek":  "logistiek met meerdere ophaal- en leverbeurten",
            "koerier":    "koerierdiensten met krappe afleveringsvensters",
            "catering":   "catering met versgevoelige bezorgingen",
            "thuiszorg":  "thuiszorg met ritregistratie voor zorgverzekeraar",
        }
        sector_hint = sector_pain_map.get(sector, "MKB-vloot in Nederland")

        user_prompt = f"""Schrijf ÉÉN korte persoonlijke e-mail (50-80 woorden TOTAAL) volgens de regels van het system-prompt.

LEAD:
- Bedrijf: {company or '(onbekend)'}
- Contactpersoon: {contact_person or '(onbekend)'}
- Voornaam voor aanhef: {first_name or '(onbekend — gebruik dan "Beste,")'}
- Sector-context fragment (1 stuk gebruiken in opening): {sector_hint}
- Locatie: {location}
- Geschatte voertuigen: {v_count if v_count > 0 else 'onbekend (NIET noemen)'}
{intel_section}
{winning_style}

LET OP:
- Geen euro's, percentages of bullet-lijsten. Geen logo, geen knop.
- Aanhef: `Beste {first_name},` (als naam bekend) OF `Beste,` (anders). NIET `Dag {company},`.
- Sluiting EXACT: "Vriendelijke groet,\\nAgah Dogan\\nEigenaar — Fleet Track Holland\\n+31 6 27246429 · sales@fleettrackholland.nl"
- Output volgens het EXACT formaat uit het system-prompt (SUBJECT_A/B/C + ---HTML--- + ---TEXT---)."""

        log.info(f"[Copywriter v4] Plain note → {company or '(no company)'} ({sector}, {location})")

        payload = {
            "model": config.CLAUDE_MODEL,
            "max_tokens": 4000,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": user_prompt}],
        }

        resp = api_guard.call(payload, self._headers, timeout=60)
        if not resp or not resp.ok:
            status = resp.status_code if resp else 'guard blocked'
            log.warning(f"[Copywriter] AI hatası: {status} — static template fallback kullanılıyor")
            return self._static_fallback(company, sector, v_count, accent_color, ctx)
        raw = resp.json()["content"][0]["text"]
        return self._parse(raw, company)

    def rewrite(self, draft: EmailDraft, feedback: list[str]) -> EmailDraft:
        """QC feedback'e göre taslağı yeniden yazar."""
        feedback_text = "\n".join(f"- {f}" for f in feedback)

        prompt = f"""De volgende e-mail heeft de kwaliteitscontrole NIET gehaald.
Herschrijf de e-mail als een 30-jarige marketing veteraan.
Los ALLE problemen op en maak de e-mail BETER dan het origineel.

PROBLEMEN:
{feedback_text}

HUIDIGE ONDERWERP: {draft.chosen_subject}

HUIDIGE TEKST:
{draft.body_text}

Geef het antwoord in EXACT hetzelfde formaat:
SUBJECT_A: [onderwerp — curiosity gap]
SUBJECT_B: [onderwerp — loss aversion]
SUBJECT_C: [onderwerp — social proof]
---HTML---
[verbeterde premium HTML e-mail met golden ratio layout]
---TEXT---
[verbeterde platte tekst]"""

        log.info(f"[Copywriter v3] Rewrite — QC sorunları: {feedback}")

        payload = {
            "model": config.CLAUDE_MODEL,
            "max_tokens": 4000,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": prompt}],
        }

        resp = api_guard.call(payload, self._headers, timeout=60)
        if not resp or not resp.ok:
            raise Exception(f"Claude API rewrite hatası: {resp.status_code if resp else 'guard blocked'}")

        raw = resp.json()["content"][0]["text"]
        company = draft.chosen_subject.split("—")[0].strip() if "—" in draft.chosen_subject else ""
        return self._parse(raw, company)

    def _parse(self, raw: str, company: str) -> EmailDraft:
        lines = raw.strip().splitlines()
        subject_a = subject_b = subject_c = ""
        html_lines = []
        text_lines = []
        mode = "header"

        for line in lines:
            if line.startswith("SUBJECT_A:"):
                subject_a = line.replace("SUBJECT_A:", "").strip()
            elif line.startswith("SUBJECT_B:"):
                subject_b = line.replace("SUBJECT_B:", "").strip()
            elif line.startswith("SUBJECT_C:"):
                subject_c = line.replace("SUBJECT_C:", "").strip()
            elif line.strip() == "---HTML---":
                mode = "html"
            elif line.strip() == "---TEXT---":
                mode = "text"
            elif line.strip() == "---":
                if mode == "header":
                    mode = "html"
            elif mode == "html":
                html_lines.append(line)
            elif mode == "text":
                text_lines.append(line)

        body_html = "\n".join(html_lines).strip()
        body_text = "\n".join(text_lines).strip()

        # Strip non-HTML content before actual HTML
        if body_html:
            html_start = re.search(r'<(!DOCTYPE|html|head|body|div|table)', body_html, re.IGNORECASE)
            if html_start:
                body_html = body_html[html_start.start():]

        # Fallback
        if not body_html and body_text:
            body_html = self._to_html(body_text)
        elif not body_html and not body_text:
            body_text = raw.strip()
            body_html = self._to_html(body_text)

        if not body_text and body_html:
            body_text = re.sub(r'<[^>]+>', '', body_html)
            body_text = re.sub(r'\s+', ' ', body_text).strip()

        # Subject fallback — diverse archetypes, no marketing patterns
        subject_a = subject_a or "Vraag over uw vloot?"
        subject_b = subject_b or "Kort vraagje"
        subject_c = subject_c or (f"Voor {company}: 1 vraag" if company else "Eén vraag")

        return EmailDraft(
            subject_a=subject_a,
            subject_b=subject_b,
            subject_c=subject_c,
            body_text=body_text,
            body_html=body_html,
        )

    def _static_fallback(self, company: str, sector: str, v_count: int,
                         accent_color: str, ctx: dict) -> EmailDraft:
        """AI unavailable — short hand-typed Dutch note, no marketing tone."""
        log.info(f"[Copywriter] Static fallback: {company} ({sector})")

        # Sector-specific one-liner observation (no fake stats)
        sector_obs_map = {
            "transport": "transport richting Nederland/België",
            "bouw":      "bouw met materieel op meerdere locaties",
            "schoonmaak": "schoonmaak met busjes op route",
            "logistiek": "logistiek met dagelijkse leverbeurten",
            "koerier":   "koerierdiensten met krappe ETA's",
            "catering":  "catering met versgevoelige bezorgingen",
            "thuiszorg": "thuiszorg met routes en ritregistratie",
        }
        obs = sector_obs_map.get(sector, "uw sector met een vaste vloot")

        # Body — 5 short lines, plain text feel
        sender_name  = getattr(config, "SENDER_NAME", "Agah Dogan")
        sender_title = getattr(config, "SENDER_TITLE", "Eigenaar — Fleet Track Holland")
        sender_email = getattr(config, "SENDER_EMAIL", "sales@fleettrackholland.nl")
        sender_phone = getattr(config, "COMPANY_PHONE", "+31627246429")

        p_style = ('margin:0 0 12px;font-family:Arial,Helvetica,sans-serif;'
                   'font-size:14px;color:#222;line-height:1.55;')

        salutation = f"Beste{(' ' + company.split()[0]) if company else ''},"
        opening = f"Ik viel over uw bedrijf — {obs}, klopt dat?"
        proposition = ("Wij regelen GPS-tracking en sluitende ritregistratie voor MKB-vloten in Nederland.")
        cta = "Past het komende week kort schakelen (10 min)?"
        signoff_line_1 = "Vriendelijke groet,"
        signoff_name = sender_name
        signoff_title = sender_title
        signoff_contact = f"{sender_phone} · {sender_email}"

        body_html = (
            f'<p style="{p_style}">{salutation}</p>'
            f'<p style="{p_style}">{opening}</p>'
            f'<p style="{p_style}">{proposition}</p>'
            f'<p style="{p_style}">{cta}</p>'
            f'<p style="{p_style}">{signoff_line_1}<br>'
            f'{signoff_name}<br>{signoff_title}<br>{signoff_contact}</p>'
        )

        body_text = (
            f"{salutation}\n\n"
            f"{opening}\n\n"
            f"{proposition}\n\n"
            f"{cta}\n\n"
            f"{signoff_line_1}\n{signoff_name}\n{signoff_title}\n{signoff_contact}\n"
        )

        # 3 distinct subject archetypes (question / two-word / company+word)
        subject_a = "Vraag over uw vloot?"
        subject_b = "Kort vraagje"
        subject_c = f"Voor {company}: 1 vraag" if company else "Eén vraag"

        return EmailDraft(
            subject_a=subject_a, subject_b=subject_b, subject_c=subject_c,
            body_html=body_html, body_text=body_text,
        )

    def _to_html(self, text: str) -> str:
        """Fallback: plain text → styled body-only HTML (template wraps it)."""
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        html_parts = []
        for p in paragraphs:
            lines = p.split("\n")
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                if line.startswith(("▸", "-", "•")):
                    clean = line.lstrip("▸-• ").strip()
                    html_parts.append(
                        f'<p style="margin:0 0 8px;font-size:14px;color:#333;line-height:1.6;">'
                        f'<span style="color:#e8600a;font-weight:bold;">▸</span> {clean}</p>'
                    )
                else:
                    html_parts.append(
                        f'<p style="margin:0 0 16px;font-size:15px;color:#333;line-height:1.7;">{line}</p>'
                    )
        return "\n".join(html_parts)

    def ping(self) -> bool:
        return True

