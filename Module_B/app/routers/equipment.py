from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from typing import Optional

from app.auth.dependencies import get_current_user, require_admin_or_coach
from app.database import get_auth_db, get_track_db
from app.services.audit import write_audit_log

router = APIRouter()


class IssueCreate(BaseModel):
    issue_id:     int
    equipment_id: int
    member_id:    int
    issue_date:   str
    quantity:     int
    return_date:  Optional[str] = None


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
        SELECT e.*, s.SportName
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
):
    ip = request.client.host if request.client else "unknown"

    if current_user["role"] == "Player":
        track_db.execute(
            """
            SELECT ei.*, e.EquipmentName, m.Name AS MemberName
            FROM EquipmentIssue ei
            JOIN Equipment e ON ei.EquipmentID = e.EquipmentID
            JOIN Member m    ON ei.MemberID    = m.MemberID
            WHERE ei.MemberID = %s
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

    if body.quantity <= 0:
        raise HTTPException(status_code=400, detail="Quantity must be a positive number.")

    # Check available stock
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
        track_db.execute(
            "INSERT INTO EquipmentIssue (IssueID, EquipmentID, MemberID, IssueDate, ReturnDate, Quantity) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (body.issue_id, body.equipment_id, body.member_id,
             body.issue_date, body.return_date, body.quantity),
        )
    except Exception as e:
        write_audit_log(auth_db, current_user["user_id"], current_user["username"],
                        "INSERT", "EquipmentIssue", str(body.issue_id), "FAILURE", {"error": str(e)}, ip)
        raise HTTPException(status_code=400, detail=str(e))

    write_audit_log(auth_db, current_user["user_id"], current_user["username"],
                    "INSERT", "EquipmentIssue", str(body.issue_id), "SUCCESS",
                    {"equipment_id": body.equipment_id, "member_id": body.member_id}, ip)
    return {"success": True, "message": "Equipment issued", "data": {"issue_id": body.issue_id}}


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
    track_db.execute("SELECT IssueID FROM EquipmentIssue WHERE IssueID = %s", (issue_id,))
    if not track_db.fetchone():
        raise HTTPException(status_code=404, detail="Issue record not found")

    track_db.execute(
        "UPDATE EquipmentIssue SET ReturnDate = %s WHERE IssueID = %s",
        (return_date, issue_id),
    )
    write_audit_log(auth_db, current_user["user_id"], current_user["username"],
                    "UPDATE", "EquipmentIssue", str(issue_id), "SUCCESS",
                    {"return_date": return_date}, ip)
    return {"success": True, "message": "Equipment returned"}
