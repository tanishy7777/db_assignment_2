from fastapi import APIRouter, Depends, HTTPException, Request

from pydantic import BaseModel

from typing import Optional



from app.auth.dependencies import get_current_user, require_admin, require_admin_or_coach

from app.database import get_auth_db, get_track_db

from app.services.audit import write_audit_log

from app.services.id_generation import insert_with_generated_id

from app.services.validation import humanize_db_error



router = APIRouter()





class EquipmentCreate(BaseModel):

    equipment_id: Optional[int] = None

    equipment_name: str

    total_quantity: int

    equipment_condition: str

    sport_id: Optional[int] = None





class IssueCreate(BaseModel):

    issue_id:     Optional[int] = None

    equipment_id: int

    member_id:    int

    issue_date:   str

    quantity:     int

    return_date:  Optional[str] = None





def _ensure_coach_can_manage_member(track_db, current_user: dict, member_id: int) -> None:

    if current_user["role"] != "Coach":

        return

    track_db.execute(

        """
        SELECT 1
        FROM TeamMember tm
        JOIN Team t ON tm.TeamID = t.TeamID
        WHERE tm.MemberID = %s AND t.CoachID = %s
        LIMIT 1
        """,

        (member_id, current_user["member_id"]),

    )

    if not track_db.fetchone():

        raise HTTPException(status_code=403, detail="Coach can only manage equipment for players on their teams.")





@router.post("")

def create_equipment(

    body: EquipmentCreate,

    request: Request,

    current_user: dict = Depends(require_admin),

    track_db=Depends(get_track_db),

    auth_db=Depends(get_auth_db),

):

    ip = request.client.host if request.client else "unknown"

    if body.total_quantity < 0:

        raise HTTPException(status_code=400, detail="Total quantity cannot be negative.")



    equipment_id = body.equipment_id

    try:

        equipment_id = insert_with_generated_id(

            track_db,

            requested_id=equipment_id,

            next_id_sql="SELECT COALESCE(MAX(EquipmentID), 0) + 1 AS nid FROM Equipment",

            insert_fn=lambda generated_id: track_db.execute(

                """
                INSERT INTO Equipment (EquipmentID, EquipmentName, TotalQuantity, EquipmentCondition, SportID)
                VALUES (%s, %s, %s, %s, %s)
                """,

                (

                    generated_id,

                    body.equipment_name,

                    body.total_quantity,

                    body.equipment_condition,

                    body.sport_id,

                ),

            ),

        )

    except Exception as exc:

        write_audit_log(auth_db, current_user["user_id"], current_user["username"],

                        "INSERT", "Equipment", str(equipment_id), "FAILURE", {"error": str(exc)}, ip)

        raise HTTPException(status_code=400, detail=humanize_db_error(exc)) from exc



    write_audit_log(auth_db, current_user["user_id"], current_user["username"],

                    "INSERT", "Equipment", str(equipment_id), "SUCCESS",

                    {"name": body.equipment_name}, ip)

    return {"success": True, "message": "Equipment created", "data": {"equipment_id": equipment_id}}





@router.get("")

def list_equipment(

    request: Request,

    current_user: dict = Depends(get_current_user),

    track_db=Depends(get_track_db),

    auth_db=Depends(get_auth_db),

):

    ip = request.client.host if request.client else "unknown"

    track_db.execute(

        """
        SELECT e.*, s.SportName,
               e.TotalQuantity - COALESCE((
                   SELECT SUM(ei.Quantity)
                   FROM EquipmentIssue ei
                   WHERE ei.EquipmentID = e.EquipmentID
                     AND ei.ReturnDate IS NULL
               ), 0) AS AvailableQuantity
        FROM Equipment e
        LEFT JOIN Sport s ON e.SportID = s.SportID
        ORDER BY e.EquipmentID
        """

    )

    rows = track_db.fetchall()

    write_audit_log(auth_db, current_user["user_id"], current_user["username"],

                    "SELECT", "Equipment", None, "SUCCESS", {"count": len(rows)}, ip)

    return {"success": True, "data": rows}





@router.get("/issues")

