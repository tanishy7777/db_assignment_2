import hashlib
from fastapi import Depends, HTTPException, Cookie
from app.auth.jwt_handler import decode_token
from app.database import get_auth_db


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def get_current_user(
    access_token: str = Cookie(default=None),
    db=Depends(get_auth_db),
) -> dict:
    if not access_token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = decode_token(access_token)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    db.execute(
        "SELECT is_revoked FROM sessions WHERE token_hash = %s",
        (_hash_token(access_token),),
    )
    session = db.fetchone()
    if not session or session["is_revoked"]:
        raise HTTPException(status_code=401, detail="Session expired or revoked")
    return payload


def require_role(*allowed_roles: str):
    """
    Factory that returns a FastAPI dependency enforcing role membership.
    Usage: Depends(require_role("Admin"))
           Depends(require_role("Admin", "Coach"))
    """
    async def _checker(current_user: dict = Depends(get_current_user)) -> dict:
        if current_user["role"] not in allowed_roles:
            raise HTTPException(
                status_code=403,
                detail=f"Access denied. Required role(s): {list(allowed_roles)}",
            )
        return current_user
    return _checker
require_admin           = require_role("Admin")

require_admin_or_coach  = require_role("Admin", "Coach")
