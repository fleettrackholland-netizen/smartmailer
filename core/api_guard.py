"""
core/api_guard.py — Self-Healing API Guard (Gemini + Claude) with AUTO-FALLBACK
Tüm AI API çağrılarını merkezi olarak korur.
- Otomatik Gemini ↔ Claude payload dönüşümü
- ★ Gemini 429 → otomatik Claude fallback
- Rate limiter + exponential backoff + circuit breaker
- Thread-safe

Kullanım — agent'lar hiç değişmeden çalışır:
    from core.api_guard import api_guard
    response = api_guard.call(payload, headers)
"""
import time
import json
import threading
import requests
from core.logger import get_logger

log = get_logger("api_guard")

CLAUDE_API_URL = "https://api.anthropic.com/v1/messages"
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class APIGuard:
    """Self-healing, rate-limit-aware API caller — Gemini & Claude with auto-fallback."""

    def __init__(
        self,
        max_requests_per_minute: int = 14,
        max_retries: int = 3,
        base_backoff_seconds: float = 5.0,
        circuit_breaker_threshold: int = 8,
        circuit_breaker_cooldown: int = 90,
    ):
        self._lock = threading.Lock()
        self._max_rpm = max_requests_per_minute
        self._request_timestamps: list[float] = []

        self._max_retries = max_retries
        self._base_backoff = base_backoff_seconds

        self._consecutive_failures = 0
        self._cb_threshold = circuit_breaker_threshold
        self._cb_cooldown = circuit_breaker_cooldown
        self._circuit_open_until: float = 0

        self._total_calls = 0
        self._total_retries = 0
        self._total_429s = 0
        self._total_successes = 0
        self._total_fallbacks = 0

        # Provider detection
        from config import config as cfg
        self._provider = cfg.AI_PROVIDER  # "gemini" or "claude"
        self._gemini_key = cfg.GEMINI_API_KEY
        self._gemini_model = cfg.GEMINI_MODEL
        self._anthropic_key = cfg.ANTHROPIC_API_KEY
        self._claude_model = cfg.CLAUDE_MODEL

        # ★ COST-OPTIMIZED FALLBACK: Gemini (ücretsiz) öncelikli, Claude sadece gerektiğinde
        self._gemini_quota_exhausted = False
        self._gemini_quota_retry_at: float = 0  # Bu zamandan sonra Gemini'yi tekrar dene
        self._GEMINI_RETRY_INTERVAL = 4 * 3600  # 4 saat — Gemini kota yenilenme süresi
        self._gemini_calls = 0   # Gemini'de başarılı çağrı sayısı
        self._claude_calls = 0   # Claude'da başarılı çağrı sayısı (maliyet takibi)

        provider_name = "Gemini 2.0 Flash" if self._provider == "gemini" else "Claude"
        has_fallback = bool(self._anthropic_key) if self._provider == "gemini" else bool(self._gemini_key)
        log.info(
            f"[API Guard] 💰 COST-OPTIMIZED: {provider_name} (birincil) — "
            f"Claude fallback={'AKTIF' if has_fallback else 'YOK'} — "
            f"Gemini retry: {self._GEMINI_RETRY_INTERVAL // 3600} saat"
        )

    # ─── RATE LIMITER ────────────────────────────────────────────

    def _wait_for_rate_limit(self):
        with self._lock:
            now = time.time()
            one_minute_ago = now - 60
            self._request_timestamps = [
                ts for ts in self._request_timestamps if ts > one_minute_ago
            ]
            if len(self._request_timestamps) >= self._max_rpm:
                oldest = self._request_timestamps[0]
                wait = (oldest + 60) - now + 0.5
                if wait > 0:
                    log.info(f"[API Guard] Rate limit — {wait:.1f}sn bekleniyor")
                    time.sleep(wait)
            self._request_timestamps.append(time.time())

    # ─── CIRCUIT BREAKER ─────────────────────────────────────────

    def _check_circuit_breaker(self) -> bool:
        if self._circuit_open_until > 0:
            if time.time() < self._circuit_open_until:
                remaining = int(self._circuit_open_until - time.time())
                log.warning(f"[API Guard] Circuit breaker AÇIK — {remaining}sn kaldı")
                return False
            else:
                log.info("[API Guard] Circuit breaker kapandı — tekrar deneniyor")
                self._circuit_open_until = 0
                self._consecutive_failures = 0
        return True

    def _record_success(self):
        self._consecutive_failures = 0
        self._total_successes += 1

    def _record_failure(self):
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._cb_threshold:
            self._circuit_open_until = time.time() + self._cb_cooldown
            log.error(
                f"[API Guard] ⚠️ Circuit breaker AÇILDI — {self._consecutive_failures} art arda hata!"
            )

    # ─── CLAUDE → GEMINI PAYLOAD DÖNÜŞÜMÜ ────────────────────────

    def _claude_to_gemini(self, payload: dict) -> tuple[str, dict]:
        """
        Claude formatındaki payload'ı Gemini formatına çevirir.
        Returns: (url, gemini_payload)
        """
        messages = payload.get("messages", [])
        max_tokens = payload.get("max_tokens", 4096)

        # Claude messages → Gemini contents
        contents = []
        for msg in messages:
            role = "user" if msg.get("role") == "user" else "model"
            text = msg.get("content", "")
            # Handle content that's a list (Claude format)
            if isinstance(text, list):
                text = " ".join(
                    part.get("text", "") for part in text if isinstance(part, dict)
                )
            contents.append({
                "role": role,
                "parts": [{"text": str(text)}]
            })

        gemini_payload = {
            "contents": contents,
            "generationConfig": {
                "maxOutputTokens": max_tokens,
                "temperature": 0.7,
            }
        }

        # System instruction (from Claude system field)
        system = payload.get("system")
        if system:
            gemini_payload["systemInstruction"] = {
                "parts": [{"text": system}]
            }

        model = self._gemini_model
        url = GEMINI_API_URL.format(model=model) + f"?key={self._gemini_key}"
        return url, gemini_payload

    def _gemini_to_claude_response(self, gemini_resp: requests.Response) -> requests.Response:
        """
        Gemini'nin response'ını Claude response formatına çevirir.
        Agent'lar fark etmeden çalışmaya devam eder.
        """
        try:
            data = gemini_resp.json()
            # Extract text from Gemini response
            text = ""
            candidates = data.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                text = " ".join(p.get("text", "") for p in parts)

            # Build Claude-compatible response
            claude_format = {
                "content": [{"type": "text", "text": text}],
                "model": self._gemini_model,
                "role": "assistant",
                "stop_reason": "end_turn",
                "usage": data.get("usageMetadata", {})
            }

            # Create a fake Response object that looks like Claude's
            fake_resp = requests.models.Response()
            fake_resp.status_code = 200
            fake_resp._content = json.dumps(claude_format).encode("utf-8")
            fake_resp.headers["content-type"] = "application/json"
            fake_resp.encoding = "utf-8"
            return fake_resp

        except Exception as e:
            log.error(f"[API Guard] Gemini response parse hatası: {e}")
            return gemini_resp

    # ─── ★ CLAUDE FALLBACK ÇAĞRISI ───────────────────────────────

    def _call_claude_fallback(self, payload: dict, timeout: int) -> requests.Response | None:
        """Gemini başarısız olduğunda Claude'u backup olarak kullan."""
        if not self._anthropic_key:
            log.warning("[API Guard] Claude fallback istendi ama ANTHROPIC_API_KEY yok!")
            return None

        self._total_fallbacks += 1
        log.info(f"[API Guard] 🔄 FALLBACK → Claude (#{self._total_fallbacks})")

        # Payload zaten Claude formatında (agent'lar Claude formatında yazar)
        # Model'i güncelle
        fallback_payload = dict(payload)
        fallback_payload["model"] = self._claude_model

        headers = {
            "x-api-key": self._anthropic_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        try:
            resp = requests.post(
                CLAUDE_API_URL,
                json=fallback_payload,
                headers=headers,
                timeout=timeout,
            )
            if resp.ok:
                self._claude_calls += 1
                log.info(f"[API Guard] ✅ Claude fallback BAŞARILI (toplam Claude: {self._claude_calls})")
                self._record_success()
                return resp
            else:
                log.warning(f"[API Guard] Claude fallback hata: {resp.status_code} — {resp.text[:200]}")
                return resp
        except Exception as e:
            log.error(f"[API Guard] Claude fallback exception: {e}")
            return None

    # ─── ★ GEMINI FALLBACK ÇAĞRISI (Claude primary ise) ──────────

    def _call_gemini_fallback(self, payload: dict, timeout: int) -> requests.Response | None:
        """Claude başarısız olduğunda Gemini'yi backup olarak kullan."""
        if not self._gemini_key:
            return None

        self._total_fallbacks += 1
        log.info(f"[API Guard] 🔄 FALLBACK → Gemini (#{self._total_fallbacks})")

        url, gemini_payload = self._claude_to_gemini(payload)
        headers = {"Content-Type": "application/json"}

        try:
            resp = requests.post(url, json=gemini_payload, headers=headers, timeout=timeout)
            if resp.ok:
                log.info("[API Guard] ✅ Gemini fallback BAŞARILI")
                self._record_success()
                return self._gemini_to_claude_response(resp)
            else:
                log.warning(f"[API Guard] Gemini fallback hata: {resp.status_code}")
                return None
        except Exception as e:
            log.error(f"[API Guard] Gemini fallback exception: {e}")
            return None

    # ─── ANA API ÇAĞRISI ─────────────────────────────────────────

    def call(
        self,
        payload: dict,
        headers: dict,
        timeout: int = 60,
    ) -> requests.Response | None:
        """
        AI API'yi çağır — provider'a göre otomatik yönlendir.
        ★ Gemini 429/quota hatası → otomatik Claude fallback
        ★ Claude hatası → otomatik Gemini fallback
        """
        self._total_calls += 1

        if not self._check_circuit_breaker():
            # Circuit breaker açık bile olsa fallback'i dene
            return self._try_fallback(payload, timeout)

        self._wait_for_rate_limit()

        # ★ COST-OPTIMIZED: Gemini birincil, Claude sadece gerektiğinde
        use_gemini = (self._provider == "gemini")
        if use_gemini and self._gemini_quota_exhausted:
            if time.time() > self._gemini_quota_retry_at:
                # 4 saat doldu — Gemini'yi probe et (tek istek ile test)
                log.info("[API Guard] ⏰ 4 saat doldu — Gemini tekrar deneniyor (ücretsiz kota yenilendi mi?)")
                self._gemini_quota_exhausted = False
                # Devam et, aşağıda normal Gemini çağrısı yapılacak
                # Eğer yine 429 alırsa tekrar Claude'a geçecek
            else:
                remaining_sec = int(self._gemini_quota_retry_at - time.time())
                remaining_hr = remaining_sec // 3600
                remaining_min = (remaining_sec % 3600) // 60
                # Her 50 çağrıda bir log bas (spam olmasın)
                if self._total_calls % 50 == 1:
                    log.info(
                        f"[API Guard] 💰 Gemini kota dolmuş — Claude kullanılıyor "
                        f"({remaining_hr}sa {remaining_min}dk sonra Gemini tekrar denenecek) "
                        f"[Claude: {self._claude_calls} çağrı]"
                    )
                return self._call_claude_fallback(payload, timeout)

        # Provider'a göre URL ve payload belirle
        if use_gemini:
            url, actual_payload = self._claude_to_gemini(payload)
            actual_headers = {"Content-Type": "application/json"}
            is_gemini = True
        else:
            url = CLAUDE_API_URL
            actual_payload = payload
            actual_headers = headers
            is_gemini = False

        for attempt in range(self._max_retries + 1):
            try:
                resp = requests.post(
                    url,
                    json=actual_payload,
                    headers=actual_headers,
                    timeout=timeout,
                )

                if resp.status_code == 429:
                    self._total_429s += 1

                    # ★ Gemini quota dolmuşsa → Claude'a geç, 4 saat sonra tekrar dene
                    if is_gemini and "quota" in resp.text.lower():
                        log.warning(
                            f"[API Guard] ⚠️ Gemini QUOTA DOLDU — Claude'a geçiliyor "
                            f"(4 saat sonra Gemini tekrar denenecek)"
                        )
                        self._gemini_quota_exhausted = True
                        self._gemini_quota_retry_at = time.time() + self._GEMINI_RETRY_INTERVAL
                        return self._call_claude_fallback(payload, timeout)

                    wait_time = self._base_backoff * (2 ** attempt)
                    retry_after = resp.headers.get("retry-after")
                    if retry_after:
                        try:
                            wait_time = max(wait_time, float(retry_after))
                        except (ValueError, TypeError):
                            pass
                    wait_time = min(wait_time, 120)

                    if attempt < self._max_retries:
                        self._total_retries += 1
                        log.warning(
                            f"[API Guard] 429 Rate limit — {wait_time:.0f}sn bekleniyor "
                            f"(deneme {attempt + 1}/{self._max_retries + 1})"
                        )
                        time.sleep(wait_time)
                        continue
                    else:
                        log.error("[API Guard] 429 — tüm denemeler başarısız, fallback deneniyor")
                        self._record_failure()
                        return self._try_fallback(payload, timeout)

                if resp.status_code in (529, 503):
                    wait_time = 15 * (attempt + 1)
                    if attempt < self._max_retries:
                        self._total_retries += 1
                        log.warning(f"[API Guard] {resp.status_code} — {wait_time}sn bekleniyor")
                        time.sleep(wait_time)
                        continue
                    else:
                        self._record_failure()
                        return self._try_fallback(payload, timeout)

                if resp.ok:
                    self._record_success()
                    # Maliyet takibi
                    if is_gemini:
                        self._gemini_calls += 1
                        return self._gemini_to_claude_response(resp)
                    return resp

                # ★ Diğer hatalar (400, 401, 500 vb.) — fallback dene
                log.warning(f"[API Guard] API hata: {resp.status_code} — {resp.text[:200]}")
                self._record_failure()
                fallback_result = self._try_fallback(payload, timeout)
                if fallback_result and fallback_result.ok:
                    return fallback_result
                return resp

            except requests.exceptions.Timeout:
                log.warning(f"[API Guard] Timeout (deneme {attempt + 1})")
                if attempt < self._max_retries:
                    self._total_retries += 1
                    time.sleep(5 * (attempt + 1))
                    continue
                self._record_failure()
                return self._try_fallback(payload, timeout)

            except requests.exceptions.ConnectionError as e:
                log.warning(f"[API Guard] Bağlantı hatası: {e}")
                if attempt < self._max_retries:
                    self._total_retries += 1
                    time.sleep(10 * (attempt + 1))
                    continue
                self._record_failure()
                return self._try_fallback(payload, timeout)

            except Exception as e:
                log.error(f"[API Guard] Beklenmeyen hata: {e}")
                self._record_failure()
                return self._try_fallback(payload, timeout)

        return None

    def _try_fallback(self, payload: dict, timeout: int) -> requests.Response | None:
        """Primary provider hatası durumunda alternatif provider'ı dene."""
        if self._provider == "gemini":
            return self._call_claude_fallback(payload, timeout)
        else:
            return self._call_gemini_fallback(payload, timeout)

    # ─── İSTATİSTİKLER ───────────────────────────────────────────

    def get_stats(self) -> dict:
        active = "claude" if self._gemini_quota_exhausted else "gemini"
        retry_info = ""
        if self._gemini_quota_exhausted:
            remaining = int(self._gemini_quota_retry_at - time.time())
            if remaining > 0:
                retry_info = f"{remaining // 3600}sa {(remaining % 3600) // 60}dk"
        return {
            "provider": self._provider,
            "active_provider": active,
            "model": self._gemini_model if active == "gemini" else self._claude_model,
            "total_calls": self._total_calls,
            "total_successes": self._total_successes,
            "gemini_calls": self._gemini_calls,
            "claude_calls": self._claude_calls,
            "total_retries": self._total_retries,
            "total_429s": self._total_429s,
            "total_fallbacks": self._total_fallbacks,
            "consecutive_failures": self._consecutive_failures,
            "circuit_breaker_open": self._circuit_open_until > time.time(),
            "gemini_quota_exhausted": self._gemini_quota_exhausted,
            "gemini_retry_in": retry_info,
        }


# ─── GLOBAL SINGLETON ────────────────────────────────────────
api_guard = APIGuard()
