from fastapi import APIRouter, Depends, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from typing import Optional
from urllib.parse import quote_plus
import os

from app.auth.dependencies import get_current_user, _hash_token
from app.auth.jwt_handler import create_token
from app.config import JWT_EXPIRY_HOURS
from app.database import get_auth_db, get_track_db
from app.services.audit import write_audit_log, verify_audit_chain
from app.routers.members import MemberCreate, MemberUpdate
from app.routers.members import list_members, get_member_portfolio, create_member, update_member, delete_member
from app.routers.teams import TeamCreate, TeamUpdate
from app.routers.teams import list_teams, get_team, create_team, update_team, delete_team

import bcrypt
from datetime import datetime, timedelta, timezone

# Creates an APIRouter to group routes in this module (so main app can include_router(...))
router = APIRouter()
_tmpl_dir = os.path.join(os.path.dirname(__file__), "..", "templates")
templates = Jinja2Templates(directory=os.path.abspath(_tmpl_dir))

# Builds a dictionary used as the context for rendering templates (the second argument to TemplateResponse)
def _ctx(request: Request, current_user: dict, **extra):
    flash = None
    success_msg = request.query_params.get("success")
    error_msg = request.query_params.get("error")
    if success_msg:
        flash = {"type": "success", "message": success_msg}
    elif error_msg:
        flash = {"type": "danger", "message": error_msg}
    return {"request": request, "current_user": current_user, "flash": flash, **extra}

# Encodes a short flash message into the redirect URL as a query parameter, e.g. "/ui/login?success=Logged+in"
def _flash_redirect(url: str, error: str = None, success: str = None) -> RedirectResponse:
    msg = error or success
    key = "error" if error else "success"
    return RedirectResponse(f"{url}?{key}={quote_plus(msg)}", status_code=303)


def _parse_required_int(value, label: str) -> int:
    value = (value or "").strip()
    if not value:
        raise ValueError(f"{label} is required.")
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{label} must be a valid integer.") from exc


def _parse_optional_int(value, label: str) -> Optional[int]:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{label} must be a valid integer.") from exc


def _parse_member_ids(form) -> list[int]:
    member_ids = []
    for raw_value in form.getlist("member_ids"):
        value = raw_value.strip()
        if not value:
            continue
        try:
            member_ids.append(int(value))
        except ValueError as exc:
            raise ValueError("Each member ID must be a valid integer.") from exc
    return member_ids


# ── Auth ───────────────────────────────────────────────────────────────────────
# Load the login page and form
@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("auth/login.html", {"request": request})

# Handle login form submission, create session and JWT token if successful, and redirect to dashboard
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

    if not user or not user["is_active"] or not bcrypt.checkpw(password.encode(), user["password_hash"].encode()):
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

    track_db.execute("SELECT TournamentName, Status, EndDate FROM Tournament " \
            "WHERE Status = 'Upcoming' or Status = 'Ongoing' ORDER BY StartDate DESC")
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
    auth_db=Depends(get_auth_db)
):
    res = list_members(request, current_user, track_db, auth_db)
    if (res["success"]):
        return templates.TemplateResponse("members/list.html",
                                      _ctx(request, current_user, active="members", members=res["data"]))


