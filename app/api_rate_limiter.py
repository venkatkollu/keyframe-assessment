import time
from fastapi import Depends, HTTPException, status
try:
    from app.database import APIKey
    from app.auth import get_api_key
except ImportError:
    from database import APIKey
    from auth import get_api_key

# Global registry of token buckets: api_key_id -> {"tokens": float, "last_refill": float}
_api_key_buckets = {}

def rate_limit(api_key: APIKey = Depends(get_api_key)):
    now = time.monotonic()
    limit = api_key.rate_limit_rpm
    if limit <= 0:
        return  # No limit set

    bucket = _api_key_buckets.setdefault(api_key.id, {"tokens": float(limit), "last_refill": now})

    # Refill tokens: tokens += elapsed * (limit / 60.0)
    elapsed = now - bucket["last_refill"]
    refilled_tokens = bucket["tokens"] + elapsed * (limit / 60.0)
    bucket["tokens"] = min(float(limit), refilled_tokens)
    bucket["last_refill"] = now

    if bucket["tokens"] < 1.0:
        # Calculate seconds to wait until we have at least 1.0 token
        tokens_needed = 1.0 - bucket["tokens"]
        refill_rate_per_sec = limit / 60.0
        seconds_to_wait = tokens_needed / refill_rate_per_sec

        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            headers={"Retry-After": str(max(1, int(seconds_to_wait)))},
            detail={
                "error": {
                    "code": "RATE_LIMIT_EXCEEDED",
                    "message": f"Rate limit of {limit} requests per minute exceeded.",
                    "suggested_action": f"Slow down your requests. You can retry in approximately {int(seconds_to_wait)} seconds.",
                    "retry_after_seconds": int(seconds_to_wait),
                    "documentation_url": "https://api.transcribe-agent.example.com/docs#rate-limiting"
                }
            }
        )

    bucket["tokens"] -= 1.0
