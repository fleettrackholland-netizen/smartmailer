"""
core/followup_engine.py — Follow-Up Sequence Engine (v5)
Gönderilen e-postalara otomatik takip zinciri — gelişmiş pazarlama.
3 aşama: Gün 3 (social proof + merak), Gün 7 (ROI + vaka çalışması), Gün 14 (urgency + FOMO).
Her aşama benzersiz pazarlama stratejileri kullanır ve önceki maillere atıfta bulunur.
"""
import json
import requests
from datetime import datetime, timedelta
from config import config
from core.logger import get_logger
from core.database import db
from core.api_guard import api_guard

log = get_logger("followup")

CLAUDE_API_URL = "https://api.anthropic.com/v1/messages"


# ═══════════════════════════════════════════════════════════════
# FOLLOW-UP PROMPT'LARI — Her aşama benzersiz pazarlama stratejisi
# ═══════════════════════════════════════════════════════════════

FOLLOWUP_PROMPTS = {
    # ─── STEP 1 (Gün 3): SOFT BUMP — geen nieuwe pitch ─────────
    1: """Je bent Agah Dogan, eigenaar van Fleet Track Holland. Je stuurde {days_ago} dagen geleden een korte vraag aan {company} ({sector}). Er kwam geen reactie.

Schrijf NU een KORTE bump — geen herhaling van de pitch, geen psychologische trigger, geen cijfers. Eén of twee zinnen, tipje-naar-boven van de oorspronkelijke mail.

VOORBEELD-TOON:
"Beste,
Korte bump op mijn bericht van vorige week. Past het misschien nu beter? Anders begrijp ik dat helemaal.

Vriendelijke groet,
Agah Dogan
Eigenaar - Fleet Track Holland
+31 6 27246429 · agah@fleettrackholland.nl"

EISEN:
- 30-50 woorden TOTAAL inclusief aanhef en ondertekening
- Geen euro-bedragen, geen percentages, geen 'GPS-tracking voor X'
- Geen 'gratis', 'actie', 'klik hier'
- Subject: kort, vraagteken aan eind (bijv. 'Bump?' of 'Past het beter?')
- Plain HTML — uitsluitend <p>-tags, geen tabellen, geen images, geen knoppen
- Body HTML: <p style="margin:0 0 12px;font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#222;line-height:1.55;">...</p> per alinea
- Ondertekening EXACT:
  Vriendelijke groet,
  Agah Dogan
  Eigenaar - Fleet Track Holland
  +31 6 27246429 · agah@fleettrackholland.nl

{previous_emails_context}

Antwoord ALLEEN in geldig JSON:
{{"subject": "...", "body_html": "<p>...</p>", "body_text": "..."}}
""",

    # ─── STEP 2 (Gün 7): NUTTIGE ARTEFACT — geen verzonnen case ───
    2: """Je bent Agah Dogan, eigenaar van Fleet Track Holland. Twee korte berichten naar {company} ({sector}) bleven onbeantwoord ({days_since_original} dagen geleden gestart).

Stuur NU een korte mail met EEN nuttige hint — geen verzonnen klantverhaal, geen fictieve ROI-cijfers. Bied iets concreets: een korte uitleg van wat de Belastingdienst-eisen voor ritregistratie inhouden, of een verwijzing naar het type apparaat dat past bij hun vlootomvang. Geen prijs noemen.

EISEN:
- 50-70 woorden TOTAAL
- Geen euro-bedragen, geen percentages, geen 'vergelijkbare bedrijven X' cijfers, geen '300+ klanten'
- Geen 'gratis', 'actie', 'klik hier', '100%', '!!!'
- Subject: <=45 tekens, archetypically 'Korte tip ritregistratie' of '1 ding over uw vloot'
- Plain HTML — uitsluitend <p>-tags, geen tabellen, geen images, geen knoppen
- Body HTML: <p style="margin:0 0 12px;font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#222;line-height:1.55;">...</p> per alinea
- Eén vraag aan het eind ('Past het komende week kort schakelen?')
- Ondertekening EXACT zoals in step 1

{previous_emails_context}

Antwoord ALLEEN in geldig JSON:
{{"subject": "...", "body_html": "<p>...</p>", "body_text": "..."}}
""",

    # ─── STEP 3 (Gün 14): SOFT BREAKUP — beleefd, binair ────────
    3: """Je bent Agah Dogan, eigenaar van Fleet Track Holland. {company} ({sector}) heeft op drie korte berichten niet gereageerd. Dit is het LAATSTE bericht — een beleefde afsluiting, GEEN urgentie, GEEN verlies-frame, GEEN FOMO.

Schrijf een KORTE breakup. Bied twee opties: 'ik sluit uw dossier' of 'stuur nog één antwoord en ik probeer het opnieuw later'. Respectvol, kort, mens-tegen-mens.

VOORBEELD-TOON:
"Beste,
Geen reactie ontvangen — niets aan de hand. Wilt u dat ik uw dossier sluit, of mag ik over een paar maanden opnieuw kort schakelen?
Een woord is genoeg.

Vriendelijke groet,
Agah Dogan
Eigenaar - Fleet Track Holland
+31 6 27246429 · agah@fleettrackholland.nl"

EISEN:
- 35-55 woorden TOTAAL inclusief ondertekening
- Geen euro-bedragen, geen percentages, geen 'verlies', geen 'concurrenten'
- Geen 'laatste kans', geen 'exclusief aanbod', geen urgentie-framing
- Subject: archetypically 'Sluit ik uw dossier?' of 'Een woord is genoeg'
- Plain HTML zoals step 1/2
- Ondertekening EXACT

{previous_emails_context}

Antwoord ALLEEN in geldig JSON:
{{"subject": "...", "body_html": "<p>...</p>", "body_text": "..."}}
""",
}


