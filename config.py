"""Configuration, API client singletons, and shared helpers."""

from __future__ import annotations

import datetime
import functools
import json
import os
import pathlib
import sys
import threading
import time

from dotenv import load_dotenv

load_dotenv()

# Write GCP credentials file if provided via environment variable (useful for cloud deploys)
gcp_credentials_json = os.getenv("GOOGLE_CREDENTIALS_JSON") or os.getenv("GCP_SA_KEY")
if gcp_credentials_json:
    try:
        import tempfile
        temp_dir = pathlib.Path(tempfile.gettempdir())
        creds_file = temp_dir / "gcp_credentials.json"
        
        # Verify if it is valid JSON before writing
        creds_data = json.loads(gcp_credentials_json)
        creds_file.write_text(json.dumps(creds_data))
        
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(creds_file)
        print(f"Successfully loaded GCP credentials from env var to {creds_file}", flush=True)
    except Exception as e:
        print(f"Failed to load GCP credentials from env var: {e}", flush=True)

# ---------------------------------------------------------------------------
# Data / cache directories
# ---------------------------------------------------------------------------

CACHE_DIR = pathlib.Path(os.getenv("CACHE_DIR", "cache"))
TRANSCRIPTS = CACHE_DIR / "transcripts"

for _p in (CACHE_DIR, TRANSCRIPTS):
    _p.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Model constants (overridable via env)
# ---------------------------------------------------------------------------

