from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.auth.dependencies import get_current_user, require_admin_or_coach
from app.database import get_auth_db, get_track_db
from app.services.audit import write_audit_log

router = APIRouter()


class PerfLogCreate(BaseModel):
    log_id:       int
    member_id:    int
    sport_id:     int
    metric_name:  str
    metric_value: float
    record_date:  str


@router.get("")
def list_performance_logs(
    request: Request,
    current_user: dict = Depends(get_current_user),
    track_db=Depends(get_track_db),
    auth_db=Depends(get_auth_db),
):
    ip = request.client.host if request.client else "unknown"

    if current_user["role"] == "Admin":
        track_db.execute(
            """
            SELECT pl.*, m.Name AS MemberName, s.SportName
            FROM PerformanceLog pl
            JOIN Member m ON pl.MemberID = m.MemberID
            JOIN Sport  s ON pl.SportID  = s.SportID
            ORDER BY pl.RecordDate DESC
            """
        )
    elif current_user["role"] == "Coach":
        track_db.execute(
            """
            SELECT pl.*, m.Name AS MemberName, s.SportName
            FROM PerformanceLog pl
            JOIN Member m ON pl.MemberID = m.MemberID
            JOIN Sport  s ON pl.SportID  = s.SportID
            WHERE pl.MemberID IN (
                SELECT tm.MemberID FROM TeamMember tm
                JOIN Team t ON tm.TeamID = t.TeamID
                WHERE t.CoachID = %s
            )
            ORDER BY pl.RecordDate DESC
            """,
            (current_user["member_id"],),
        )
    else:  # Player
        track_db.execute(
            """
            SELECT pl.*, m.Name AS MemberName, s.SportName
            FROM PerformanceLog pl
            JOIN Member m ON pl.MemberID = m.MemberID
            JOIN Sport  s ON pl.SportID  = s.SportID
            WHERE pl.MemberID = %s
            ORDER BY pl.RecordDate DESC
            """,
            (current_user["member_id"],),
        )

    rows = track_db.fetchall()
    for r in rows:
        r["RecordDate"] = str(r["RecordDate"])

    write_audit_log(auth_db, current_user["user_id"], current_user["username"],
                    "SELECT", "PerformanceLog", None, "SUCCESS", {"count": len(rows)}, ip)
    return {"success": True, "data": rows}


@router.post("")
def create_performance_log(
    body: PerfLogCreate,
    request: Request,
    current_user: dict = Depends(require_admin_or_coach),
    track_db=Depends(get_track_db),
    auth_db=Depends(get_auth_db),
):
    ip = request.client.host if request.client else "unknown"
    try:
        track_db.execute(
            "INSERT INTO PerformanceLog (LogID, MemberID, SportID, MetricName, MetricValue, RecordDate) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (body.log_id, body.member_id, body.sport_id,
             body.metric_name, body.metric_value, body.record_date),
        )
    except Exception as e:
        write_audit_log(auth_db, current_user["user_id"], current_user["username"],
                        "INSERT", "PerformanceLog", str(body.log_id), "FAILURE", {"error": str(e)}, ip)
        raise HTTPException(status_code=400, detail=str(e))

    write_audit_log(auth_db, current_user["user_id"], current_user["username"],
                    "INSERT", "PerformanceLog", str(body.log_id), "SUCCESS",
                    {"member_id": body.member_id, "metric": body.metric_name}, ip)
    return {"success": True, "message": "Performance log created", "data": {"log_id": body.log_id}}
