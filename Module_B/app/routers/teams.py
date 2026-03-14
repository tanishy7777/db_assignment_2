from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from typing import Optional

from app.auth.dependencies import get_current_user, require_admin, require_admin_or_coach
from app.database import get_auth_db, get_track_db
from app.services.audit import write_audit_log

router = APIRouter()


class TeamCreate(BaseModel):
    team_id:    int
    team_name:  str
    sport_id:   int
    formed_date: str
    coach_id:   Optional[int] = None
    captain_id: Optional[int] = None


class TeamUpdate(BaseModel):
    team_name:  Optional[str] = None
    coach_id:   Optional[int] = None
    captain_id: Optional[int] = None


@router.get("")
def list_teams(
    request: Request,
    current_user: dict = Depends(get_current_user),
    track_db=Depends(get_track_db),
    auth_db=Depends(get_auth_db),
):
    ip = request.client.host if request.client else "unknown"
    track_db.execute(
        """
        SELECT t.*, s.SportName,
               m.Name AS CoachName
        FROM Team t
        JOIN Sport s ON t.SportID = s.SportID
        LEFT JOIN Member m ON t.CoachID = m.MemberID
        ORDER BY t.TeamID
        """
    )
    rows = track_db.fetchall()
    write_audit_log(auth_db, current_user["user_id"], current_user["username"],
                    "SELECT", "Team", None, "SUCCESS", {"count": len(rows)}, ip)
    return {"success": True, "data": rows}


@router.get("/{team_id}")
def get_team(
    team_id: int,
    request: Request,
    current_user: dict = Depends(get_current_user),
    track_db=Depends(get_track_db),
    auth_db=Depends(get_auth_db),
):
    ip = request.client.host if request.client else "unknown"
    track_db.execute(
        """
        SELECT t.*, s.SportName, m.Name AS CoachName
        FROM Team t
        JOIN Sport s ON t.SportID = s.SportID
        LEFT JOIN Member m ON t.CoachID = m.MemberID
        WHERE t.TeamID = %s
        """,
        (team_id,),
    )
    team = track_db.fetchone()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    track_db.execute(
        """
        SELECT tm.*, m.Name, m.Role, m.Email
        FROM TeamMember tm
        JOIN Member m ON tm.MemberID = m.MemberID
        WHERE tm.TeamID = %s
        """,
        (team_id,),
    )
    roster = track_db.fetchall()

    write_audit_log(auth_db, current_user["user_id"], current_user["username"],
                    "SELECT", "Team", str(team_id), "SUCCESS", None, ip)
    return {"success": True, "data": {"team": team, "roster": roster}}


@router.post("")
def create_team(
    body: TeamCreate,
    request: Request,
    current_user: dict = Depends(require_admin),
    track_db=Depends(get_track_db),
    auth_db=Depends(get_auth_db),
):
    ip = request.client.host if request.client else "unknown"
    try:
        track_db.execute(
            "INSERT INTO Team (TeamID, TeamName, CoachID, CaptainID, SportID, FormedDate) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (body.team_id, body.team_name, body.coach_id, body.captain_id,
             body.sport_id, body.formed_date),
        )
    except Exception as e:
        write_audit_log(auth_db, current_user["user_id"], current_user["username"],
                        "INSERT", "Team", str(body.team_id), "FAILURE", {"error": str(e)}, ip)
        raise HTTPException(status_code=400, detail=str(e))

    write_audit_log(auth_db, current_user["user_id"], current_user["username"],
                    "INSERT", "Team", str(body.team_id), "SUCCESS",
                    {"name": body.team_name}, ip)
    return {"success": True, "message": "Team created", "data": {"team_id": body.team_id}}


@router.put("/{team_id}")
def update_team(
    team_id: int,
    body: TeamUpdate,
    request: Request,
    current_user: dict = Depends(require_admin_or_coach),
    track_db=Depends(get_track_db),
    auth_db=Depends(get_auth_db),
):
    ip = request.client.host if request.client else "unknown"

    # Coach can only update their own teams
    if current_user["role"] == "Coach":
        track_db.execute(
            "SELECT TeamID FROM Team WHERE TeamID = %s AND CoachID = %s",
            (team_id, current_user["member_id"]),
        )
        if not track_db.fetchone():
            raise HTTPException(status_code=403, detail="Coach can only update their own teams")

    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")

    col_map = {"team_name": "TeamName", "coach_id": "CoachID", "captain_id": "CaptainID"}
    set_clause = ", ".join(f"{col_map[k]} = %s" for k in fields)
    track_db.execute(f"UPDATE Team SET {set_clause} WHERE TeamID = %s",
                     list(fields.values()) + [team_id])

    write_audit_log(auth_db, current_user["user_id"], current_user["username"],
                    "UPDATE", "Team", str(team_id), "SUCCESS", fields, ip)
    return {"success": True, "message": "Team updated"}


@router.delete("/{team_id}")
def delete_team(
    team_id: int,
    request: Request,
    current_user: dict = Depends(require_admin),
    track_db=Depends(get_track_db),
    auth_db=Depends(get_auth_db),
):
    ip = request.client.host if request.client else "unknown"
    track_db.execute("SELECT TeamID FROM Team WHERE TeamID = %s", (team_id,))
    if not track_db.fetchone():
        raise HTTPException(status_code=404, detail="Team not found")

    track_db.execute("DELETE FROM Team WHERE TeamID = %s", (team_id,))
    write_audit_log(auth_db, current_user["user_id"], current_user["username"],
                    "DELETE", "Team", str(team_id), "SUCCESS", None, ip)
    return {"success": True, "message": f"Team {team_id} deleted"}
