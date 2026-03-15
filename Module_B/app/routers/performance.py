from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from typing import Optional

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


class PerfLogUpdate(BaseModel):
    sport_id:     Optional[int]   = None
    metric_name:  Optional[str]   = None
    metric_value: Optional[float] = None
    record_date:  Optional[str]   = None


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


@router.put("/{log_id}")
def update_performance_log(
    log_id: int,
    body: PerfLogUpdate,
    request: Request,
    current_user: dict = Depends(require_admin_or_coach),
    track_db=Depends(get_track_db),
    auth_db=Depends(get_auth_db),
):
    ip = request.client.host if request.client else "unknown"
    track_db.execute("SELECT LogID, MemberID FROM PerformanceLog WHERE LogID=%s", (log_id,))
    row = track_db.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Log not found")

    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")

    col_map = {
        "sport_id": "SportID", "metric_name": "MetricName",
        "metric_value": "MetricValue", "record_date": "RecordDate",
    }
    set_clause = ", ".join(f"{col_map[k]} = %s" for k in fields)
    track_db.execute(f"UPDATE PerformanceLog SET {set_clause} WHERE LogID=%s",
                     list(fields.values()) + [log_id])
    write_audit_log(auth_db, current_user["user_id"], current_user["username"],
                    "UPDATE", "PerformanceLog", str(log_id), "SUCCESS", fields, ip)
    return {"success": True, "message": "Performance log updated"}


@router.delete("/{log_id}")
def delete_performance_log(
    log_id: int,
    request: Request,
    current_user: dict = Depends(require_admin_or_coach),
    track_db=Depends(get_track_db),
    auth_db=Depends(get_auth_db),
):
    ip = request.client.host if request.client else "unknown"
    track_db.execute("SELECT LogID FROM PerformanceLog WHERE LogID=%s", (log_id,))
    if not track_db.fetchone():
        raise HTTPException(status_code=404, detail="Log not found")

    track_db.execute("DELETE FROM PerformanceLog WHERE LogID=%s", (log_id,))
    write_audit_log(auth_db, current_user["user_id"], current_user["username"],
                    "DELETE", "PerformanceLog", str(log_id), "SUCCESS", None, ip)
    return {"success": True, "message": f"Log {log_id} deleted"}