class FollowUpEngine:
    """3-aşamalı otomatik follow-up zinciri — gelişmiş pazarlama stratejileri."""

    def __init__(self):
        self._headers = {
            "x-api-key": config.ANTHROPIC_API_KEY,
            "content-type": "application/json",
            "anthropic-version": "2023-06-01",
        }

    # ─── ZAMANLANMIŞ FOLLOW-UP'LARI OLUŞTUR ────────────────────

    def schedule_followups(self, email: str, original_subject: str,
                           company: str, sector: str = "",
                           vehicles: str = "", campaign_id: str = ""):
        """İlk gönderim sonrası 3 follow-up zamanla."""
        if not config.FOLLOWUP_ENABLED:
            return

        now = datetime.now()
        steps = [
            (1, config.FOLLOWUP_DAY_1),  # Gün 3
            (2, config.FOLLOWUP_DAY_2),  # Gün 7
            (3, config.FOLLOWUP_DAY_3),  # Gün 14
        ]

        for step, days in steps:
            scheduled_at = (now + timedelta(days=days)).isoformat()
            db.schedule_followup(
                email=email,
                step=step,
                scheduled_at=scheduled_at,
                original_subject=original_subject,
                company=company,
                sector=sector,
                vehicles=vehicles,
                campaign_id=campaign_id,
            )

        log.info(f"[FOLLOWUP] 3 takip zamanlandı: {email} "
                 f"(Gün {config.FOLLOWUP_DAY_1}/{config.FOLLOWUP_DAY_2}"
                 f"/{config.FOLLOWUP_DAY_3})")

    # ─── ÖNCEKİ E-POSTALARI TOPLA (REFERANS İÇİN) ─────────────

    def _get_previous_emails_context(self, email: str, current_step: int) -> str:
        """Önceki e-postaların özetini döner — AI'ın atıfta bulunması için."""
        context_parts = []

        # Orijinal gönderilen e-postayı al
        try:
            sent = db.get_sent_email_content(email)
            if sent and sent.get("subject"):
                context_parts.append(
                    f"--- ILKE-MAIL (orijineel) ---\n"
                    f"Onderwerp: {sent.get('subject', '')}\n"
                    f"Verzonden op: {sent.get('sent_at', 'onbekend')}\n"
                    f"Samenvatting: E-mail over GPS tracking oplossingen voor hun vloot."
                )
        except Exception:
            pass

        # Önceki follow-up'ları al
        try:
            all_followups = db.get_followups_for_email(email)
            for fu in all_followups:
                if fu.get("step", 0) < current_step and fu.get("status") == "sent":
                    fu_subject = fu.get("subject", "")
                    fu_body = fu.get("body_text", "") or ""
                    # Sadece ilk 100 karakter
                    summary = fu_body[:150].replace("\n", " ").strip()
                    context_parts.append(
                        f"--- FOLLOW-UP {fu['step']} ---\n"
                        f"Onderwerp: {fu_subject}\n"
                        f"Samenvatting: {summary}..."
                    )
        except Exception:
            pass

        if context_parts:
            return "\n\nEERDER VERZONDEN E-MAILS (verwijs hier subtiel naar):\n" + "\n\n".join(context_parts)
        return "\n\n(Dit is de eerste follow-up, verwijs naar je originele e-mail.)"

    # ─── BEKLEYEN FOLLOW-UP'LARI İŞLE VE GÖNDER ────────────────

    def process_pending(self) -> list[dict]:
        """Zamanı gelen follow-up'ları üret, Brevo ile gönder ve DB güncelle."""
        from core.send_engine import SendEngine

        pending = db.get_pending_followups()
        results = []
        sender = SendEngine()

        log.info(f"[FOLLOWUP] {len(pending)} bekleyen follow-up işleniyor...")

        for fu in pending:
            email = fu["email"]
            step = fu["step"]

            # Yanıt geldiyse atla
            if db.has_response(email):
                db.update_followup_status(fu["id"], "skipped_replied")
                log.info(f"[FOLLOWUP] Atlandı (yanıt var): {email} step {step}")
                continue

            # Unsubscribe olduysa atla
            if db.is_unsubscribed(email):
                db.update_followup_status(fu["id"], "skipped_unsub")
                log.info(f"[FOLLOWUP] Atlandı (unsub): {email} step {step}")
                continue

            # E-posta açılmış ama yanıt yok → daha agresif follow-up
            has_opened = db.has_opened(email)

            try:
                # 1. AI ile follow-up e-postası üret
                draft = self._generate_followup(
                    email=email,
                    step=step,
                    original_subject=fu.get("original_subject", ""),
                    company=fu.get("company", ""),
                    sector=fu.get("sector", ""),
                    vehicles=fu.get("vehicles", ""),
                    has_opened=has_opened,
                )

                # 2. Brevo ile gönder
                from core.send_engine import EmailMessage
                msg = EmailMessage(
                    to_email=email,
                    to_name=fu.get("company", ""),
                    subject=draft["subject"],
                    html_body=draft["body_html"],
                    text_body=draft.get("body_text", ""),
                )
                send_result = sender.send(msg)

                if send_result.success:
                    # 3. DB güncelle — sent olarak işaretle
                    db.update_followup_status(
                        fu["id"], "sent",
                        subject=draft["subject"],
                        body_html=draft["body_html"],
                        body_text=draft.get("body_text", ""),
                    )
                    # 4. Sent log'a kaydet (follow-up olarak)
                    db.log_sent(
                        email=email,
                        company=fu.get("company", ""),
                        sector=fu.get("sector", ""),
                        subject=draft["subject"],
                        method=f"followup_step_{step}",
                        message_id=send_result.message_id or "",
                    )
                    log.info(f"[FOLLOWUP] ✅ Gönderildi: {email} step {step} — {draft['subject']}")
                    results.append({
                        "id": fu["id"],
                        "email": email,
                        "step": step,
                        "subject": draft["subject"],
                        "status": "sent",
                        "has_opened": has_opened,
                    })
                else:
                    error_msg = send_result.error or "Bilinmeyen hata"
                    log.error(f"[FOLLOWUP] ❌ Gönderilemedi: {email} — {error_msg}")
                    db.update_followup_status(fu["id"], "error")
                    results.append({
                        "id": fu["id"],
                        "email": email,
                        "step": step,
                        "status": "error",
                        "error": error_msg,
                    })

            except Exception as e:
                log.error(f"[FOLLOWUP] Üretim/gönderim hatası: {email} step {step} — {e}")
                db.update_followup_status(fu["id"], "error")

        log.info(f"[FOLLOWUP] İşlem tamamlandı: {len(results)} follow-up işlendi")
        return results

    # ─── AI FOLLOW-UP ÜRETİCİ (GELİŞMİŞ) ─────────────────────

    def _generate_followup(self, email: str, step: int, original_subject: str,
                           company: str, sector: str, vehicles: str,
                           has_opened: bool = False) -> dict:
        """Claude ile gelişmiş follow-up e-postası üret — önceki maillere atıfta bulunur."""

        # Önceki e-posta bağlamını al
        previous_context = self._get_previous_emails_context(email, step)

        # Gün hesapla
        days_map = {1: config.FOLLOWUP_DAY_1, 2: config.FOLLOWUP_DAY_2, 3: config.FOLLOWUP_DAY_3}
        days_ago = days_map.get(step, 3)
        days_since_original = days_ago

        # Benzer vloot büyüklüğü hesapla (case study için)
        try:
            veh_count = int(vehicles) if vehicles and str(vehicles).isdigit() else 30
        except (ValueError, TypeError):
            veh_count = 30
        vehicles_similar = max(10, veh_count + (-10 if veh_count > 30 else 5))

        prompt_template = FOLLOWUP_PROMPTS.get(step, FOLLOWUP_PROMPTS[1])
        user_prompt = prompt_template.format(
            original_subject=original_subject or "GPS tracking voor uw vloot",
            company=company or "uw bedrijf",
            sector=sector or "zakelijke dienstverlening",
            vehicles=vehicles or "meerdere",
            days_ago=days_ago,
            days_since_original=days_since_original,
            vehicles_similar=vehicles_similar,
            previous_emails_context=previous_context,
        )

        # E-posta açılmış ama yanıt yok — ekstra bilgi ekle
        if has_opened:
            user_prompt += (
                "\n\n🔔 BELANGRIJK: Deze persoon heeft je eerdere e-mail GEOPEND maar NIET gereageerd. "
                "Dit betekent dat er INTERESSE is. Verwijs hier subtiel naar, bijvoorbeeld: "
                "'Ik zag dat u mijn vorige bericht heeft bekeken...' of "
                "'Ik begrijp dat het druk is, maar uw interesse geeft aan dat...'"
            )

        payload = {
            "model": config.CLAUDE_MODEL,
            "max_tokens": 800,
            "messages": [{"role": "user", "content": user_prompt}],
        }

        resp = api_guard.call(payload, self._headers, timeout=30)
        if not resp or not resp.ok:
            raise Exception(f"Claude follow-up hatası: {resp.status_code if resp else 'guard blocked'}")

        raw = resp.json()["content"][0]["text"]

        json_str = raw
        if "```json" in raw:
            json_str = raw.split("```json")[1].split("```")[0]
        elif "```" in raw:
            json_str = raw.split("```")[1].split("```")[0]

        result = json.loads(json_str.strip())

        # Unsubscribe footer ekle (her follow-up'a) — Hollandse wettelijke footer
        unsub_url = config.UNSUBSCRIBE_URL
        footer_html = (
            f'<br><hr style="border:none;border-top:1px solid #eee;margin:20px 0">'
            f'<p style="font-size:11px;color:#999;line-height:1.4">'
            f'{config.COMPANY_NAME}<br>'
            f'<a href="{unsub_url}" style="color:#999">Uitschrijven</a></p>'
            f'<p style="margin:8px 0 4px;font-size:11px;color:#999;line-height:1.5;">'
            f'<a href="{unsub_url}" style="color:#999;text-decoration:underline;">'
            f'Afmelden voor dit project</a> &#8729; '
            f'<a href="{unsub_url}" style="color:#999;text-decoration:underline;">'
            f'Afmelden voor alle meldingen</a></p>'
            f'<p style="margin:0 0 4px;font-size:11px;color:#999;line-height:1.5;">'
            f'Hulp nodig? Beantwoord deze e-mail en ons team neemt contact met u op.</p>'
            f'<p style="margin:0;font-size:11px;color:#999;line-height:1.5;">'
            f'&copy; 2026 Rotterdam<br/>'
            f'<a href="https://www.fleettrackholland.nl/voorwaarden" '
            f'style="color:#999;text-decoration:underline;">Servicevoorwaarden</a> &#8729; '
            f'<a href="https://www.fleettrackholland.nl/privacy" '
            f'style="color:#999;text-decoration:underline;">Privacybeleid</a></p>'
        )
        result["body_html"] = result.get("body_html", "") + footer_html

        return result

    # ─── İSTATİSTİKLER ─────────────────────────────────────────

    def get_stats(self) -> dict:
        """Follow-up istatistikleri."""
        return db.get_followup_stats()

    def ping(self) -> bool:
        return True
