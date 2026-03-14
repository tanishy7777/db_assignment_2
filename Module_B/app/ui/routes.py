from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from typing import Optional
import os

from app.auth.dependencies import get_current_user, _hash_token
from app.auth.jwt_handler import create_token
from app.config import JWT_EXPIRY_HOURS
from app.database import get_auth_db, get_track_db
from app.services.audit import write_audit_log, verify_audit_chain

import bcrypt
from datetime import datetime, timedelta, timezone

router = APIRouter()
_tmpl_dir = os.path.join(os.path.dirname(__file__), "..", "templates")
templates = Jinja2Templates(directory=os.path.abspath(_tmpl_dir))


def _ctx(request: Request, current_user: dict, **extra):
    """Build a base template context dict."""
    return {"request": request, "current_user": current_user, **extra}


# ── Auth ───────────────────────────────────────────────────────────────────────

@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("auth/login.html", {"request": request})


@router.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db=Depends(get_auth_db),
):
    ip = request.client.host if request.client else "unknown"
    db.execute(
        "SELECT user_id, username, password_hash, role, member_id, is_active "
        "FROM users WHERE username = %s",
        (username,),
    )
    user = db.fetchone()

    if not user or not user["is_active"] or \
       not bcrypt.checkpw(password.encode(), user["password_hash"].encode()):
        write_audit_log(db, None, username, "LOGIN", "users", None,
                        "FAILURE", {"reason": "bad credentials"}, ip)
        return templates.TemplateResponse(
            "auth/login.html",
            {"request": request, "error": "Invalid username or password", "username": username},
        )

    token = create_token(user["user_id"], user["username"], user["role"], user["member_id"])
    expires_at = datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRY_HOURS)
    db.execute(
        "INSERT INTO sessions (user_id, token_hash, expires_at) VALUES (%s, %s, %s)",
        (user["user_id"], _hash_token(token), expires_at),
    )
    write_audit_log(db, user["user_id"], user["username"], "LOGIN", "sessions",
                    str(user["user_id"]), "SUCCESS", None, ip)

    resp = RedirectResponse(url="/ui/dashboard", status_code=303)
    resp.set_cookie("access_token", token, httponly=True,
                    max_age=JWT_EXPIRY_HOURS * 3600, samesite="lax")
    return resp


# ── Dashboard ──────────────────────────────────────────────────────────────────

@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(
    request: Request,
    current_user: dict = Depends(get_current_user),
    track_db=Depends(get_track_db),
):
    track_db.execute("SELECT COUNT(*) AS c FROM Member")
    members = track_db.fetchone()["c"]
    track_db.execute("SELECT Role, COUNT(*) AS c FROM Member GROUP BY Role")
    roles = {r["Role"]: r["c"] for r in track_db.fetchall()}

    track_db.execute("SELECT COUNT(*) AS c FROM Team")
    teams = track_db.fetchone()["c"]
    track_db.execute("SELECT COUNT(DISTINCT SportID) AS c FROM Team")
    sports = track_db.fetchone()["c"]

    track_db.execute("SELECT COUNT(*) AS c FROM Event WHERE Status='Scheduled'")
    upcoming = track_db.fetchone()["c"]
    track_db.execute("SELECT COUNT(*) AS c FROM Event WHERE Status='Ongoing'")
    ongoing = track_db.fetchone()["c"]

    track_db.execute("SELECT COUNT(*) AS c FROM Equipment")
    equip = track_db.fetchone()["c"]
    track_db.execute("SELECT COUNT(*) AS c FROM EquipmentIssue WHERE ReturnDate IS NULL")
    unreturned = track_db.fetchone()["c"]

    track_db.execute(
        """
        SELECT e.EventID, e.EventName, e.EventDate, e.Status, v.VenueName
        FROM Event e JOIN Venue v ON e.VenueID = v.VenueID
        WHERE e.Status IN ('Scheduled','Ongoing')
        ORDER BY e.EventDate ASC LIMIT 8
        """
    )
    recent_events = track_db.fetchall()
    for ev in recent_events:
        ev["EventDate"] = str(ev["EventDate"])

    track_db.execute("SELECT TournamentName, Status, EndDate FROM Tournament ORDER BY StartDate DESC")
    tournaments = track_db.fetchall()
    for t in tournaments:
        t["EndDate"] = str(t["EndDate"])

    return templates.TemplateResponse("dashboard.html", _ctx(
        request, current_user,
        active="dashboard",
        stats={
            "members": members, "players": roles.get("Player", 0),
            "coaches": roles.get("Coach", 0), "admins": roles.get("Admin", 0),
            "teams": teams, "sports": sports,
            "upcoming_events": upcoming, "ongoing_events": ongoing,
            "equipment": equip, "unreturned": unreturned,
        },
        recent_events=recent_events,
        tournaments=tournaments,
    ))


