from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from typing import Optional

from app.auth.dependencies import get_current_user, require_admin, require_admin_or_coach
from app.database import get_auth_db, get_track_db
from app.services.audit import write_audit_log

router = APIRouter()


# ── Pydantic models ────────────────────────────────────────────────────────────

class MemberCreate(BaseModel):
    member_id:      int
    name:           str
    age:            int
    email:          str
    contact_number: str
    gender:         str          # 'M' | 'F' | 'O'
    role:           str          # 'Player' | 'Coach' | 'Admin'
    join_date:      str          # YYYY-MM-DD
    image:          Optional[str] = None
    # Auth account
    username:       str
    password:       str


class MemberUpdate(BaseModel):
    name:           Optional[str] = None
    age:            Optional[int] = None
    email:          Optional[str] = None
    contact_number: Optional[str] = None
    image:          Optional[str] = None


# ── Helpers ────────────────────────────────────────────────────────────────────

def _get_member_or_404(track_db, member_id: int) -> dict:
    track_db.execute("SELECT * FROM Member WHERE MemberID = %s", (member_id,))
    row = track_db.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Member not found")
    return row


def _can_view_member(current_user: dict, member_id: int) -> bool:
    """Players may only view their own profile."""
    if current_user["role"] in ("Admin", "Coach"):
        return True
    return current_user["member_id"] == member_id


# ── GET /api/members ───────────────────────────────────────────────────────────

@router.get("")
def list_members(
    request: Request,
    current_user: dict = Depends(get_current_user),
    track_db=Depends(get_track_db),
    auth_db=Depends(get_auth_db),
):
    ip = request.client.host if request.client else "unknown"

    if current_user["role"] == "Player":
        track_db.execute(
            "SELECT * FROM Member WHERE MemberID = %s",
            (current_user["member_id"],),
        )
    else:
        track_db.execute("SELECT * FROM Member ORDER BY MemberID")

    rows = track_db.fetchall()

    write_audit_log(
        auth_db, current_user["user_id"], current_user["username"],
        "SELECT", "Member", None, "SUCCESS", {"count": len(rows)}, ip,
    )
    return {"success": True, "data": rows}


# ── GET /api/members/me ────────────────────────────────────────────────────────

@router.get("/me")
def get_my_profile(
    request: Request,
    current_user: dict = Depends(get_current_user),
    track_db=Depends(get_track_db),
    auth_db=Depends(get_auth_db),
):
    ip = request.client.host if request.client else "unknown"
    member = _get_member_or_404(track_db, current_user["member_id"])
    write_audit_log(
        auth_db, current_user["user_id"], current_user["username"],
        "SELECT", "Member", str(current_user["member_id"]), "SUCCESS", None, ip,
    )
    return {"success": True, "data": member}


# ── GET /api/members/{id} — full portfolio ─────────────────────────────────────

@router.get("/{member_id}")
def get_member_portfolio(
    member_id: int,
    request: Request,
    current_user: dict = Depends(get_current_user),
    track_db=Depends(get_track_db),
    auth_db=Depends(get_auth_db),
):
    ip = request.client.host if request.client else "unknown"

    if not _can_view_member(current_user, member_id):
        write_audit_log(
            auth_db, current_user["user_id"], current_user["username"],
            "SELECT", "Member", str(member_id), "UNAUTHORIZED",
            {"reason": "player accessing another member"}, ip,
        )
        raise HTTPException(status_code=403, detail="Access denied")

    member = _get_member_or_404(track_db, member_id)

    # Teams
    track_db.execute(
        """
        SELECT t.TeamID, t.TeamName, tm.Position, tm.IsCaptain, s.SportName
        FROM TeamMember tm
        JOIN Team t  ON tm.TeamID  = t.TeamID
        JOIN Sport s ON t.SportID  = s.SportID
        WHERE tm.MemberID = %s
        """,
        (member_id,),
    )
    teams = track_db.fetchall()

    # Performance logs — RBAC filtered
    if current_user["role"] == "Admin":
        track_db.execute(
            """
            SELECT pl.*, s.SportName FROM PerformanceLog pl
            JOIN Sport s ON pl.SportID = s.SportID
            WHERE pl.MemberID = %s ORDER BY pl.RecordDate DESC
            """,
            (member_id,),
        )
    elif current_user["role"] == "Coach":
        # Coach can see if member is on one of their teams
        track_db.execute(
            """
            SELECT pl.*, s.SportName FROM PerformanceLog pl
            JOIN Sport s ON pl.SportID = s.SportID
            WHERE pl.MemberID = %s
              AND EXISTS (
                  SELECT 1 FROM TeamMember tm
                  JOIN Team t ON tm.TeamID = t.TeamID
                  WHERE tm.MemberID = %s AND t.CoachID = %s
              )
            ORDER BY pl.RecordDate DESC
            """,
            (member_id, member_id, current_user["member_id"]),
        )
    else:
        # Player: own logs only
        if current_user["member_id"] != member_id:
            track_db.execute("SELECT 1 WHERE FALSE")  # empty
        else:
            track_db.execute(
                """
                SELECT pl.*, s.SportName FROM PerformanceLog pl
                JOIN Sport s ON pl.SportID = s.SportID
                WHERE pl.MemberID = %s ORDER BY pl.RecordDate DESC
                """,
                (member_id,),
            )
    perf_logs = track_db.fetchall()

    # Medical records — Admin or own only
    if current_user["role"] == "Admin" or current_user["member_id"] == member_id:
        track_db.execute(
            "SELECT * FROM MedicalRecord WHERE MemberID = %s ORDER BY DiagnosisDate DESC",
            (member_id,),
        )
        medical = track_db.fetchall()
    else:
        medical = []

    write_audit_log(
        auth_db, current_user["user_id"], current_user["username"],
        "SELECT", "Member", str(member_id), "SUCCESS", None, ip,
    )
    return {
        "success": True,
        "data": {
            "member":       member,
            "teams":        teams,
            "performance":  perf_logs,
            "medical":      medical,
        },
    }