@router.get("/members/new", response_class=HTMLResponse)
def member_new_form(
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    if current_user["role"] != "Admin":
        return RedirectResponse("/ui/members", status_code=303)
    return templates.TemplateResponse("members/form.html",
                                      _ctx(request, current_user, active="members",
                                           member=None, form_data={}, error=None))


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
    mem = MemberCreate(
        member_id=member_id,
        name=name,
        email=email,
        age=age,
        contact_number=contact_number,
        gender=gender,
        role=role,
        join_date=join_date,
        username=username,
        password=password,
    )

    res = create_member(mem, request, current_user, track_db, auth_db)
    if not res["success"]:
        form_data = {
            "MemberID": member_id,
            "Name": name,
            "Email": email,
            "Age": age,
            "ContactNumber": contact_number,
            "Gender": gender,
            "Role": role,
            "JoinDate": join_date,
            "Username": username,
        }
        return templates.TemplateResponse("members/form.html",
                                          _ctx(request, current_user, active="members",
                                               member=None, form_data=form_data, error=res["message"]))
    return RedirectResponse(f"/ui/members/{member_id}", status_code=303)


@router.get("/members/{member_id}", response_class=HTMLResponse)
def member_portfolio(
    member_id: int,
    request: Request,
    current_user: dict = Depends(get_current_user),
    track_db=Depends(get_track_db),
    auth_db=Depends(get_auth_db),
):
    try:
        res = get_member_portfolio(member_id, request, current_user, track_db, auth_db)
    except HTTPException:
        return RedirectResponse("/ui/members", status_code=303)

    portfolio = res["data"]
    member = portfolio["member"]
    teams = portfolio["teams"]
    performance = portfolio["performance"]
    medical = portfolio["medical"]

    member["JoinDate"] = str(member["JoinDate"])
    for p in performance:
        p["RecordDate"] = str(p["RecordDate"])
    for m2 in medical:
        m2["DiagnosisDate"] = str(m2["DiagnosisDate"])
        if m2.get("RecoveryDate"):
            m2["RecoveryDate"] = str(m2["RecoveryDate"])

    track_db.execute("SELECT SportID, SportName FROM Sport ORDER BY SportName")
    sports = track_db.fetchall()

    return templates.TemplateResponse("members/portfolio.html", _ctx(
        request, current_user, active="members",
        member=member, teams=teams, performance=performance, medical=medical,
        sports=sports,
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
                                           member=member, form_data={}, error=None))


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
    body = MemberUpdate(
        name=name,
        email=email,
        age=age,
        contact_number=contact_number,
    )
    try:
        update_member(member_id, body, request, current_user, track_db, auth_db)
    except HTTPException as exc:
        if exc.status_code == 403:
            return RedirectResponse(f"/ui/members/{member_id}", status_code=303)
        if exc.status_code == 404:
            return RedirectResponse("/ui/members", status_code=303)

        form_data = {
            "MemberID": member_id,
            "Name": name,
            "Email": email,
            "Age": age,
            "ContactNumber": contact_number,
        }
        track_db.execute("SELECT * FROM Member WHERE MemberID=%s", (member_id,))
        member = track_db.fetchone()
        return templates.TemplateResponse(
            "members/form.html",
            _ctx(
                request,
                current_user,
                active="members",
                member=member,
                form_data=form_data,
                error=exc.detail,
            ),
        )
    return RedirectResponse(f"/ui/members/{member_id}", status_code=303)


@router.post("/members/{member_id}/delete")
def member_delete(
    member_id: int,
    request: Request,
    current_user: dict = Depends(get_current_user),
    track_db=Depends(get_track_db),
    auth_db=Depends(get_auth_db),
):
    try:
        delete_member(member_id, request, current_user, track_db, auth_db)
    except HTTPException:
        return RedirectResponse("/ui/members", status_code=303)
    return RedirectResponse("/ui/members", status_code=303)


# ── Teams ──────────────────────────────────────────────────────────────────────

@router.get("/teams", response_class=HTMLResponse)
def teams_list(
    request: Request,
    current_user: dict = Depends(get_current_user),
    track_db=Depends(get_track_db),
    auth_db=Depends(get_auth_db),
):
    res = list_teams(request, current_user, track_db, auth_db)
    teams = res["data"]
    for t in teams:
        if t.get("FormedDate") is not None:
            t["FormedDate"] = str(t["FormedDate"])
    return templates.TemplateResponse("teams/list.html",
                                      _ctx(request, current_user, active="teams", teams=teams))


@router.get("/teams/new", response_class=HTMLResponse)
def team_new_form(
    request: Request,
    current_user: dict = Depends(get_current_user),
    track_db=Depends(get_track_db),
):
    if current_user["role"] not in ("Admin", "Coach"):
        return RedirectResponse("/ui/teams", status_code=303)
    return templates.TemplateResponse("teams/form.html",
                                      _ctx(request, current_user, form_data={"member_ids": [""]}, active="teams", error=None))


@router.post("/teams/new")
async def team_create(
    request: Request,
    current_user: dict = Depends(get_current_user),
    track_db=Depends(get_track_db),
    auth_db=Depends(get_auth_db),
):
    if current_user["role"] not in ("Admin", "Coach"):
        return RedirectResponse("/ui/teams", status_code=303)

    form = await request.form()
    team_name = (form.get("team_name") or "").strip()
    formed_date = (form.get("formed_date") or "").strip()
    raw_form_data = {
        "team_name": team_name,
        "sport_id": form.get("sport_id", ""),
        "coach_id": form.get("coach_id", ""),
        "captain_id": form.get("captain_id", ""),
        "member_ids": form.getlist("member_ids") or [""],
        "formed_date": formed_date,
    }

    try:
        sport_id = _parse_required_int(form.get("sport_id"), "Sport ID")
        coach_id = _parse_optional_int(form.get("coach_id"), "Coach ID")
        captain_id = _parse_optional_int(form.get("captain_id"), "Captain ID")
        member_ids = _parse_member_ids(form)
    except ValueError as exc:
        return templates.TemplateResponse(
            "teams/form.html",
            _ctx(request, current_user, active="teams", form_data=raw_form_data, error=str(exc)),
        )

    body = TeamCreate(
        team_name=team_name,
        sport_id=sport_id,
        coach_id=coach_id,
        captain_id=captain_id,
        member_ids=member_ids,
        formed_date=formed_date,
    )
    try:   
        res = create_team(body, request, current_user, track_db, auth_db)
    except HTTPException as exc:
        return templates.TemplateResponse("teams/form.html",
                                          _ctx(request, current_user, active="teams", form_data=raw_form_data, error=exc.detail))
    return RedirectResponse(f"/ui/teams/{res['data']['team_id']}", status_code=303)


@router.get("/teams/{team_id}/edit", response_class=HTMLResponse)
def team_edit_form(
    team_id: int,
    request: Request,
    current_user: dict = Depends(get_current_user),
    track_db=Depends(get_track_db),
    auth_db=Depends(get_auth_db),
):
    if current_user["role"] not in ("Admin", "Coach"):
        return RedirectResponse(f"/ui/teams/{team_id}", status_code=303)

    try:
        res = get_team(team_id, request, current_user, track_db, auth_db)
    except HTTPException:
        return RedirectResponse("/ui/teams", status_code=303)

    team = res["data"]["team"]
    if team["CoachID"] != current_user["member_id"] and current_user["role"] != "Admin":
        return RedirectResponse(f"/ui/teams/{team_id}", status_code=303)
    if team.get("FormedDate") is not None:
        team["FormedDate"] = str(team["FormedDate"])
    form_data = {
        "team_name": team["TeamName"],
        "sport_id": team["SportID"],
        "coach_id": team["CoachID"],
        "captain_id": team["CaptainID"],
        "member_ids": [member["MemberID"] for member in res["data"]["roster"]] or [""],
        "formed_date": team["FormedDate"]
    }
    return templates.TemplateResponse("teams/form.html",
                                      _ctx(request, current_user, active="teams", team=team, form_data=form_data, error=None))


@router.post("/teams/{team_id}/edit")
async def team_edit_submit(
    team_id: int,
    request: Request,
    current_user: dict = Depends(get_current_user),
    track_db=Depends(get_track_db),
    auth_db=Depends(get_auth_db),
):
    if current_user["role"] not in ("Admin", "Coach"):
        return RedirectResponse(f"/ui/teams/{team_id}", status_code=303)

    form = await request.form()
    team_name = (form.get("team_name") or "").strip()
    formed_date = (form.get("formed_date") or "").strip()
    raw_form_data = {
        "team_name": team_name,
        "sport_id": form.get("sport_id", ""),
        "coach_id": form.get("coach_id", ""),
        "captain_id": form.get("captain_id", ""),
        "member_ids": form.getlist("member_ids") or [""],
        "formed_date": formed_date,
    }

    try:
        sport_id = _parse_required_int(form.get("sport_id"), "Sport ID")
        coach_id = _parse_optional_int(form.get("coach_id"), "Coach ID")
        captain_id = _parse_optional_int(form.get("captain_id"), "Captain ID")
        member_ids = _parse_member_ids(form)
    except ValueError as exc:
        team = {
            "TeamID": team_id,
            "TeamName": team_name,
            "SportID": raw_form_data["sport_id"],
            "CoachID": raw_form_data["coach_id"],
            "CaptainID": raw_form_data["captain_id"],
            "FormedDate": formed_date,
        }
        return templates.TemplateResponse(
            "teams/form.html",
            _ctx(request, current_user, active="teams", team=team, form_data=raw_form_data, error=str(exc)),
        )

    body = TeamUpdate(
        team_name=team_name,
        sport_id=sport_id,
        coach_id=coach_id,
        captain_id=captain_id,
        member_ids=member_ids,
        formed_date=formed_date,
    )
    try:   
        update_team(team_id, body, request, current_user, track_db, auth_db)
        return RedirectResponse(f"/ui/teams/{team_id}", status_code=303)
    except HTTPException as exc:
        if exc.status_code in (403, 404):
            return RedirectResponse(f"/ui/teams/{team_id}" if exc.status_code == 403 else "/ui/teams", status_code=303)

        team = {
            "TeamID": team_id,
            "TeamName": team_name,
            "SportID": raw_form_data["sport_id"],
            "CoachID": raw_form_data["coach_id"],
            "CaptainID": raw_form_data["captain_id"],
            "FormedDate": formed_date,
        }
        return templates.TemplateResponse("teams/form.html",
                                          _ctx(request, current_user, active="teams", team=team, form_data=raw_form_data, error=exc.detail))



@router.post("/teams/{team_id}/delete")
def team_delete(
    team_id: int,
    request: Request,
    current_user: dict = Depends(get_current_user),
    track_db=Depends(get_track_db),
    auth_db=Depends(get_auth_db),
):
    try:
        delete_team(team_id, request, current_user, track_db, auth_db)
    except HTTPException:
        return RedirectResponse(f"/ui/teams/{team_id}", status_code=303)
    return RedirectResponse("/ui/teams", status_code=303)


@router.get("/teams/{team_id}", response_class=HTMLResponse)
def team_detail(
    team_id: int,
    request: Request,
    current_user: dict = Depends(get_current_user),
    track_db=Depends(get_track_db),
    auth_db=Depends(get_auth_db),
):
    try:
        res = get_team(team_id, request, current_user, track_db, auth_db)
    except HTTPException:
        return RedirectResponse("/ui/teams", status_code=303)

    team = res["data"]["team"]
    roster = res["data"]["roster"]
    if team.get("FormedDate") is not None:
        team["FormedDate"] = str(team["FormedDate"])
    for r in roster:
        if r.get("JoinDate") is not None:
            r["JoinDate"] = str(r["JoinDate"])

    return templates.TemplateResponse("teams/detail.html",
                                      _ctx(request, current_user, active="teams",
                                           team=team, roster=roster))


# ── Tournaments ────────────────────────────────────────────────────────────────

@router.get("/tournaments", response_class=HTMLResponse)
def tournaments_list(
    request: Request,
    current_user: dict = Depends(get_current_user),
    track_db=Depends(get_track_db),
):
    track_db.execute("SELECT * FROM Tournament ORDER BY StartDate DESC")
    tournaments = track_db.fetchall()
    for t in tournaments:
        t["StartDate"] = str(t["StartDate"])
        t["EndDate"] = str(t["EndDate"])
    return templates.TemplateResponse("tournaments/list.html",
                                      _ctx(request, current_user, active="tournaments",
                                           tournaments=tournaments))


@router.get("/tournaments/new", response_class=HTMLResponse)

def tournament_new_form(
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    if current_user["role"] != "Admin":
        return RedirectResponse("/ui/tournaments", status_code=303)
    return templates.TemplateResponse("tournaments/form.html",
                                      _ctx(request, current_user, form_data={}, active="tournaments", error=None))


@router.post("/tournaments/new")
def tournament_create(
    request: Request,
    current_user: dict = Depends(get_current_user),
    track_db=Depends(get_track_db),
    auth_db=Depends(get_auth_db),
    tournament_name: str = Form(...),
    start_date: str = Form(...),
    end_date: str = Form(...),
    description: Optional[str] = Form(None),
    status: str = Form(...),
):
    if current_user["role"] != "Admin":
        return RedirectResponse("/ui/tournaments", status_code=303)
    ip = request.client.host if request.client else "unknown"
    track_db.execute("SELECT COALESCE(MAX(TournamentID), 0) + 1 AS nid FROM Tournament")
    next_id = track_db.fetchone()["nid"]
    try:
        track_db.execute(
            "INSERT INTO Tournament (TournamentID, TournamentName, StartDate, EndDate, Description, Status) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (next_id, tournament_name, start_date, end_date, description or None, status),
        )
        write_audit_log(auth_db, current_user["user_id"], current_user["username"],
                        "INSERT", "Tournament", str(next_id), "SUCCESS",
                        {"name": tournament_name}, ip)
    except Exception as e:
        form_data = {
            "tournament_name": tournament_name,
            "start_date": start_date,
            "end_date": end_date,
            "description": description,
            "status": status,
        }
        return templates.TemplateResponse("tournaments/form.html",
                                          _ctx(request, current_user, form_data=form_data, active="tournaments", error=str(e)))
    return RedirectResponse("/ui/tournaments", status_code=303)


@router.get("/tournaments/{tournament_id}", response_class=HTMLResponse)
def tournament_detail(
    tournament_id: int,
    request: Request,
    current_user: dict = Depends(get_current_user),
    track_db=Depends(get_track_db),
):
    track_db.execute("SELECT * FROM Tournament WHERE TournamentID=%s", (tournament_id,))
    tournament = track_db.fetchone()
    if not tournament:
        return RedirectResponse("/ui/tournaments", status_code=303)
    tournament["StartDate"] = str(tournament["StartDate"])
    tournament["EndDate"] = str(tournament["EndDate"])

    track_db.execute(
        "SELECT e.*, s.SportName, v.VenueName FROM Event e "
        "JOIN Sport s ON e.SportID=s.SportID JOIN Venue v ON e.VenueID=v.VenueID "
        "WHERE e.TournamentID=%s ORDER BY e.EventDate", (tournament_id,))
    events = track_db.fetchall()
    for ev in events:
        ev["EventDate"] = str(ev["EventDate"])
        ev["StartTime"] = str(ev["StartTime"])
        ev["EndTime"]   = str(ev["EndTime"])

    track_db.execute(
        "SELECT tr.RegID, t.TeamID, t.TeamName, s.SportName FROM TournamentRegistration tr "
        "JOIN Team t ON tr.TeamID=t.TeamID JOIN Sport s ON t.SportID=s.SportID "
        "WHERE tr.TournamentID=%s ORDER BY t.TeamName", (tournament_id,))
    registered_teams = track_db.fetchall()

    registered_ids = {r["TeamID"] for r in registered_teams}
    track_db.execute("SELECT TeamID, TeamName FROM Team ORDER BY TeamName")
    all_teams = [t for t in track_db.fetchall() if t["TeamID"] not in registered_ids]

    return templates.TemplateResponse("tournaments/detail.html",
                                      _ctx(request, current_user, active="tournaments",
                                           tournament=tournament, events=events,
                                           registered_teams=registered_teams, all_teams=all_teams))


@router.get("/tournaments/{tournament_id}/edit", response_class=HTMLResponse)
def tournament_edit_form(
    tournament_id: int,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    if current_user["role"] != "Admin":
        return RedirectResponse(f"/ui/tournaments/{tournament_id}", status_code=303)
    from app.database import get_track_db as _get_track_db
    # Use a direct import to avoid dependency injection in GET
    return RedirectResponse(f"/ui/tournaments/{tournament_id}/edit-form", status_code=303)


@router.get("/tournaments/{tournament_id}/edit-form", response_class=HTMLResponse)
def tournament_edit_page(
    tournament_id: int,
    request: Request,
    current_user: dict = Depends(get_current_user),
    track_db=Depends(get_track_db),
):
    if current_user["role"] != "Admin":
        return RedirectResponse(f"/ui/tournaments/{tournament_id}", status_code=303)
    track_db.execute("SELECT * FROM Tournament WHERE TournamentID=%s", (tournament_id,))
    tournament = track_db.fetchone()
    if not tournament:
        return RedirectResponse("/ui/tournaments", status_code=303)
    tournament["StartDate"] = str(tournament["StartDate"])
    tournament["EndDate"] = str(tournament["EndDate"])
    form_data = {
        "tournament_name": tournament["TournamentName"],
        "start_date": tournament["StartDate"],
        "end_date": tournament["EndDate"],
        "description": tournament["Description"],
        "status": tournament["Status"],
    }
    return templates.TemplateResponse("tournaments/form.html",
                                      _ctx(request, current_user, form_data=form_data,active="tournaments",
                                           tournament=tournament, error=None))


@router.post("/tournaments/{tournament_id}/edit")
def tournament_edit_submit(
    tournament_id: int,
    request: Request,
    current_user: dict = Depends(get_current_user),
    track_db=Depends(get_track_db),
    auth_db=Depends(get_auth_db),
    tournament_name: str = Form(...),
    start_date: str = Form(...),
    end_date: str = Form(...),
    description: Optional[str] = Form(None),
    status: str = Form(...),
):
    if current_user["role"] != "Admin":
        return RedirectResponse(f"/ui/tournaments/{tournament_id}", status_code=303)
    ip = request.client.host if request.client else "unknown"
    try:
        track_db.execute(
            "UPDATE Tournament SET TournamentName=%s, StartDate=%s, EndDate=%s, "
            "Description=%s, Status=%s WHERE TournamentID=%s",
            (tournament_name, start_date, end_date, description or None, status, tournament_id),
        )
        write_audit_log(auth_db, current_user["user_id"], current_user["username"],
                        "UPDATE", "Tournament", str(tournament_id), "SUCCESS",
                        {"name": tournament_name}, ip)
    except Exception as e:
        return _flash_redirect(f"/ui/tournaments/{tournament_id}/edit-form", error=str(e))
    return RedirectResponse(f"/ui/tournaments/{tournament_id}", status_code=303)


@router.post("/tournaments/{tournament_id}/delete")
def tournament_delete(
    tournament_id: int,
    request: Request,
    current_user: dict = Depends(get_current_user),
    track_db=Depends(get_track_db),
    auth_db=Depends(get_auth_db),
):
    if current_user["role"] != "Admin":
        return RedirectResponse("/ui/tournaments", status_code=303)
    ip = request.client.host if request.client else "unknown"
    track_db.execute("DELETE FROM Tournament WHERE TournamentID=%s", (tournament_id,))
    write_audit_log(auth_db, current_user["user_id"], current_user["username"],
                    "DELETE", "Tournament", str(tournament_id), "SUCCESS", None, ip)
    return _flash_redirect("/ui/tournaments", success="Tournament deleted.")


@router.post("/tournaments/{tournament_id}/register")
def tournament_register_team(
    tournament_id: int,
    request: Request,
    current_user: dict = Depends(get_current_user),
    track_db=Depends(get_track_db),
    auth_db=Depends(get_auth_db),
    team_id: int = Form(...),
):
    if current_user["role"] not in ("Admin", "Coach"):
        return RedirectResponse(f"/ui/tournaments/{tournament_id}", status_code=303)
    ip = request.client.host if request.client else "unknown"
    try:
        track_db.execute(
            "SELECT RegID FROM TournamentRegistration WHERE TournamentID=%s AND TeamID=%s",
            (tournament_id, team_id))
        if track_db.fetchone():
            return _flash_redirect(f"/ui/tournaments/{tournament_id}",
                                   error="Team is already registered.")
        track_db.execute(
            "SELECT COALESCE(MAX(RegID), 0) + 1 AS nid FROM TournamentRegistration")
        next_id = track_db.fetchone()["nid"]
        track_db.execute(
            "INSERT INTO TournamentRegistration (RegID, TournamentID, TeamID) VALUES (%s,%s,%s)",
            (next_id, tournament_id, team_id))
        write_audit_log(auth_db, current_user["user_id"], current_user["username"],
                        "INSERT", "TournamentRegistration", str(next_id), "SUCCESS",
                        {"tournament_id": tournament_id, "team_id": team_id}, ip)
    except Exception as e:
        return _flash_redirect(f"/ui/tournaments/{tournament_id}", error=str(e))
    return _flash_redirect(f"/ui/tournaments/{tournament_id}", success="Team registered.")


@router.post("/tournaments/{tournament_id}/unregister/{team_id}")
def tournament_unregister_team(
    tournament_id: int,
    team_id: int,
    request: Request,
    current_user: dict = Depends(get_current_user),
    track_db=Depends(get_track_db),
    auth_db=Depends(get_auth_db),
):
    if current_user["role"] != "Admin":
        return RedirectResponse(f"/ui/tournaments/{tournament_id}", status_code=303)
    ip = request.client.host if request.client else "unknown"
    track_db.execute(
        "DELETE FROM TournamentRegistration WHERE TournamentID=%s AND TeamID=%s",
        (tournament_id, team_id))
    write_audit_log(auth_db, current_user["user_id"], current_user["username"],
                    "DELETE", "TournamentRegistration", None, "SUCCESS",
                    {"tournament_id": tournament_id, "team_id": team_id}, ip)
    return _flash_redirect(f"/ui/tournaments/{tournament_id}", success="Team unregistered.")


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


@router.get("/events/new", response_class=HTMLResponse)
def event_new_form(
    request: Request,
    current_user: dict = Depends(get_current_user),
    track_db=Depends(get_track_db),
):
    if current_user["role"] != "Admin":
        return RedirectResponse("/ui/events", status_code=303)
    track_db.execute("SELECT SportID, SportName FROM Sport ORDER BY SportName")
    sports = track_db.fetchall()
    track_db.execute("SELECT VenueID, VenueName FROM Venue ORDER BY VenueName")
    venues = track_db.fetchall()
    track_db.execute("SELECT TournamentID, TournamentName FROM Tournament ORDER BY TournamentName")
    tournaments = track_db.fetchall()
    return templates.TemplateResponse("events/form.html",
                                      _ctx(request, current_user, active="events",
                                           sports=sports, venues=venues, tournaments=tournaments, error=None))


@router.post("/events/new")
def event_create(
    request: Request,
    current_user: dict = Depends(get_current_user),
    track_db=Depends(get_track_db),
    auth_db=Depends(get_auth_db),
    event_name: str = Form(...),
    tournament_id: Optional[int] = Form(None),
    event_date: str = Form(...),
    start_time: str = Form(...),
    end_time: str = Form(...),
    venue_id: int = Form(...),
    sport_id: int = Form(...),
    status: str = Form(...),
    round_name: Optional[str] = Form(None),
):
    if current_user["role"] != "Admin":
        return RedirectResponse("/ui/events", status_code=303)
    ip = request.client.host if request.client else "unknown"
    track_db.execute("SELECT COALESCE(MAX(EventID), 0) + 1 AS nid FROM Event")
    next_id = track_db.fetchone()["nid"]
    try:
        track_db.execute(
            "INSERT INTO Event (EventID, EventName, TournamentID, EventDate, StartTime, EndTime, "
            "VenueID, SportID, Status, Round) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (next_id, event_name, tournament_id, event_date, start_time, end_time,
             venue_id, sport_id, status, round_name or None),
        )
        write_audit_log(auth_db, current_user["user_id"], current_user["username"],
                        "INSERT", "Event", str(next_id), "SUCCESS", {"name": event_name}, ip)
    except Exception as e:
        track_db.execute("SELECT SportID, SportName FROM Sport ORDER BY SportName")
        sports = track_db.fetchall()
        track_db.execute("SELECT VenueID, VenueName FROM Venue ORDER BY VenueName")
        venues = track_db.fetchall()
        track_db.execute("SELECT TournamentID, TournamentName FROM Tournament ORDER BY TournamentName")
        tournaments = track_db.fetchall()
        return templates.TemplateResponse("events/form.html",
                                          _ctx(request, current_user, active="events",
                                               sports=sports, venues=venues,
                                               tournaments=tournaments, error=str(e)))
    return RedirectResponse(f"/ui/events/{next_id}", status_code=303)


@router.get("/events/{event_id}/edit", response_class=HTMLResponse)
def event_edit_form(
    event_id: int,
    request: Request,
    current_user: dict = Depends(get_current_user),
    track_db=Depends(get_track_db),
):
    if current_user["role"] != "Admin":
        return RedirectResponse(f"/ui/events/{event_id}", status_code=303)
    track_db.execute("SELECT * FROM Event WHERE EventID=%s", (event_id,))
    event = track_db.fetchone()
    if not event:
        return RedirectResponse("/ui/events", status_code=303)
    for k in ("EventDate", "StartTime", "EndTime"):
        event[k] = str(event[k])
    track_db.execute("SELECT SportID, SportName FROM Sport ORDER BY SportName")
    sports = track_db.fetchall()
    track_db.execute("SELECT VenueID, VenueName FROM Venue ORDER BY VenueName")
    venues = track_db.fetchall()
    track_db.execute("SELECT TournamentID, TournamentName FROM Tournament ORDER BY TournamentName")
    tournaments = track_db.fetchall()
    return templates.TemplateResponse("events/form.html",
                                      _ctx(request, current_user, active="events",
                                           event=event, sports=sports, venues=venues,
                                           tournaments=tournaments, error=None))


@router.post("/events/{event_id}/edit")
def event_edit_submit(
    event_id: int,
    request: Request,
    current_user: dict = Depends(get_current_user),
    track_db=Depends(get_track_db),
    auth_db=Depends(get_auth_db),
    event_name: str = Form(...),
    tournament_id: Optional[int] = Form(None),
    event_date: str = Form(...),
    start_time: str = Form(...),
    end_time: str = Form(...),
    venue_id: int = Form(...),
    sport_id: int = Form(...),
    status: str = Form(...),
    round_name: Optional[str] = Form(None),
):
    if current_user["role"] != "Admin":
        return RedirectResponse(f"/ui/events/{event_id}", status_code=303)
    ip = request.client.host if request.client else "unknown"
    try:
        track_db.execute(
            "UPDATE Event SET EventName=%s, TournamentID=%s, EventDate=%s, StartTime=%s, "
            "EndTime=%s, VenueID=%s, SportID=%s, Status=%s, Round=%s WHERE EventID=%s",
            (event_name, tournament_id, event_date, start_time, end_time,
             venue_id, sport_id, status, round_name or None, event_id),
        )
        write_audit_log(auth_db, current_user["user_id"], current_user["username"],
                        "UPDATE", "Event", str(event_id), "SUCCESS", {"name": event_name}, ip)
    except Exception as e:
        return _flash_redirect(f"/ui/events/{event_id}/edit", error=str(e))
    return RedirectResponse(f"/ui/events/{event_id}", status_code=303)


@router.post("/events/{event_id}/delete")
def event_delete(
    event_id: int,
    request: Request,
    current_user: dict = Depends(get_current_user),
    track_db=Depends(get_track_db),
    auth_db=Depends(get_auth_db),
):
    if current_user["role"] != "Admin":
        return RedirectResponse("/ui/events", status_code=303)
    ip = request.client.host if request.client else "unknown"
    track_db.execute("DELETE FROM Event WHERE EventID=%s", (event_id,))
    write_audit_log(auth_db, current_user["user_id"], current_user["username"],
                    "DELETE", "Event", str(event_id), "SUCCESS", None, ip)
    return _flash_redirect("/ui/events", success="Event deleted.")


@router.post("/events/{event_id}/add-team")
def event_add_team(
    event_id: int,
    request: Request,
    current_user: dict = Depends(get_current_user),
    track_db=Depends(get_track_db),
    auth_db=Depends(get_auth_db),
    team_id: int = Form(...),
):
    if current_user["role"] != "Admin":
        return RedirectResponse(f"/ui/events/{event_id}", status_code=303)
    ip = request.client.host if request.client else "unknown"
    track_db.execute("SELECT TournamentID FROM Event WHERE EventID=%s", (event_id,))
    ev = track_db.fetchone()
    if not ev:
        return _flash_redirect("/ui/events", error="Event not found.")

    if ev["TournamentID"]:
        track_db.execute(
            "SELECT RegID FROM TournamentRegistration WHERE TournamentID=%s AND TeamID=%s",
            (ev["TournamentID"], team_id))
        if not track_db.fetchone():
            return _flash_redirect(
                f"/ui/events/{event_id}",
                error="Team must be registered for the tournament before joining its events.")

    track_db.execute(
        "SELECT ParticipationID FROM Participation WHERE EventID=%s AND TeamID=%s",
        (event_id, team_id))
    if track_db.fetchone():
        return _flash_redirect(f"/ui/events/{event_id}",
                                error="Team is already participating in this event.")

    track_db.execute("SELECT COALESCE(MAX(ParticipationID), 0) + 1 AS nid FROM Participation")
    next_id = track_db.fetchone()["nid"]
    track_db.execute(
        "INSERT INTO Participation (ParticipationID, TeamID, EventID) VALUES (%s,%s,%s)",
        (next_id, team_id, event_id))
    write_audit_log(auth_db, current_user["user_id"], current_user["username"],
                    "INSERT", "Participation", str(next_id), "SUCCESS",
                    {"event_id": event_id, "team_id": team_id}, ip)
    return _flash_redirect(f"/ui/events/{event_id}", success="Team added to event.")


@router.post("/events/{event_id}/remove-team/{team_id}")
def event_remove_team(
    event_id: int,
    team_id: int,
    request: Request,
    current_user: dict = Depends(get_current_user),
    track_db=Depends(get_track_db),
    auth_db=Depends(get_auth_db),
):
    if current_user["role"] != "Admin":
        return RedirectResponse(f"/ui/events/{event_id}", status_code=303)
    ip = request.client.host if request.client else "unknown"
    track_db.execute(
        "DELETE FROM Participation WHERE EventID=%s AND TeamID=%s", (event_id, team_id))
    write_audit_log(auth_db, current_user["user_id"], current_user["username"],
                    "DELETE", "Participation", None, "SUCCESS",
                    {"event_id": event_id, "team_id": team_id}, ip)
    return _flash_redirect(f"/ui/events/{event_id}", success="Team removed from event.")


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

    # For "add team" form: show eligible teams (registered for tournament if applicable, not yet in event)
    participating_ids = {p["TeamID"] for p in participation}
    if event.get("TournamentID"):
        track_db.execute(
            "SELECT t.TeamID, t.TeamName FROM TournamentRegistration tr "
            "JOIN Team t ON tr.TeamID=t.TeamID WHERE tr.TournamentID=%s ORDER BY t.TeamName",
            (event["TournamentID"],))
    else:
        track_db.execute("SELECT TeamID, TeamName FROM Team ORDER BY TeamName")
    eligible_teams = [t for t in track_db.fetchall() if t["TeamID"] not in participating_ids]

    return templates.TemplateResponse("events/detail.html",
                                      _ctx(request, current_user, active="events",
                                           event=event, participation=participation,
                                           eligible_teams=eligible_teams))


# ── Equipment ──────────────────────────────────────────────────────────────────

@router.get("/equipment", response_class=HTMLResponse)
def equipment_list(
    request: Request,
    current_user: dict = Depends(get_current_user),
    track_db=Depends(get_track_db),
):
    track_db.execute(
        "SELECT e.*, s.SportName, "
        "e.TotalQuantity - COALESCE((SELECT SUM(ei.Quantity) FROM EquipmentIssue ei "
        "WHERE ei.EquipmentID=e.EquipmentID AND ei.ReturnDate IS NULL), 0) AS AvailableQuantity "
        "FROM Equipment e LEFT JOIN Sport s ON e.SportID=s.SportID ORDER BY e.EquipmentID"
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

    track_db.execute("SELECT MemberID, Name FROM Member ORDER BY Name")
    members = track_db.fetchall()

    return templates.TemplateResponse("equipment/list.html",
                                      _ctx(request, current_user, active="equipment",
                                           equipment=equipment, issues=issues, members=members))


@router.post("/equipment/issue")
def equipment_issue(
    request: Request,
    current_user: dict = Depends(get_current_user),
    track_db=Depends(get_track_db),
    auth_db=Depends(get_auth_db),
    equipment_id: int = Form(...),
    member_id: int = Form(...),
    issue_date: str = Form(...),
    quantity: int = Form(...),
):
    if current_user["role"] not in ("Admin", "Coach"):
        return RedirectResponse("/ui/equipment", status_code=303)
    ip = request.client.host if request.client else "unknown"
    if quantity <= 0:
        return _flash_redirect("/ui/equipment", error="Quantity must be a positive number.")
    track_db.execute(
        "SELECT e.TotalQuantity, COALESCE(SUM(ei.Quantity), 0) AS issued "
        "FROM Equipment e LEFT JOIN EquipmentIssue ei "
        "ON e.EquipmentID=ei.EquipmentID AND ei.ReturnDate IS NULL "
        "WHERE e.EquipmentID=%s GROUP BY e.TotalQuantity",
        (equipment_id,))
    stock = track_db.fetchone()
    if not stock:
        return _flash_redirect("/ui/equipment", error="Equipment not found.")
    available = stock["TotalQuantity"] - stock["issued"]
    if quantity > available:
        return _flash_redirect("/ui/equipment",
            error=f"Cannot issue {quantity} item(s). Only {available} available.")
    track_db.execute("SELECT COALESCE(MAX(IssueID), 0) + 1 AS nid FROM EquipmentIssue")
    next_id = track_db.fetchone()["nid"]
    track_db.execute(
        "INSERT INTO EquipmentIssue (IssueID, EquipmentID, MemberID, IssueDate, Quantity) "
        "VALUES (%s,%s,%s,%s,%s)",
        (next_id, equipment_id, member_id, issue_date, quantity),
    )
    write_audit_log(auth_db, current_user["user_id"], current_user["username"],
                    "INSERT", "EquipmentIssue", str(next_id), "SUCCESS",
                    {"equipment_id": equipment_id, "member_id": member_id}, ip)
    return _flash_redirect("/ui/equipment", success="Equipment issued successfully.")


@router.post("/equipment/issue/{issue_id}/return")
def equipment_return(
    issue_id: int,
    request: Request,
    current_user: dict = Depends(get_current_user),
    track_db=Depends(get_track_db),
    auth_db=Depends(get_auth_db),
    return_date: str = Form(...),
):
    if current_user["role"] not in ("Admin", "Coach"):
        return RedirectResponse("/ui/equipment", status_code=303)
    ip = request.client.host if request.client else "unknown"
    track_db.execute(
        "UPDATE EquipmentIssue SET ReturnDate=%s WHERE IssueID=%s",
        (return_date, issue_id),
    )
    write_audit_log(auth_db, current_user["user_id"], current_user["username"],
                    "UPDATE", "EquipmentIssue", str(issue_id), "SUCCESS",
                    {"return_date": return_date}, ip)
    return RedirectResponse("/ui/equipment", status_code=303)


# ── Performance logs ───────────────────────────────────────────────────────────

@router.get("/performance-logs/{log_id}/edit", response_class=HTMLResponse)
def perf_log_edit_form(
    log_id: int,
    request: Request,
    current_user: dict = Depends(get_current_user),
    track_db=Depends(get_track_db),
):
    if current_user["role"] not in ("Admin", "Coach"):
        return RedirectResponse("/ui/dashboard", status_code=303)
    track_db.execute(
        "SELECT pl.*, s.SportName FROM PerformanceLog pl "
        "JOIN Sport s ON pl.SportID=s.SportID WHERE pl.LogID=%s", (log_id,))
    log = track_db.fetchone()
    if not log:
        return RedirectResponse("/ui/members", status_code=303)
    log["RecordDate"] = str(log["RecordDate"])
    track_db.execute("SELECT SportID, SportName FROM Sport ORDER BY SportName")
    sports = track_db.fetchall()
    return templates.TemplateResponse("performance/form.html",
                                      _ctx(request, current_user, active="members",
                                           log=log, sports=sports, error=None))


@router.post("/performance-logs/{log_id}/edit")
def perf_log_edit_submit(
    log_id: int,
    request: Request,
    current_user: dict = Depends(get_current_user),
    track_db=Depends(get_track_db),
    auth_db=Depends(get_auth_db),
    member_id: int = Form(...),
    sport_id: int = Form(...),
    metric_name: str = Form(...),
    metric_value: float = Form(...),
    record_date: str = Form(...),
):
    if current_user["role"] not in ("Admin", "Coach"):
        return RedirectResponse(f"/ui/members/{member_id}", status_code=303)
    ip = request.client.host if request.client else "unknown"
    track_db.execute(
        "UPDATE PerformanceLog SET SportID=%s, MetricName=%s, MetricValue=%s, RecordDate=%s "
        "WHERE LogID=%s",
        (sport_id, metric_name, metric_value, record_date, log_id))
    write_audit_log(auth_db, current_user["user_id"], current_user["username"],
                    "UPDATE", "PerformanceLog", str(log_id), "SUCCESS",
                    {"metric": metric_name, "value": metric_value}, ip)
    return RedirectResponse(f"/ui/members/{member_id}", status_code=303)


@router.post("/performance-logs/{log_id}/delete")
def perf_log_delete(
    log_id: int,
    request: Request,
    current_user: dict = Depends(get_current_user),
    track_db=Depends(get_track_db),
    auth_db=Depends(get_auth_db),
    member_id: int = Form(...),
):
    if current_user["role"] not in ("Admin", "Coach"):
        return RedirectResponse(f"/ui/members/{member_id}", status_code=303)
    ip = request.client.host if request.client else "unknown"
    track_db.execute("DELETE FROM PerformanceLog WHERE LogID=%s", (log_id,))
    write_audit_log(auth_db, current_user["user_id"], current_user["username"],
                    "DELETE", "PerformanceLog", str(log_id), "SUCCESS", None, ip)
    return RedirectResponse(f"/ui/members/{member_id}", status_code=303)


@router.post("/performance-logs/new")
def performance_log_create(
    request: Request,
    current_user: dict = Depends(get_current_user),
    track_db=Depends(get_track_db),
    auth_db=Depends(get_auth_db),
    member_id: int = Form(...),
    sport_id: int = Form(...),
    metric_name: str = Form(...),
    metric_value: float = Form(...),
    record_date: str = Form(...),
):
    if current_user["role"] not in ("Admin", "Coach"):
        return RedirectResponse(f"/ui/members/{member_id}", status_code=303)
    ip = request.client.host if request.client else "unknown"
    track_db.execute("SELECT COALESCE(MAX(LogID), 0) + 1 AS nid FROM PerformanceLog")
    next_id = track_db.fetchone()["nid"]
    track_db.execute(
        "INSERT INTO PerformanceLog (LogID, MemberID, SportID, MetricName, MetricValue, RecordDate) "
        "VALUES (%s,%s,%s,%s,%s,%s)",
        (next_id, member_id, sport_id, metric_name, metric_value, record_date),
    )
    write_audit_log(auth_db, current_user["user_id"], current_user["username"],
                    "INSERT", "PerformanceLog", str(next_id), "SUCCESS",
                    {"member_id": member_id, "metric": metric_name}, ip)
    return RedirectResponse(f"/ui/members/{member_id}", status_code=303)


# ── Medical records ─────────────────────────────────────────────────────────────

@router.post("/medical-records/new")
def medical_record_create(
    request: Request,
    current_user: dict = Depends(get_current_user),
    track_db=Depends(get_track_db),
    auth_db=Depends(get_auth_db),
    member_id: int = Form(...),
    medical_condition: str = Form(...),
    diagnosis_date: str = Form(...),
    recovery_date: Optional[str] = Form(None),
    status: str = Form(...),
):
    if current_user["role"] != "Admin":
        return RedirectResponse(f"/ui/members/{member_id}", status_code=303)
    ip = request.client.host if request.client else "unknown"
    track_db.execute("SELECT COALESCE(MAX(RecordID), 0) + 1 AS nid FROM MedicalRecord")
    next_id = track_db.fetchone()["nid"]
    track_db.execute(
        "INSERT INTO MedicalRecord (RecordID, MemberID, MedicalCondition, DiagnosisDate, RecoveryDate, Status) "
        "VALUES (%s,%s,%s,%s,%s,%s)",
        (next_id, member_id, medical_condition, diagnosis_date, recovery_date or None, status),
    )
    write_audit_log(auth_db, current_user["user_id"], current_user["username"],
                    "INSERT", "MedicalRecord", str(next_id), "SUCCESS",
                    {"member_id": member_id, "condition": medical_condition}, ip)
    return RedirectResponse(f"/ui/members/{member_id}", status_code=303)


# ── Medical record edit/delete ─────────────────────────────────────────────────

@router.get("/medical-records/{record_id}/edit", response_class=HTMLResponse)
def medical_record_edit_form(
    record_id: int,
    request: Request,
    current_user: dict = Depends(get_current_user),
    track_db=Depends(get_track_db),
):
    if current_user["role"] != "Admin":
        return RedirectResponse("/ui/members", status_code=303)
    track_db.execute("SELECT * FROM MedicalRecord WHERE RecordID=%s", (record_id,))
    record = track_db.fetchone()
    if not record:
        return RedirectResponse("/ui/members", status_code=303)
    record["DiagnosisDate"] = str(record["DiagnosisDate"])
    if record.get("RecoveryDate"):
        record["RecoveryDate"] = str(record["RecoveryDate"])
    return templates.TemplateResponse("medical/form.html",
                                      _ctx(request, current_user, active="members",
                                           record=record, error=None))


@router.post("/medical-records/{record_id}/edit")
def medical_record_edit_submit(
    record_id: int,
    request: Request,
    current_user: dict = Depends(get_current_user),
    track_db=Depends(get_track_db),
    auth_db=Depends(get_auth_db),
    member_id: int = Form(...),
    medical_condition: str = Form(...),
    diagnosis_date: str = Form(...),
    recovery_date: Optional[str] = Form(None),
    status: str = Form(...),
):
    if current_user["role"] != "Admin":
        return RedirectResponse(f"/ui/members/{member_id}", status_code=303)
    ip = request.client.host if request.client else "unknown"
    track_db.execute(
        "UPDATE MedicalRecord SET MedicalCondition=%s, DiagnosisDate=%s, "
        "RecoveryDate=%s, Status=%s WHERE RecordID=%s",
        (medical_condition, diagnosis_date, recovery_date or None, status, record_id))
    write_audit_log(auth_db, current_user["user_id"], current_user["username"],
                    "UPDATE", "MedicalRecord", str(record_id), "SUCCESS",
                    {"condition": medical_condition}, ip)
    return RedirectResponse(f"/ui/members/{member_id}", status_code=303)


@router.post("/medical-records/{record_id}/delete")
def medical_record_delete(
    record_id: int,
    request: Request,
    current_user: dict = Depends(get_current_user),
    track_db=Depends(get_track_db),
    auth_db=Depends(get_auth_db),
    member_id: int = Form(...),
):
    if current_user["role"] != "Admin":
        return RedirectResponse(f"/ui/members/{member_id}", status_code=303)
    ip = request.client.host if request.client else "unknown"
    track_db.execute("DELETE FROM MedicalRecord WHERE RecordID=%s", (record_id,))
    write_audit_log(auth_db, current_user["user_id"], current_user["username"],
                    "DELETE", "MedicalRecord", str(record_id), "SUCCESS", None, ip)
    return RedirectResponse(f"/ui/members/{member_id}", status_code=303)


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


@router.post("/admin/verify-audit")
def verify_audit(
    request: Request,
    current_user: dict = Depends(get_current_user),
    auth_db=Depends(get_auth_db),
):
    if current_user["role"] != "Admin":
        return {"error": "Unauthorized"}
    
    result = verify_audit_chain(auth_db)
    return {"data": result}


# ── Root redirect ──────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
def root():
    return RedirectResponse("/ui/dashboard", status_code=303)