# ── Members ────────────────────────────────────────────────────────────────────

@router.get("/members", response_class=HTMLResponse)
def members_list(
    request: Request,
    current_user: dict = Depends(get_current_user),
    track_db=Depends(get_track_db),
):
    if current_user["role"] == "Player":
        track_db.execute("SELECT * FROM Member WHERE MemberID = %s",
                         (current_user["member_id"],))
    else:
        track_db.execute("SELECT * FROM Member ORDER BY MemberID")
    members = track_db.fetchall()
    for m in members:
        m["JoinDate"] = str(m["JoinDate"])
    return templates.TemplateResponse("members/list.html",
                                      _ctx(request, current_user, active="members", members=members))


@router.get("/members/new", response_class=HTMLResponse)
def member_new_form(
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    if current_user["role"] != "Admin":
        return RedirectResponse("/ui/members", status_code=303)
    return templates.TemplateResponse("members/form.html",
                                      _ctx(request, current_user, active="members", member=None, error=None))


@router.post("/members/new")
def member_create(
    request: Request,
    current_user: dict = Depends(get_current_user),
    track_db=Depends(get_track_db),
    auth_db=Depends(get_auth_db),
    member_id: int = Form(...),
    name: str = Form(...),
    email: str = Form(...),
    age: int = Form(...),
    contact_number: str = Form(...),
    gender: str = Form(...),
    role: str = Form(...),
    join_date: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
):
    ip = request.client.host if request.client else "unknown"
    try:
        track_db.execute(
            "INSERT INTO Member (MemberID,Name,Age,Email,ContactNumber,Gender,Role,JoinDate) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (member_id, name, age, email, contact_number, gender, role, join_date),
        )
        pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        auth_db.execute(
            "INSERT INTO users (username, password_hash, role, member_id) VALUES (%s,%s,%s,%s)",
            (username, pw_hash, role, member_id),
        )
        write_audit_log(auth_db, current_user["user_id"], current_user["username"],
                        "INSERT", "Member", str(member_id), "SUCCESS", {"name": name}, ip)
    except Exception as e:
        return templates.TemplateResponse("members/form.html",
                                          _ctx(request, current_user, active="members",
                                               member=None, error=str(e)))
    return RedirectResponse(f"/ui/members/{member_id}", status_code=303)


@router.get("/members/{member_id}", response_class=HTMLResponse)
def member_portfolio(
    member_id: int,
    request: Request,
    current_user: dict = Depends(get_current_user),
    track_db=Depends(get_track_db),
    auth_db=Depends(get_auth_db),
):
    ip = request.client.host if request.client else "unknown"
    if current_user["role"] == "Player" and current_user["member_id"] != member_id:
        return RedirectResponse(f"/ui/members/{current_user['member_id']}", status_code=303)

    track_db.execute("SELECT * FROM Member WHERE MemberID = %s", (member_id,))
    member = track_db.fetchone()
    if not member:
        return RedirectResponse("/ui/members", status_code=303)
    member["JoinDate"] = str(member["JoinDate"])

    track_db.execute(
        """
        SELECT tm.*, t.TeamName, t.TeamID, s.SportName
        FROM TeamMember tm JOIN Team t ON tm.TeamID=t.TeamID JOIN Sport s ON t.SportID=s.SportID
        WHERE tm.MemberID=%s
        """, (member_id,))
    teams = track_db.fetchall()
    for t in teams:
        t["JoinDate"] = str(t["JoinDate"])

    if current_user["role"] == "Admin":
        track_db.execute(
            "SELECT pl.*, s.SportName FROM PerformanceLog pl JOIN Sport s ON pl.SportID=s.SportID "
            "WHERE pl.MemberID=%s ORDER BY pl.RecordDate DESC", (member_id,))
    elif current_user["role"] == "Coach":
        track_db.execute(
            """
            SELECT pl.*, s.SportName FROM PerformanceLog pl JOIN Sport s ON pl.SportID=s.SportID
            WHERE pl.MemberID=%s AND EXISTS (
                SELECT 1 FROM TeamMember tm JOIN Team t ON tm.TeamID=t.TeamID
                WHERE tm.MemberID=%s AND t.CoachID=%s)
            ORDER BY pl.RecordDate DESC
            """, (member_id, member_id, current_user["member_id"]))
    elif current_user["member_id"] == member_id:
        track_db.execute(
            "SELECT pl.*, s.SportName FROM PerformanceLog pl JOIN Sport s ON pl.SportID=s.SportID "
            "WHERE pl.MemberID=%s ORDER BY pl.RecordDate DESC", (member_id,))
    else:
        track_db.execute("SELECT 1 WHERE FALSE")
    performance = track_db.fetchall()
    for p in performance:
        p["RecordDate"] = str(p["RecordDate"])

    medical = []
    if current_user["role"] == "Admin" or current_user["member_id"] == member_id:
        track_db.execute(
            "SELECT * FROM MedicalRecord WHERE MemberID=%s ORDER BY DiagnosisDate DESC", (member_id,))
        medical = track_db.fetchall()
        for m2 in medical:
            m2["DiagnosisDate"] = str(m2["DiagnosisDate"])
            if m2.get("RecoveryDate"):
                m2["RecoveryDate"] = str(m2["RecoveryDate"])

    write_audit_log(auth_db, current_user["user_id"], current_user["username"],
                    "SELECT", "Member", str(member_id), "SUCCESS", None, ip)

    return templates.TemplateResponse("members/portfolio.html", _ctx(
        request, current_user, active="members",
        member=member, teams=teams, performance=performance, medical=medical,
    ))


@router.get("/members/{member_id}/edit", response_class=HTMLResponse)
def member_edit_form(
    member_id: int,
    request: Request,
    current_user: dict = Depends(get_current_user),
    track_db=Depends(get_track_db),
):
    if current_user["role"] != "Admin" and current_user["member_id"] != member_id:
        return RedirectResponse(f"/ui/members/{member_id}", status_code=303)
    track_db.execute("SELECT * FROM Member WHERE MemberID=%s", (member_id,))
    member = track_db.fetchone()
    if not member:
        return RedirectResponse("/ui/members", status_code=303)
    return templates.TemplateResponse("members/form.html",
                                      _ctx(request, current_user, active="members",
                                           member=member, error=None))


@router.post("/members/{member_id}/edit")
def member_edit_submit(
    member_id: int,
    request: Request,
    current_user: dict = Depends(get_current_user),
    track_db=Depends(get_track_db),
    auth_db=Depends(get_auth_db),
    name: str = Form(...),
    email: str = Form(...),
    age: int = Form(...),
    contact_number: str = Form(...),
):
    ip = request.client.host if request.client else "unknown"
    track_db.execute(
        "UPDATE Member SET Name=%s, Email=%s, Age=%s, ContactNumber=%s WHERE MemberID=%s",
        (name, email, age, contact_number, member_id),
    )
    write_audit_log(auth_db, current_user["user_id"], current_user["username"],
                    "UPDATE", "Member", str(member_id), "SUCCESS",
                    {"name": name, "email": email}, ip)
    return RedirectResponse(f"/ui/members/{member_id}", status_code=303)


@router.post("/members/{member_id}/delete")
def member_delete(
    member_id: int,
    request: Request,
    current_user: dict = Depends(get_current_user),
    track_db=Depends(get_track_db),
    auth_db=Depends(get_auth_db),
):
    ip = request.client.host if request.client else "unknown"
    if current_user["role"] != "Admin":
        return RedirectResponse("/ui/members", status_code=303)
    auth_db.execute("UPDATE users SET member_id=NULL WHERE member_id=%s", (member_id,))
    track_db.execute("DELETE FROM Member WHERE MemberID=%s", (member_id,))
    write_audit_log(auth_db, current_user["user_id"], current_user["username"],
                    "DELETE", "Member", str(member_id), "SUCCESS", None, ip)
    return RedirectResponse("/ui/members", status_code=303)


# ── Teams ──────────────────────────────────────────────────────────────────────

@router.get("/teams", response_class=HTMLResponse)
def teams_list(
    request: Request,
    current_user: dict = Depends(get_current_user),
    track_db=Depends(get_track_db),
):
    track_db.execute(
        "SELECT t.*, s.SportName, m.Name AS CoachName FROM Team t "
        "JOIN Sport s ON t.SportID=s.SportID LEFT JOIN Member m ON t.CoachID=m.MemberID "
        "ORDER BY t.TeamID"
    )
    teams = track_db.fetchall()
    for t in teams:
        t["FormedDate"] = str(t["FormedDate"])
    return templates.TemplateResponse("teams/list.html",
                                      _ctx(request, current_user, active="teams", teams=teams))


@router.get("/teams/{team_id}", response_class=HTMLResponse)
def team_detail(
    team_id: int,
    request: Request,
    current_user: dict = Depends(get_current_user),
    track_db=Depends(get_track_db),
):
    track_db.execute(
        "SELECT t.*, s.SportName, m.Name AS CoachName FROM Team t "
        "JOIN Sport s ON t.SportID=s.SportID LEFT JOIN Member m ON t.CoachID=m.MemberID "
        "WHERE t.TeamID=%s", (team_id,))
    team = track_db.fetchone()
    if not team:
        return RedirectResponse("/ui/teams", status_code=303)
    team["FormedDate"] = str(team["FormedDate"])

    track_db.execute(
        "SELECT tm.*, m.Name, m.Role, m.Email FROM TeamMember tm "
        "JOIN Member m ON tm.MemberID=m.MemberID WHERE tm.TeamID=%s", (team_id,))
    roster = track_db.fetchall()
    for r in roster:
        r["JoinDate"] = str(r["JoinDate"])

    return templates.TemplateResponse("teams/detail.html",
                                      _ctx(request, current_user, active="teams",
                                           team=team, roster=roster))


# ── Events ─────────────────────────────────────────────────────────────────────

@router.get("/events", response_class=HTMLResponse)
def events_list(
    request: Request,
    current_user: dict = Depends(get_current_user),
    track_db=Depends(get_track_db),
):
    track_db.execute(
        "SELECT e.*, s.SportName, v.VenueName, t.TournamentName FROM Event e "
        "JOIN Sport s ON e.SportID=s.SportID JOIN Venue v ON e.VenueID=v.VenueID "
        "LEFT JOIN Tournament t ON e.TournamentID=t.TournamentID ORDER BY e.EventDate DESC"
    )
    events = track_db.fetchall()
    for ev in events:
        ev["EventDate"]  = str(ev["EventDate"])
        ev["StartTime"]  = str(ev["StartTime"])
        ev["EndTime"]    = str(ev["EndTime"])
    return templates.TemplateResponse("events/list.html",
                                      _ctx(request, current_user, active="events", events=events))


@router.get("/events/{event_id}", response_class=HTMLResponse)
def event_detail(
    event_id: int,
    request: Request,
    current_user: dict = Depends(get_current_user),
    track_db=Depends(get_track_db),
):
    track_db.execute(
        "SELECT e.*, s.SportName, v.VenueName, t.TournamentName FROM Event e "
        "JOIN Sport s ON e.SportID=s.SportID JOIN Venue v ON e.VenueID=v.VenueID "
        "LEFT JOIN Tournament t ON e.TournamentID=t.TournamentID WHERE e.EventID=%s", (event_id,))
    event = track_db.fetchone()
    if not event:
        return RedirectResponse("/ui/events", status_code=303)
    for k in ("EventDate", "StartTime", "EndTime"):
        event[k] = str(event[k])

    track_db.execute(
        "SELECT p.*, tm.TeamName FROM Participation p JOIN Team tm ON p.TeamID=tm.TeamID "
        "WHERE p.EventID=%s", (event_id,))
    participation = track_db.fetchall()
    return templates.TemplateResponse("events/detail.html",
                                      _ctx(request, current_user, active="events",
                                           event=event, participation=participation))


# ── Equipment ──────────────────────────────────────────────────────────────────

@router.get("/equipment", response_class=HTMLResponse)
def equipment_list(
    request: Request,
    current_user: dict = Depends(get_current_user),
    track_db=Depends(get_track_db),
):
    track_db.execute(
        "SELECT e.*, s.SportName FROM Equipment e LEFT JOIN Sport s ON e.SportID=s.SportID "
        "ORDER BY e.EquipmentID"
    )
    equipment = track_db.fetchall()

    track_db.execute(
        "SELECT ei.*, e.EquipmentName, m.Name AS MemberName FROM EquipmentIssue ei "
        "JOIN Equipment e ON ei.EquipmentID=e.EquipmentID "
        "JOIN Member m ON ei.MemberID=m.MemberID WHERE ei.ReturnDate IS NULL"
    )
    issues = track_db.fetchall()
    for i in issues:
        i["IssueDate"] = str(i["IssueDate"])

    return templates.TemplateResponse("equipment/list.html",
                                      _ctx(request, current_user, active="equipment",
                                           equipment=equipment, issues=issues))


# ── Admin audit ────────────────────────────────────────────────────────────────

@router.get("/admin/audit", response_class=HTMLResponse)
def audit_page(
    request: Request,
    current_user: dict = Depends(get_current_user),
    auth_db=Depends(get_auth_db),
):
    if current_user["role"] != "Admin":
        return RedirectResponse("/ui/dashboard", status_code=303)
    auth_db.execute("SELECT * FROM audit_log ORDER BY log_id DESC LIMIT 100")
    logs = auth_db.fetchall()
    for log in logs:
        log["timestamp"] = str(log["timestamp"])
    return templates.TemplateResponse("admin/audit.html",
                                      _ctx(request, current_user, active="audit", logs=logs))


# ── Root redirect ──────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
def root():
    return RedirectResponse("/ui/dashboard", status_code=303)
