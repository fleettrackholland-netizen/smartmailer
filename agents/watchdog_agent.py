"""
agents/watchdog_agent.py — Gerçek Sistem Gözetleme + Alarm Sistemi (v2.0)
Tüm sistem sağlığını izler, sorunları tespit eder,
EMAIL + WHATSAPP ile alarm gönderir, ve auto-recovery yapar.

ARTIK SADECE GÖRÜNTÜ DEĞİL — GERÇEK BİR GÖZETLEMECİ!
"""
import os
import time
import json
import threading
import traceback
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional
from core.logger import get_logger

log = get_logger("watchdog")


@dataclass
class HealthStatus:
    name: str
    status: str       # "OK" | "WARNING" | "CRITICAL"
    detail: str = ""
    checked_at: datetime = field(default_factory=datetime.now)

    def is_ok(self)       -> bool: return self.status == "OK"
    def is_warning(self)  -> bool: return self.status == "WARNING"
    def is_critical(self) -> bool: return self.status == "CRITICAL"


class WatchdogAgent:
    """
    Watchdog v2.0 — GERÇEK Sistem Gözetleme + Alarm Sistemi
    
    Kontroller:
    - Pipeline heartbeat (son 30 dk'da çalıştı mı?)
    - Lead keşfi (son 24 saatte lead bulundu mu?)
    - Email gönderimi (izin verilen saatlerde gönderim var mı?)
    - DB bağlantısı (gerçek query)
    - API anahtarları (gerçek API call)
    - Disk alanı
    
    Alarmlar:
    - Email alarm (Brevo API → doganagahm@gmail.com)
    - WhatsApp alarm (OpenClaw API → Hetzner VPS)
    - Günlük sabah raporu (08:00)
    """

    CHECK_INTERVAL_SEC = 300   # Her 5 dakikada kontrol
    DAILY_REPORT_DONE = False

    def __init__(self, agents: dict = None, config=None):
        self._agents = agents or {}
        self._config = config

        if config is None:
            from config import config as cfg
            self._config = cfg

        self._thread: Optional[threading.Thread] = None
        self._running = False

        # İstatistikler
        self._check_count = 0
        self._recovery_count = 0
        self._critical_count = 0
        self._start_time = datetime.now()

        # Alarm cooldown — aynı alarm tekrar tekrar gönderilmesin
        self._last_alarm_at: dict[str, datetime] = {}
        self._alarm_cooldown = timedelta(minutes=self._config.ALARM_COOLDOWN_MIN)

        # Kampanya metrikleri
        self.sent_count = 0
        self.failed_count = 0
        self.bounce_count = 0
        self.last_send_at: Optional[datetime] = None

        # Son kontrol sonuçları
        self._last_results: list[HealthStatus] = []
        self._last_check_at: Optional[datetime] = None
        self._issues_history: list[dict] = []  # Son 100 sorun

        log.info("Watchdog v2.0 GERÇEK gözetleme + alarm sistemi aktif.")

    # ─── THREAD KONTROLÜ ─────────────────────────────────────────

    def start(self):
        """Watchdog'u arka plan thread olarak başlatır."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="WatchdogV2Thread"
        )
        self._thread.start()
        log.info("Watchdog v2.0 thread başlatıldı — 5 dk aralıklarla kontrol.")

    def stop(self):
        self._running = False
        log.info("Watchdog v2.0 durduruldu.")

    def _loop(self):
        """Ana watchdog döngüsü — her 5 dakikada tam sistem kontrolü."""
        time.sleep(30)  # İlk 30 sn bekle (startup tamamlansın)
        while self._running:
            try:
                results = self.run_checks()

                # Günlük sabah raporu kontrolü
                self._check_daily_report()

            except Exception as e:
                log.error(f"Watchdog iç hatası: {e}\n{traceback.format_exc()}")
            time.sleep(self.CHECK_INTERVAL_SEC)

    # ─── ANA KONTROL METODU ──────────────────────────────────────

    def run_checks(self) -> list[HealthStatus]:
        """Tam sistem sağlık kontrolü — gerçek testlerle."""
        self._check_count += 1
        results = []
        critical_issues = []
        warning_issues = []

        # 1. Pipeline heartbeat kontrolü
        hb = self._check_pipeline_heartbeat()
        results.append(hb)
        if hb.is_critical():
            critical_issues.append(hb)

        # 2. DB bağlantı kontrolü (gerçek query)
        db_check = self._check_database()
        results.append(db_check)
        if db_check.is_critical():
            critical_issues.append(db_check)

        # 3. Lead keşfi kontrolü (son 24 saatte lead bulundu mu?)
        lead_check = self._check_lead_discovery()
        results.append(lead_check)
        if lead_check.is_critical():
            critical_issues.append(lead_check)
        elif lead_check.is_warning():
            warning_issues.append(lead_check)

        # 4. Email gönderim kontrolü
        send_check = self._check_email_sending()
        results.append(send_check)
        if send_check.is_critical():
            critical_issues.append(send_check)
        elif send_check.is_warning():
            warning_issues.append(send_check)

        # 5. API anahtar kontrolü (config varlık)
        api_check = self._check_api_keys()
        results.append(api_check)
        if api_check.is_critical():
            critical_issues.append(api_check)

        # 6. Bounce rate kontrolü
        bounce_check = self._check_bounce_rate()
        results.append(bounce_check)
        if bounce_check.is_critical():
            critical_issues.append(bounce_check)

        # 7. Disk alanı kontrolü
        disk_check = self._check_disk_space()
        results.append(disk_check)
        if disk_check.is_warning():
            warning_issues.append(disk_check)

        # Sonuçları kaydet
        self._last_results = results
        self._last_check_at = datetime.now()

        # ─── KRİTİK SORUN ALARM! ─────────────────────────────────
        if critical_issues:
            self._critical_count += 1
            issues_text = "\n".join(
                f"🔴 {hs.name}: {hs.detail}" for hs in critical_issues
            )
            log.error(
                f"[WATCHDOG] 🔴 {len(critical_issues)} KRİTİK SORUN!\n{issues_text}"
            )
            # Alarm gönder!
            self._send_alarm(
                f"🚨 SmartMailer KRİTİK ALARM",
                f"{len(critical_issues)} kritik sorun tespit edildi:\n\n{issues_text}",
                alarm_type="critical"
            )
            # Issue history'e ekle
            for hs in critical_issues:
                self._add_issue(hs.name, hs.status, hs.detail)

        elif warning_issues:
            warn_text = "\n".join(
                f"⚠️ {hs.name}: {hs.detail}" for hs in warning_issues
            )
            log.warning(f"[WATCHDOG] ⚠️ Uyarılar:\n{warn_text}")
            for hs in warning_issues:
                self._add_issue(hs.name, hs.status, hs.detail)
        else:
            log.debug(f"[WATCHDOG] ✅ Sistem sağlıklı | Kontrol #{self._check_count}")

        return results

    # ─── TEKİL KONTROLLER (GERÇEK!) ──────────────────────────────

    def _check_pipeline_heartbeat(self) -> HealthStatus:
        """Pipeline son 30 dk'da çalıştı mı? heartbeat.txt kontrol."""
        try:
            hb_path = os.path.join(self._config.DATA_DIR, "heartbeat.txt")
            if not os.path.exists(hb_path):
                return HealthStatus("pipeline_heartbeat", "CRITICAL",
                                    "Pipeline heartbeat dosyası YOK — pipeline hiç çalışmamış!")

            with open(hb_path, "r") as f:
                hb_time = f.read().strip()

            if not hb_time:
                return HealthStatus("pipeline_heartbeat", "CRITICAL",
                                    "Heartbeat dosyası boş!")

            hb_dt = datetime.fromisoformat(hb_time)
            age_sec = (datetime.now() - hb_dt).total_seconds()
            max_age = self._config.HEARTBEAT_MAX_AGE  # 1800 sn = 30 dk

            if age_sec > max_age * 2:
                return HealthStatus("pipeline_heartbeat", "CRITICAL",
                    f"Pipeline {int(age_sec//3600)} saat {int((age_sec%3600)//60)} dk'dır DURMUŞ! "
                    f"Son heartbeat: {hb_time}")
            elif age_sec > max_age:
                return HealthStatus("pipeline_heartbeat", "WARNING",
                    f"Pipeline {int(age_sec//60)} dk'dır sessiz — son: {hb_time}")

            return HealthStatus("pipeline_heartbeat", "OK",
                                f"Son heartbeat: {int(age_sec//60)} dk önce")
        except Exception as e:
            return HealthStatus("pipeline_heartbeat", "CRITICAL",
                                f"Heartbeat kontrol hatası: {e}")

    def _check_database(self) -> HealthStatus:
        """DB'ye gerçek bir query gönder."""
        try:
            from core.database import db
            # Gerçek query
            stats = db.get_stats()
            total = stats.get("total_leads", 0)
            sent = stats.get("total_sent", 0)
            return HealthStatus("database", "OK",
                                f"DB aktif — {total} lead, {sent} gönderilmiş")
        except Exception as e:
            return HealthStatus("database", "CRITICAL",
                                f"DB BAĞLANTI HATASI: {e}")

    def _check_lead_discovery(self) -> HealthStatus:
        """Son 24 saatte yeni lead keşfedildi mi?"""
        try:
            from core.database import db
            # Son 24 saatteki yeni lead sayısı
            with db._conn() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) FROM leads WHERE datetime(created_at) > datetime('now', '-1 day')"
                ).fetchone()
                recent_leads = row[0] if row else 0

            if recent_leads == 0:
                # İş saatleri dışındaysa (gece) warning, iş saatlerindeyse critical
                hour = datetime.now().hour
                if 8 <= hour < 19:
                    return HealthStatus("lead_discovery", "WARNING",
                        "Son 24 saatte hiç yeni lead keşfedilmedi — pipeline çalışmıyor olabilir")
                return HealthStatus("lead_discovery", "OK",
                    "Gece saatleri — lead keşfi bekleniyor")
            return HealthStatus("lead_discovery", "OK",
                                f"Son 24 saatte {recent_leads} yeni lead keşfedildi")
        except Exception as e:
            return HealthStatus("lead_discovery", "WARNING",
                                f"Lead kontrolü hatası: {e}")

    def _check_email_sending(self) -> HealthStatus:
        """Bugün email gönderildi mi? (saat izinliyse kontrol et)"""
        try:
            from core.database import db
            today_sent = db.get_today_sent_count()
            hour = datetime.now().hour
            wday = datetime.now().weekday()

            # Gönderim saatleri dışındaysa OK
            if wday == 6 and (hour < 12 or hour >= 19):
                return HealthStatus("email_sending", "OK",
                    f"Pazar gece — gönderim penceresi dışı | Bugün: {today_sent}")
            elif hour < 8 or hour >= 19:
                return HealthStatus("email_sending", "OK",
                    f"Gece saatleri — gönderim penceresi dışı | Bugün: {today_sent}")

            # İş saatlerinde ama hiç gönderilmemişse
            if today_sent == 0 and hour >= 10:
                return HealthStatus("email_sending", "WARNING",
                    f"Saat {hour}:00 oldu ama bugün hiç email gönderilmedi!")

            return HealthStatus("email_sending", "OK",
                                f"Bugün {today_sent} email gönderildi")
        except Exception as e:
            return HealthStatus("email_sending", "WARNING",
                                f"Email gönderim kontrolü hatası: {e}")

    def _check_api_keys(self) -> HealthStatus:
        """API anahtarları var mı?"""
        issues = []
        if not self._config.GEMINI_API_KEY and not self._config.ANTHROPIC_API_KEY:
            issues.append("Hiçbir AI API anahtarı yok!")
        if not self._config.BREVO_API_KEY and not self._config.BREVO_SMTP_PASS:
            issues.append("Brevo kimlik bilgisi yok!")
        if not self._config.SENDER_EMAIL:
            issues.append("SENDER_EMAIL eksik!")

        if issues:
            return HealthStatus("api_keys", "CRITICAL", " | ".join(issues))
        return HealthStatus("api_keys", "OK", "Tüm API anahtarları mevcut")

    def _check_bounce_rate(self) -> HealthStatus:
        """Bounce oranını DB'den kontrol et."""
        try:
            from core.database import db
            stats = db.get_event_stats()
            total_sent = stats.get("total_sent", 0)
            bounces = stats.get("bounces", 0) + stats.get("hard_bounces", 0)

            if total_sent == 0:
                return HealthStatus("bounce_rate", "OK", "Henüz gönderim yok")

            rate = bounces / max(total_sent, 1)
            if rate > 0.05:
                self._bounce_critical = True
                return HealthStatus("bounce_rate", "CRITICAL",
                    f"Bounce rate %{rate*100:.1f} — KRİTİK! ({bounces}/{total_sent})")
            elif rate > 0.02:
                return HealthStatus("bounce_rate", "WARNING",
                    f"Bounce rate %{rate*100:.1f} ({bounces}/{total_sent})")
            return HealthStatus("bounce_rate", "OK",
                                f"%{rate*100:.1f} ({bounces}/{total_sent})")
        except Exception:
            # Event stats yoksa basit kontrol
            if self.sent_count == 0:
                return HealthStatus("bounce_rate", "OK", "Henüz gönderim yok")
            rate = self.bounce_count / max(self.sent_count, 1)
            if rate > 0.05:
                return HealthStatus("bounce_rate", "CRITICAL",
                    f"Bounce rate %{rate*100:.1f}")
            return HealthStatus("bounce_rate", "OK",
                                f"%{rate*100:.1f}")

    def _check_disk_space(self) -> HealthStatus:
        """Disk alanı kontrolü."""
        try:
            import shutil
            db_path = os.path.join(self._config.DATA_DIR, "smartmailer_ultimate.db")
            if os.path.exists(db_path):
                db_size_mb = os.path.getsize(db_path) / (1024 * 1024)
                total, used, free = shutil.disk_usage(self._config.BASE_DIR)
                free_gb = free / (1024**3)

                if free_gb < 1:
                    return HealthStatus("disk_space", "WARNING",
                        f"Disk alanı az: {free_gb:.1f} GB kalan | DB: {db_size_mb:.1f} MB")
                return HealthStatus("disk_space", "OK",
                    f"Disk: {free_gb:.1f} GB boş | DB: {db_size_mb:.1f} MB")
        except Exception:
            pass
        return HealthStatus("disk_space", "OK", "Kontrol edilemedi")

    # ─── ALARM SİSTEMİ ──────────────────────────────────────────

    def _can_send_alarm(self, alarm_type: str) -> bool:
        """Alarm cooldown kontrolü — spam önleme."""
        last = self._last_alarm_at.get(alarm_type)
        if last and (datetime.now() - last) < self._alarm_cooldown:
            return False
        return True

    def _send_alarm(self, subject: str, message: str, alarm_type: str = "general"):
        """Email + WhatsApp alarm gönder."""
        if not self._can_send_alarm(alarm_type):
            log.debug(f"[WATCHDOG] Alarm cooldown — {alarm_type} atlandı")
            return

        self._last_alarm_at[alarm_type] = datetime.now()

        # 1. Email Alarm (Brevo API)
        self._send_email_alarm(subject, message)

        # 2. WhatsApp Alarm (OpenClaw API)
        self._send_whatsapp_alarm(f"{subject}\n\n{message}")

    def _send_email_alarm(self, subject: str, message: str):
        """Brevo API ile alarm emaili gönder."""
        try:
            import requests
            brevo_key = self._config.BREVO_API_KEY
            if not brevo_key:
                log.warning("[WATCHDOG] Brevo API key yok — email alarm gönderilemedi")
                return

            payload = {
                "sender": {
                    "name": "SmartMailer Watchdog",
                    "email": self._config.SENDER_EMAIL
                },
                "to": [{"email": self._config.ALARM_EMAIL}],
                "subject": subject,
                "htmlContent": f"""
                <div style="font-family:Arial;max-width:600px;margin:0 auto;background:#1a1a2e;color:#eee;padding:30px;border-radius:12px;">
                    <h2 style="color:#ff4757;margin-top:0;">🚨 SmartMailer Watchdog Alarm</h2>
                    <div style="background:#16213e;padding:20px;border-radius:8px;border-left:4px solid #ff4757;">
                        <pre style="white-space:pre-wrap;font-size:14px;color:#ddd;margin:0;">{message}</pre>
                    </div>
                    <p style="color:#888;font-size:12px;margin-top:20px;">
                        ⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}<br>
                        📊 Dashboard: <a href="https://app.fleettrackholland.nl/ops" style="color:#3498db;">Ops Dashboard</a>
                    </p>
                </div>
                """,
                "textContent": f"{subject}\n\n{message}\n\nZaman: {datetime.now().isoformat()}"
            }

            resp = requests.post(
                "https://api.brevo.com/v3/smtp/email",
                json=payload,
                headers={"api-key": brevo_key, "content-type": "application/json"},
                timeout=15
            )

            if resp.ok:
                log.info(f"[WATCHDOG] ✅ Email alarm gönderildi: {self._config.ALARM_EMAIL}")
            else:
                log.warning(f"[WATCHDOG] Email alarm hata: {resp.status_code} — {resp.text[:200]}")
        except Exception as e:
            log.error(f"[WATCHDOG] Email alarm gönderilemedi: {e}")

    def _send_whatsapp_alarm(self, message: str):
        """OpenClaw API üzerinden WhatsApp mesajı gönder."""
        try:
            import requests
            api_url = self._config.ALARM_WHATSAPP_API
            phone = self._config.ALARM_WHATSAPP

            if not api_url or not phone:
                log.debug("[WATCHDOG] WhatsApp alarm yapılandırılmamış — atlanıyor")
                return

            # OpenClaw send message endpoint
            # Format: phone@c.us
            clean_phone = phone.replace("+", "").replace(" ", "").replace("-", "")
            payload = {
                "chatId": f"{clean_phone}@c.us",
                "message": message,
            }

            resp = requests.post(
                f"{api_url}/api/sendText",
                json=payload,
                timeout=15,
            )

            if resp.ok:
                log.info(f"[WATCHDOG] ✅ WhatsApp alarm gönderildi: {phone}")
            else:
                log.debug(f"[WATCHDOG] WhatsApp alarm hata: {resp.status_code}")
        except Exception as e:
            log.debug(f"[WATCHDOG] WhatsApp alarm gönderilemedi: {e}")

    # ─── GÜNLÜK SABAH RAPORU ─────────────────────────────────────

    def _check_daily_report(self):
        """Her sabah 08:00'da günlük özet rapor gönder."""
        now = datetime.now()
        if now.hour != self._config.DAILY_REPORT_HOUR:
            self.__class__.DAILY_REPORT_DONE = False
            return

        if self.__class__.DAILY_REPORT_DONE:
            return

        self.__class__.DAILY_REPORT_DONE = True
        self._send_daily_report()

    def _send_daily_report(self):
        """Günlük sabah raporu oluştur ve gönder."""
        try:
            from core.database import db

            stats = db.get_stats()
            today_sent = db.get_today_sent_count()
            all_leads = db.get_all_leads()

            # Son 24 saatteki yeni leadler
            new_leads_24h = 0
            try:
                with db._conn() as conn:
                    row = conn.execute(
                        "SELECT COUNT(*) FROM leads WHERE datetime(created_at) > datetime('now', '-1 day')"
                    ).fetchone()
                    new_leads_24h = row[0] if row else 0
            except Exception:
                pass

            # Son kontrol sonuçları
            check_summary = ""
            if self._last_results:
                for hs in self._last_results:
                    icon = "✅" if hs.is_ok() else ("⚠️" if hs.is_warning() else "🔴")
                    check_summary += f"{icon} {hs.name}: {hs.detail}\n"

            # Pipeline durumu
            pipeline_status = "❓ Bilinmiyor"
            try:
                hb_path = os.path.join(self._config.DATA_DIR, "heartbeat.txt")
                if os.path.exists(hb_path):
                    with open(hb_path, "r") as f:
                        hb_time = f.read().strip()
                    hb_dt = datetime.fromisoformat(hb_time)
                    age = (datetime.now() - hb_dt).total_seconds()
                    if age < 1800:
                        pipeline_status = f"✅ Aktif (son: {int(age//60)} dk önce)"
                    else:
                        pipeline_status = f"🔴 DURMUŞ! (son: {int(age//3600)} saat önce)"
            except Exception:
                pass

            report = f"""📊 SmartMailer Günlük Rapor — {datetime.now().strftime('%d/%m/%Y')}

🔧 Pipeline: {pipeline_status}
📋 Toplam Lead: {stats.get('total_leads', 0)}
🆕 Son 24h Yeni: {new_leads_24h}
📧 Toplam Gönderim: {stats.get('total_sent', 0)}
📧 Bugün Gönderilen: {today_sent}
🔥 Hot Leads: {stats.get('hot_leads', 0)}

━━━━━━━━━━━━━━━━━━━━━━━━━
Sağlık Kontrolü:
{check_summary if check_summary else 'Henüz kontrol yapılmadı'}
━━━━━━━━━━━━━━━━━━━━━━━━━
Watchdog: {self._check_count} kontrol | {self._critical_count} kritik olay
"""

            self._send_alarm(
                f"📊 SmartMailer Günlük Rapor — {datetime.now().strftime('%d/%m')}",
                report,
                alarm_type="daily_report"
            )
            log.info("[WATCHDOG] ✅ Günlük sabah raporu gönderildi")

        except Exception as e:
            log.error(f"[WATCHDOG] Günlük rapor hatası: {e}")

    # ─── ISSUE HISTORY ───────────────────────────────────────────

    def _add_issue(self, name: str, status: str, detail: str):
        """Sorun geçmişine ekle (son 100)."""
        self._issues_history.append({
            "name": name,
            "status": status,
            "detail": detail,
            "time": datetime.now().isoformat(),
        })
        if len(self._issues_history) > 100:
            self._issues_history = self._issues_history[-100:]

    # ─── METRİK GÜNCELLEME ───────────────────────────────────────

    def record_send(self, success: bool, bounced: bool = False):
        if success:
            self.sent_count += 1
            self.last_send_at = datetime.now()
        else:
            self.failed_count += 1
        if bounced:
            self.bounce_count += 1

    @property
    def should_stop_campaign(self) -> bool:
        return getattr(self, "_bounce_critical", False)

    # ─── DETAYLI SAĞLIK RAPORU ───────────────────────────────────

    def get_health_report(self) -> dict:
        """Tam sağlık raporu — ops dashboard için."""
        results = self._last_results if self._last_results else self.run_checks()

        checks = []
        for hs in results:
            checks.append({
                "name": hs.name,
                "status": hs.status,
                "detail": hs.detail,
                "checked_at": hs.checked_at.isoformat(),
            })

        overall = "OK"
        if any(hs.is_critical() for hs in results):
            overall = "CRITICAL"
        elif any(hs.is_warning() for hs in results):
            overall = "WARNING"

        return {
            "overall": overall,
            "checks": checks,
            "last_check_at": self._last_check_at.isoformat() if self._last_check_at else None,
            "total_checks": self._check_count,
            "critical_incidents": self._critical_count,
            "recoveries": self._recovery_count,
            "uptime_hours": round((datetime.now() - self._start_time).total_seconds() / 3600, 1),
            "recent_issues": self._issues_history[-20:],
        }

    def get_summary(self) -> dict:
        """Eski uyumlu özet (orchestrator için)."""
        uptime = datetime.now() - self._start_time
        return {
            "uptime_minutes": uptime.seconds // 60,
            "checks_run": self._check_count,
            "auto_recoveries": self._recovery_count,
            "critical_incidents": self._critical_count,
            "sent": self.sent_count,
            "failed": self.failed_count,
            "bounce": self.bounce_count,
        }

    def ping(self) -> bool:
        return True