GEMINI_TRANSCRIBE_MODEL_STRONG = os.getenv("GEMINI_TRANSCRIBE_MODEL_STRONG", "gemini-2.5-flash")
GEMINI_TRANSCRIBE_MODEL_PRO = os.getenv("GEMINI_TRANSCRIBE_MODEL_PRO", "gemini-2.5-pro")


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def log(*a):
    print(datetime.datetime.utcnow().strftime("%H:%M:%S"), *a,
          file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Usage tracker
# ---------------------------------------------------------------------------

_PRICING = {
    "gemini-2.5-flash":      {"input": 0.30, "output": 2.50},
    "gemini-2.5-pro":        {"input": 1.25, "output": 10.00},
    "gemini-3.1-flash-lite": {"input": 0.075, "output": 0.30},
}


class UsageTracker:
    def __init__(self):
        self._lock = threading.Lock()
        self.reset()

    def reset(self):
        with self._lock:
            self._calls = []
            self._by_provider = {}
            self._chirp3_minutes = 0.0
            self._chirp3_cost = 0.0

    def record(self, provider, model, purpose, input_tokens=0, output_tokens=0, latency_ms=0):
        pricing = _PRICING.get(model, {"input": 0, "output": 0})
        cost = input_tokens / 1e6 * pricing["input"] + output_tokens / 1e6 * pricing["output"]
        with self._lock:
            self._calls.append({
                "provider": provider, "model": model, "purpose": purpose,
                "input_tokens": input_tokens, "output_tokens": output_tokens,
                "cost_usd": round(cost, 6), "latency_ms": round(latency_ms, 1),
            })
            key = f"{provider}/{model}"
            agg = self._by_provider.setdefault(key, {
                "calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0,
            })
            agg["calls"] += 1
            agg["input_tokens"] += input_tokens
            agg["output_tokens"] += output_tokens
            agg["cost_usd"] += cost

    def record_gemini(self, purpose, response=None, model="gemini-2.5-flash", latency_ms=0):
        inp, out = 0, 0
        if response is not None:
            um = getattr(response, "usage_metadata", None)
            if um:
                inp = getattr(um, "prompt_token_count", 0) or 0
                out = getattr(um, "candidates_token_count", 0) or 0
        self.record("google", model, purpose, inp, out, latency_ms)

    def record_chirp3(self, audio_seconds: float):
        """Track Chirp 3 sync usage at $0.016/min."""
        minutes = audio_seconds / 60.0
        cost = minutes * 0.016
        with self._lock:
            self._chirp3_minutes += minutes
            self._chirp3_cost += cost

    def summary(self):
        with self._lock:
            total_cost = sum(a["cost_usd"] for a in self._by_provider.values()) + self._chirp3_cost
            total_calls = sum(a["calls"] for a in self._by_provider.values())
            result = {
                "total_calls": total_calls,
                "total_cost_usd": round(total_cost, 4),
                "by_provider": {
                    k: {**v, "cost_usd": round(v["cost_usd"], 4)}
                    for k, v in self._by_provider.items()
                },
            }
            if self._chirp3_minutes > 0:
                result["chirp3"] = {
                    "minutes": round(self._chirp3_minutes, 1),
                    "cost_usd": round(self._chirp3_cost, 4),
                }
            return result

    def print_summary(self):
        s = self.summary()
        log(f"\n{'=' * 50}")
        log(f"USAGE: {s['total_calls']} API calls, ${s['total_cost_usd']:.4f} total")
        for key, agg in s["by_provider"].items():
            log(f"  {key}: {agg['calls']} calls, "
                f"{agg['input_tokens']:,} in / {agg['output_tokens']:,} out, "
                f"${agg['cost_usd']:.4f}")
        if "chirp3" in s:
            c3 = s["chirp3"]
            log(f"  chirp3-sync: {c3['minutes']:.1f} min, ${c3['cost_usd']:.4f}")
        log(f"{'=' * 50}")


usage_tracker = UsageTracker()


# ---------------------------------------------------------------------------
# Retry decorator
# ---------------------------------------------------------------------------

def retry_with_backoff(
    max_retries: int = 5,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    retryable: tuple[type[Exception], ...] | None = None,
):
    """Retry on transient API errors with exponential backoff."""
    _RETRYABLE_STATUS_CODES = ("429", "500", "503")

    def _is_retryable(exc: Exception) -> bool:
        if retryable and isinstance(exc, retryable):
            return True
        msg = str(exc).lower()
        if any(code in msg for code in _RETRYABLE_STATUS_CODES):
            return True
        if "rate" in msg or "overloaded" in msg or "timeout" in msg:
            return True
        return False

    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            last_exc: Exception | None = None
            for attempt in range(max_retries):
                try:
                    return fn(*args, **kwargs)
                except Exception as exc:
                    last_exc = exc
                    if not _is_retryable(exc) or attempt == max_retries - 1:
                        raise
                    delay = min(base_delay * (2 ** attempt), max_delay)
                    log(f"  [retry] {fn.__name__} attempt {attempt + 1}: "
                        f"{exc} — sleeping {delay:.1f}s")
                    time.sleep(delay)
            raise last_exc  # type: ignore[misc]
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# Lazy API client singletons
# ---------------------------------------------------------------------------

GCP_PROJECT_ID = os.getenv("GCP_PROJECT_ID", "")
GCP_LOCATION = os.getenv("GCP_LOCATION", "us-central1")

_gemini = None


def get_gemini():
    """Get Gemini client using Vertex AI with Application Default Credentials.

    Uses the same GOOGLE_APPLICATION_CREDENTIALS as Chirp 3.
    """
    global _gemini
    if _gemini is None:
        from google import genai as google_genai
        if not GCP_PROJECT_ID:
            raise RuntimeError("GCP_PROJECT_ID not set")
        _gemini = google_genai.Client(
            vertexai=True,
            project=GCP_PROJECT_ID,
            location=GCP_LOCATION,
        )
    return _gemini


# ---------------------------------------------------------------------------
# Media part helper (Vertex AI uses inline bytes, not Files API)
# ---------------------------------------------------------------------------

def prepare_media_part(media: pathlib.Path) -> object:
    """Read a local video/audio file and return a Gemini Part with inline bytes."""
    import mimetypes
    from google import genai

    mime, _ = mimetypes.guess_type(str(media))
    if not mime:
        mime = "video/mp4"
    data = media.read_bytes()
    return genai.types.Part.from_bytes(data=data, mime_type=mime)
