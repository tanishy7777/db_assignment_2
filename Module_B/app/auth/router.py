import hashlib
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from app.auth.dependencies import get_current_user, _hash_token
from app.auth.jwt_handler import create_token
from app.config import JWT_EXPIRY_HOURS
from app.database import get_auth_db
from app.services.audit import write_audit_log

router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/login")
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db=Depends(get_auth_db),
):
    ip = request.client.host if request.client else "unknown"

    # Look up user
    db.execute(
        "SELECT user_id, username, password_hash, role, member_id, is_active "
        "FROM users WHERE username = %s",
        (body.username,),
    )
    user = db.fetchone()

    if not user or not user["is_active"]:
        write_audit_log(
            db, None, body.username, "LOGIN", "users", None,
            "FAILURE", {"reason": "user not found or inactive"}, ip,
        )
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not bcrypt.checkpw(body.password.encode(), user["password_hash"].encode()):
        write_audit_log(
            db, user["user_id"], body.username, "LOGIN", "users",
            str(user["user_id"]), "FAILURE", {"reason": "wrong password"}, ip,
        )
        raise HTTPException(status_code=401, detail="Invalid credentials")

    # Create JWT
    token = create_token(
        user["user_id"], user["username"], user["role"], user["member_id"]
    )

    # Store session
    expires_at = datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRY_HOURS)
    db.execute(
        "INSERT INTO sessions (user_id, token_hash, expires_at) VALUES (%s, %s, %s)",
        (user["user_id"], _hash_token(token), expires_at),
    )

    write_audit_log(
        db, user["user_id"], user["username"], "LOGIN", "sessions",
        str(user["user_id"]), "SUCCESS", None, ip,
    )

    # Set HTTP-only cookie
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        max_age=JWT_EXPIRY_HOURS * 3600,
        samesite="lax",
    )
    return {
        "success": True,
        "message": "Login successful",
        "data": {
            "user_id":   user["user_id"],
            "username":  user["username"],
            "role":      user["role"],
            "member_id": user["member_id"],
        },
    }


@router.get("/logout")
def logout(
    request: Request,
    response: Response,
    current_user: dict = Depends(get_current_user),
    db=Depends(get_auth_db),
):
    ip = request.client.host if request.client else "unknown"
    token = request.cookies.get("access_token", "")

    db.execute(
        "UPDATE sessions SET is_revoked = TRUE WHERE token_hash = %s",
        (_hash_token(token),),
    )

    write_audit_log(
        db, current_user["user_id"], current_user["username"],
        "LOGOUT", "sessions", str(current_user["user_id"]), "SUCCESS", None, ip,
    )

    response.delete_cookie("access_token")
    return {"success": True, "message": "Logged out"}


@router.get("/isAuth")
def is_auth(current_user: dict = Depends(get_current_user)):
    return {
        "success":    True,
        "data": {
            "user_id":   current_user["user_id"],
            "username":  current_user["username"],
            "role":      current_user["role"],
            "member_id": current_user["member_id"],
        },
    }
