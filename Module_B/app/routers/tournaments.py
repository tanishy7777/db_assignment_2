from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from typing import Optional

from app.auth.dependencies import get_current_user, require_admin
from app.database import get_auth_db, get_track_db
from app.services.audit import write_audit_log

router = APIRouter()


class TournamentCreate(BaseModel):
    tournament_id:   int
    tournament_name: str
    start_date:      str
    end_date:        str
    status:          str   # Upcoming | Ongoing | Completed
    description:     Optional[str] = None


@router.get("")
def list_tournaments(
    request: Request,
    current_user: dict = Depends(get_current_user),
    track_db=Depends(get_track_db),
    auth_db=Depends(get_auth_db),
):
    ip = request.client.host if request.client else "unknown"
    track_db.execute("SELECT * FROM Tournament ORDER BY StartDate DESC")
    rows = track_db.fetchall()
    for r in rows:
        r["StartDate"] = str(r["StartDate"])
        r["EndDate"]   = str(r["EndDate"])
    write_audit_log(auth_db, current_user["user_id"], current_user["username"],
                    "SELECT", "Tournament", None, "SUCCESS", {"count": len(rows)}, ip)
    return {"success": True, "data": rows}


@router.post("")
def create_tournament(
    body: TournamentCreate,
    request: Request,
    current_user: dict = Depends(require_admin),
    track_db=Depends(get_track_db),
    auth_db=Depends(get_auth_db),
):
    ip = request.client.host if request.client else "unknown"
    try:
        track_db.execute(
            "INSERT INTO Tournament (TournamentID, TournamentName, StartDate, EndDate, Description, Status) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (body.tournament_id, body.tournament_name, body.start_date,
             body.end_date, body.description, body.status),
        )
    except Exception as e:
        write_audit_log(auth_db, current_user["user_id"], current_user["username"],
                        "INSERT", "Tournament", str(body.tournament_id), "FAILURE", {"error": str(e)}, ip)
        raise HTTPException(status_code=400, detail=str(e))

    write_audit_log(auth_db, current_user["user_id"], current_user["username"],
                    "INSERT", "Tournament", str(body.tournament_id), "SUCCESS",
                    {"name": body.tournament_name}, ip)
    return {"success": True, "message": "Tournament created",
            "data": {"tournament_id": body.tournament_id}}