def list_issues(

    request: Request,

    current_user: dict = Depends(get_current_user),

    track_db=Depends(get_track_db),

    auth_db=Depends(get_auth_db),

    active_only: bool = False,

):

    ip = request.client.host if request.client else "unknown"

    active_clause = " AND ei.ReturnDate IS NULL" if active_only else ""



    if current_user["role"] == "Player":

        track_db.execute(

            """
            SELECT ei.*, e.EquipmentName, m.Name AS MemberName
            FROM EquipmentIssue ei
            JOIN Equipment e ON ei.EquipmentID = e.EquipmentID
            JOIN Member m    ON ei.MemberID    = m.MemberID
            WHERE ei.MemberID = %s
            """ + active_clause + """
            ORDER BY ei.IssueDate DESC
            """,

            (current_user["member_id"],),

        )

    elif current_user["role"] == "Coach":

        track_db.execute(

            """
            SELECT ei.*, e.EquipmentName, m.Name AS MemberName
            FROM EquipmentIssue ei
            JOIN Equipment e ON ei.EquipmentID = e.EquipmentID
            JOIN Member m    ON ei.MemberID    = m.MemberID
            WHERE EXISTS (
                SELECT 1
                FROM TeamMember tm
                JOIN Team t ON tm.TeamID = t.TeamID
                WHERE tm.MemberID = ei.MemberID
                  AND t.CoachID = %s
            )
            """ + active_clause + """
            ORDER BY ei.IssueDate DESC
            """,

            (current_user["member_id"],),

        )

    else:

        track_db.execute(

            """
            SELECT ei.*, e.EquipmentName, m.Name AS MemberName
            FROM EquipmentIssue ei
            JOIN Equipment e ON ei.EquipmentID = e.EquipmentID
            JOIN Member m    ON ei.MemberID    = m.MemberID
            WHERE 1 = 1
            """ + active_clause + """
            ORDER BY ei.IssueDate DESC
            """

        )

    rows = track_db.fetchall()

    for r in rows:

        r["IssueDate"]  = str(r["IssueDate"])

        if r.get("ReturnDate"):

            r["ReturnDate"] = str(r["ReturnDate"])



    write_audit_log(auth_db, current_user["user_id"], current_user["username"],

                    "SELECT", "EquipmentIssue", None, "SUCCESS", {"count": len(rows)}, ip)

    return {"success": True, "data": rows}





@router.post("/issue")

def issue_equipment(

    body: IssueCreate,

    request: Request,

    current_user: dict = Depends(require_admin_or_coach),

    track_db=Depends(get_track_db),

    auth_db=Depends(get_auth_db),

):

    ip = request.client.host if request.client else "unknown"

    issue_id = body.issue_id

    if body.quantity <= 0:

        raise HTTPException(status_code=400, detail="Quantity must be a positive number.")

    _ensure_coach_can_manage_member(track_db, current_user, body.member_id)




    track_db.execute(

        """
        SELECT e.TotalQuantity,
               COALESCE(SUM(ei.Quantity), 0) AS issued
        FROM Equipment e
        LEFT JOIN EquipmentIssue ei
               ON e.EquipmentID = ei.EquipmentID AND ei.ReturnDate IS NULL
        WHERE e.EquipmentID = %s
        GROUP BY e.TotalQuantity
        """,

        (body.equipment_id,),

    )

    stock = track_db.fetchone()

    if not stock:

        raise HTTPException(status_code=404, detail="Equipment not found.")

    available = stock["TotalQuantity"] - stock["issued"]

    if body.quantity > available:

        raise HTTPException(

            status_code=400,

            detail=f"Cannot issue {body.quantity} item(s). Only {available} available.",

        )



    try:

        issue_id = insert_with_generated_id(

            track_db,

            requested_id=issue_id,

            next_id_sql="SELECT COALESCE(MAX(IssueID), 0) + 1 AS nid FROM EquipmentIssue",

            insert_fn=lambda issue_id: track_db.execute(

                "INSERT INTO EquipmentIssue (IssueID, EquipmentID, MemberID, IssueDate, ReturnDate, Quantity) "

                "VALUES (%s, %s, %s, %s, %s, %s)",

                (issue_id, body.equipment_id, body.member_id,

                 body.issue_date, body.return_date, body.quantity),

            ),

        )

    except Exception as e:

        write_audit_log(auth_db, current_user["user_id"], current_user["username"],

                        "INSERT", "EquipmentIssue", str(issue_id), "FAILURE", {"error": str(e)}, ip)

        raise HTTPException(status_code=400, detail=humanize_db_error(e)) from e



    write_audit_log(auth_db, current_user["user_id"], current_user["username"],

                    "INSERT", "EquipmentIssue", str(issue_id), "SUCCESS",

                    {"equipment_id": body.equipment_id, "member_id": body.member_id}, ip)

    return {"success": True, "message": "Equipment issued", "data": {"issue_id": issue_id}}





@router.put("/issue/{issue_id}/return")

def return_equipment(

    issue_id: int,

    return_date: str,

    request: Request,

    current_user: dict = Depends(require_admin_or_coach),

    track_db=Depends(get_track_db),

    auth_db=Depends(get_auth_db),

):

    ip = request.client.host if request.client else "unknown"

    track_db.execute("SELECT IssueID, MemberID FROM EquipmentIssue WHERE IssueID = %s", (issue_id,))

    issue = track_db.fetchone()

    if not issue:

        raise HTTPException(status_code=404, detail="Issue record not found")

    if current_user["role"] == "Coach":

        _ensure_coach_can_manage_member(track_db, current_user, issue["MemberID"])



    track_db.execute(

        "UPDATE EquipmentIssue SET ReturnDate = %s WHERE IssueID = %s",

        (return_date, issue_id),

    )

    write_audit_log(auth_db, current_user["user_id"], current_user["username"],

                    "UPDATE", "EquipmentIssue", str(issue_id), "SUCCESS",

                    {"return_date": return_date}, ip)

    return {"success": True, "message": "Equipment returned"}

