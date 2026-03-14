from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from typing import Optional

from app.auth.dependencies import get_current_user, require_admin
from app.database import get_auth_db, get_track_db
from app.services.audit import write_audit_log

router = APIRouter()


class MedicalCreate(BaseModel):
    record_id:         int
    member_id:         int
    medical_condition: str
    diagnosis_date:    str
    status:            str   # Active | Recovered | Chronic
    recovery_date:     Optional[str] = None


@router.get("/{member_id}")
def get_medical_records(
    member_id: int,
    request: Request,
    current_user: dict = Depends(get_current_user),
    track_db=Depends(get_track_db),
    auth_db=Depends(get_auth_db),
):
    ip = request.client.host if request.client else "unknown"

    # Admin or own records only
    if current_user["role"] != "Admin" and current_user["member_id"] != member_id:
        write_audit_log(auth_db, current_user["user_id"], current_user["username"],
                        "SELECT", "MedicalRecord", str(member_id), "UNAUTHORIZED",
                        {"reason": "not admin and not own record"}, ip)
        raise HTTPException(status_code=403, detail="Access denied")

    track_db.execute(
        "SELECT * FROM MedicalRecord WHERE MemberID = %s ORDER BY DiagnosisDate DESC",
        (member_id,),
    )
    rows = track_db.fetchall()
    for r in rows:
        r["DiagnosisDate"] = str(r["DiagnosisDate"])
        if r.get("RecoveryDate"):
            r["RecoveryDate"] = str(r["RecoveryDate"])

    write_audit_log(auth_db, current_user["user_id"], current_user["username"],
                    "SELECT", "MedicalRecord", str(member_id), "SUCCESS",
                    {"count": len(rows)}, ip)
    return {"success": True, "data": rows}


@router.post("")
def create_medical_record(
    body: MedicalCreate,
    request: Request,
    current_user: dict = Depends(require_admin),
    track_db=Depends(get_track_db),
    auth_db=Depends(get_auth_db),
):
    ip = request.client.host if request.client else "unknown"
    try:
        track_db.execute(
            """
            INSERT INTO MedicalRecord
                (RecordID, MemberID, MedicalCondition, DiagnosisDate, RecoveryDate, Status)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (body.record_id, body.member_id, body.medical_condition,
             body.diagnosis_date, body.recovery_date, body.status),
        )
    except Exception as e:
        write_audit_log(auth_db, current_user["user_id"], current_user["username"],
                        "INSERT", "MedicalRecord", str(body.record_id), "FAILURE",
                        {"error": str(e)}, ip)
        raise HTTPException(status_code=400, detail=str(e))

    write_audit_log(auth_db, current_user["user_id"], current_user["username"],
                    "INSERT", "MedicalRecord", str(body.record_id), "SUCCESS",
                    {"member_id": body.member_id, "condition": body.medical_condition}, ip)
    return {"success": True, "message": "Medical record created",
            "data": {"record_id": body.record_id}}
