from fastapi import APIRouter, Depends, Request



from app.auth.dependencies import require_admin

from app.database import get_auth_db

from app.services.audit import verify_audit_chain



router = APIRouter()





@router.get("/audit-log")

def get_audit_log(

    limit: int = 100,

    current_user: dict = Depends(require_admin),

    db=Depends(get_auth_db),

):

    db.execute(

        "SELECT * FROM audit_log ORDER BY log_id DESC LIMIT %s", (limit,)

    )

    rows = db.fetchall()


    for row in rows:

        if row.get("timestamp"):

            row["timestamp"] = str(row["timestamp"])

    return {"success": True, "data": rows}





@router.get("/verify-audit")

def verify_audit(

    current_user: dict = Depends(require_admin),

    db=Depends(get_auth_db),

):

    result = verify_audit_chain(db)

    return {"success": True, "data": result}

