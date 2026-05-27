from fastapi import Security, HTTPException, status, Depends
from fastapi.security.api_key import APIKeyHeader
from sqlmodel import Session, select
try:
    from app.database import APIKey, get_session
except ImportError:
    from database import APIKey, get_session

API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)

def get_api_key(
    api_key_header: str = Security(API_KEY_HEADER),
    session: Session = Depends(get_session)
) -> APIKey:
    if not api_key_header:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "code": "MISSING_API_KEY",
                    "message": "Authentication required. Please provide your API key in the 'X-API-Key' header.",
                    "suggested_action": "Add the 'X-API-Key' header to your request containing a valid API key.",
                    "documentation_url": "https://api.transcribe-agent.example.com/docs#authentication"
                }
            }
        )

    statement = select(APIKey).where(APIKey.key == api_key_header)
    api_key_obj = session.exec(statement).first()

    if not api_key_obj:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "code": "INVALID_API_KEY",
                    "message": "The provided API key is invalid or has been revoked.",
                    "suggested_action": "Check for typos or generate a new API key via the key management portal.",
                    "documentation_url": "https://api.transcribe-agent.example.com/docs#authentication"
                }
            }
        )

    if not api_key_obj.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "code": "REVOKED_API_KEY",
                    "message": "This API key has been deactivated.",
                    "suggested_action": "Contact support or use an active API key.",
                    "documentation_url": "https://api.transcribe-agent.example.com/docs#authentication"
                }
            }
        )

    if api_key_obj.quota_used_usd >= api_key_obj.quota_limit_usd:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "error": {
                    "code": "QUOTA_EXCEEDED",
                    "message": f"Billing quota limit of ${api_key_obj.quota_limit_usd:.2f} exceeded.",
                    "suggested_action": "Increase your billing plan, add funds, or wait for the quota reset period.",
                    "documentation_url": "https://api.transcribe-agent.example.com/docs#billing-quotas"
                }
            }
        )

    return api_key_obj