# ── POST /api/members ──────────────────────────────────────────────────────────

@router.post("")
def create_member(
    body: MemberCreate,
    request: Request,
    current_user: dict = Depends(require_admin),
    track_db=Depends(get_track_db),
    auth_db=Depends(get_auth_db),
):
    import bcrypt
    ip = request.client.host if request.client else "unknown"

    if body.age <= 0:
        raise HTTPException(status_code=400, detail="Age must be a positive number.")

    # Insert into olympia_track.Member
    try:
        track_db.execute(
            """
            INSERT INTO Member
                (MemberID, Name, Image, Age, Email, ContactNumber, Gender, Role, JoinDate)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                body.member_id, body.name, body.image, body.age, body.email,
                body.contact_number, body.gender, body.role, body.join_date,
            ),
        )
    except Exception as e:
        write_audit_log(
            auth_db, current_user["user_id"], current_user["username"],
            "INSERT", "Member", str(body.member_id), "FAILURE", {"error": str(e)}, ip,
        )
        raise HTTPException(status_code=400, detail=str(e))

    # Create auth account
    pw_hash = bcrypt.hashpw(body.password.encode(), bcrypt.gensalt()).decode()
    try:
        auth_db.execute(
            "INSERT INTO users (username, password_hash, role, member_id) VALUES (%s, %s, %s, %s)",
            (body.username, pw_hash, body.role, body.member_id),
        )
    except Exception as e:
        write_audit_log(
            auth_db, current_user["user_id"], current_user["username"],
            "INSERT", "users", None, "FAILURE", {"error": str(e)}, ip,
        )
        raise HTTPException(status_code=400, detail=f"Auth account error: {e}")

    write_audit_log(
        auth_db, current_user["user_id"], current_user["username"],
        "INSERT", "Member", str(body.member_id), "SUCCESS",
        {"name": body.name, "role": body.role}, ip,
    )
    return {"success": True, "message": "Member created", "data": {"member_id": body.member_id}}


# ── PUT /api/members/{id} ──────────────────────────────────────────────────────

@router.put("/{member_id}")
def update_member(
    member_id: int,
    body: MemberUpdate,
    request: Request,
    current_user: dict = Depends(get_current_user),
    track_db=Depends(get_track_db),
    auth_db=Depends(get_auth_db),
):
    ip = request.client.host if request.client else "unknown"

    # Admin can update anyone; others only themselves
    if current_user["role"] != "Admin" and current_user["member_id"] != member_id:
        write_audit_log(
            auth_db, current_user["user_id"], current_user["username"],
            "UPDATE", "Member", str(member_id), "UNAUTHORIZED",
            {"reason": "not admin and not own profile"}, ip,
        )
        raise HTTPException(status_code=403, detail="Access denied")

    _get_member_or_404(track_db, member_id)

    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")
    if "age" in fields and fields["age"] <= 0:
        raise HTTPException(status_code=400, detail="Age must be a positive number.")

    col_map = {
        "name": "Name", "age": "Age", "email": "Email",
        "contact_number": "ContactNumber", "image": "Image",
    }
    set_clause = ", ".join(f"{col_map[k]} = %s" for k in fields)
    values = list(fields.values()) + [member_id]

    track_db.execute(f"UPDATE Member SET {set_clause} WHERE MemberID = %s", values)

    write_audit_log(
        auth_db, current_user["user_id"], current_user["username"],
        "UPDATE", "Member", str(member_id), "SUCCESS", fields, ip,
    )
    return {"success": True, "message": "Member updated"}


# ── DELETE /api/members/{id} ───────────────────────────────────────────────────

@router.delete("/{member_id}")
def delete_member(
    member_id: int,
    request: Request,
    current_user: dict = Depends(require_admin),
    track_db=Depends(get_track_db),
    auth_db=Depends(get_auth_db),
):
    ip = request.client.host if request.client else "unknown"

    _get_member_or_404(track_db, member_id)

    # Nullify user account link before deleting member
    auth_db.execute(
        "UPDATE users SET member_id = NULL WHERE member_id = %s", (member_id,)
    )
    track_db.execute("DELETE FROM Member WHERE MemberID = %s", (member_id,))

    write_audit_log(
        auth_db, current_user["user_id"], current_user["username"],
        "DELETE", "Member", str(member_id), "SUCCESS", None, ip,
    )
    return {"success": True, "message": f"Member {member_id} deleted"}
