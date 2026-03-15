from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from typing import Optional

from app.auth.dependencies import get_current_user, require_admin
from app.database import get_auth_db, get_track_db
from app.services.audit import write_audit_log

router = APIRouter()


class EventCreate(BaseModel):
    event_id:      int
    event_name:    str
    event_date:    str
    start_time:    str
    end_time:      str
    venue_id:      int
    sport_id:      int
    status:        str   # Scheduled | Ongoing | Completed | Cancelled
    tournament_id: Optional[int] = None
    round:         Optional[str] = None


class EventUpdate(BaseModel):
    event_name:    Optional[str] = None
    event_date:    Optional[str] = None
    start_time:    Optional[str] = None
    end_time:      Optional[str] = None
    venue_id:      Optional[int] = None
    sport_id:      Optional[int] = None
    status:        Optional[str] = None
    tournament_id: Optional[int] = None
    round:         Optional[str] = None


@router.get("")
def list_events(
    request: Request,
    current_user: dict = Depends(get_current_user),
    track_db=Depends(get_track_db),
    auth_db=Depends(get_auth_db),
    tournament_id: Optional[int] = None,
    sport_id:      Optional[int] = None,
    status:        Optional[str] = None,
):
    ip = request.client.host if request.client else "unknown"

    query = """
        SELECT e.*, s.SportName, v.VenueName, t.TournamentName
        FROM Event e
        JOIN Sport s ON e.SportID = s.SportID
        JOIN Venue v ON e.VenueID = v.VenueID
        LEFT JOIN Tournament t ON e.TournamentID = t.TournamentID
        WHERE 1=1
    """
    params = []
    if tournament_id:
        query += " AND e.TournamentID = %s"; params.append(tournament_id)
    if sport_id:
        query += " AND e.SportID = %s";      params.append(sport_id)
    if status:
        query += " AND e.Status = %s";       params.append(status)
    query += " ORDER BY e.EventDate DESC"

    track_db.execute(query, params)
    rows = track_db.fetchall()
    for r in rows:
        r["EventDate"]  = str(r["EventDate"])
        r["StartTime"]  = str(r["StartTime"])
        r["EndTime"]    = str(r["EndTime"])

    write_audit_log(auth_db, current_user["user_id"], current_user["username"],
                    "SELECT", "Event", None, "SUCCESS", {"count": len(rows)}, ip)
    return {"success": True, "data": rows}


@router.get("/{event_id}")
def get_event(
    event_id: int,
    request: Request,
    current_user: dict = Depends(get_current_user),
    track_db=Depends(get_track_db),
    auth_db=Depends(get_auth_db),
):
    ip = request.client.host if request.client else "unknown"
    track_db.execute(
        """
        SELECT e.*, s.SportName, v.VenueName, t.TournamentName
        FROM Event e
        JOIN Sport s ON e.SportID  = s.SportID
        JOIN Venue v ON e.VenueID  = v.VenueID
        LEFT JOIN Tournament t ON e.TournamentID = t.TournamentID
        WHERE e.EventID = %s
        """,
        (event_id,),
    )
    event = track_db.fetchone()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # Participating teams + results
    track_db.execute(
        """
        SELECT p.*, tm.TeamName
        FROM Participation p
        JOIN Team tm ON p.TeamID = tm.TeamID
        WHERE p.EventID = %s
        """,
        (event_id,),
    )
    participation = track_db.fetchall()

    for k in ("EventDate", "StartTime", "EndTime"):
        if event.get(k):
            event[k] = str(event[k])

    write_audit_log(auth_db, current_user["user_id"], current_user["username"],
                    "SELECT", "Event", str(event_id), "SUCCESS", None, ip)
    return {"success": True, "data": {"event": event, "participation": participation}}


@router.post("")
def create_event(
    body: EventCreate,
    request: Request,
    current_user: dict = Depends(require_admin),
    track_db=Depends(get_track_db),
    auth_db=Depends(get_auth_db),
):
    ip = request.client.host if request.client else "unknown"
    try:
        track_db.execute(
            """
            INSERT INTO Event
                (EventID, EventName, TournamentID, EventDate, StartTime, EndTime,
                 VenueID, SportID, Status, Round)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (body.event_id, body.event_name, body.tournament_id, body.event_date,
             body.start_time, body.end_time, body.venue_id, body.sport_id,
             body.status, body.round),
        )
    except Exception as e:
        write_audit_log(auth_db, current_user["user_id"], current_user["username"],
                        "INSERT", "Event", str(body.event_id), "FAILURE", {"error": str(e)}, ip)
        raise HTTPException(status_code=400, detail=str(e))

    write_audit_log(auth_db, current_user["user_id"], current_user["username"],
                    "INSERT", "Event", str(body.event_id), "SUCCESS",
                    {"name": body.event_name}, ip)
    return {"success": True, "message": "Event created", "data": {"event_id": body.event_id}}


@router.put("/{event_id}")
def update_event(
    event_id: int,
    body: EventUpdate,
    request: Request,
    current_user: dict = Depends(require_admin),
    track_db=Depends(get_track_db),
    auth_db=Depends(get_auth_db),
):
    ip = request.client.host if request.client else "unknown"
    track_db.execute("SELECT EventID FROM Event WHERE EventID=%s", (event_id,))
    if not track_db.fetchone():
        raise HTTPException(status_code=404, detail="Event not found")

    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")

    col_map = {
        "event_name": "EventName", "event_date": "EventDate", "start_time": "StartTime",
        "end_time": "EndTime", "venue_id": "VenueID", "sport_id": "SportID",
        "status": "Status", "tournament_id": "TournamentID", "round": "Round",
    }
    set_clause = ", ".join(f"{col_map[k]} = %s" for k in fields)
    track_db.execute(f"UPDATE Event SET {set_clause} WHERE EventID=%s",
                     list(fields.values()) + [event_id])
    write_audit_log(auth_db, current_user["user_id"], current_user["username"],
                    "UPDATE", "Event", str(event_id), "SUCCESS", fields, ip)
    return {"success": True, "message": "Event updated"}


@router.delete("/{event_id}")
def delete_event(
    event_id: int,
    request: Request,
    current_user: dict = Depends(require_admin),
    track_db=Depends(get_track_db),
    auth_db=Depends(get_auth_db),
):
    ip = request.client.host if request.client else "unknown"
    track_db.execute("SELECT EventID FROM Event WHERE EventID=%s", (event_id,))
    if not track_db.fetchone():
        raise HTTPException(status_code=404, detail="Event not found")

    track_db.execute("DELETE FROM Event WHERE EventID=%s", (event_id,))
    write_audit_log(auth_db, current_user["user_id"], current_user["username"],
                    "DELETE", "Event", str(event_id), "SUCCESS", None, ip)
    return {"success": True, "message": f"Event {event_id} deleted"}
