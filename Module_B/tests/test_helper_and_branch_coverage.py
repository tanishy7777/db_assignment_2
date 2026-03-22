from __future__ import annotations
import asyncio
import importlib
import logging
from datetime import date, datetime
import pytest
from fastapi import HTTPException, Response
from fastapi.responses import RedirectResponse
from .conftest import GuardDB, ScriptedDB, make_request


def _raise_http(status_code: int, detail: str):
    def _impl(*args, **kwargs):
        raise HTTPException(status_code=status_code, detail=detail)
    return _impl


def test_validation_helpers_cover_core_branches():
    from app.services import validation
    assert validation.parse_iso_date("2024-03-01", "Date").isoformat() == "2024-03-01"
    assert validation.parse_iso_time("10:20:30", "Time").isoformat() == "10:20:30"
    assert validation.parse_iso_time("10:20", "Time").isoformat() == "10:20:00"
    with pytest.raises(ValueError):
        validation.parse_iso_date("03/01/2024", "Date")
    with pytest.raises(ValueError):
        validation.parse_iso_time("bad", "Time")
    with pytest.raises(ValueError):
        validation.validate_not_future(date(2099, 1, 1), "X")
    with pytest.raises(ValueError):
        validation.validate_date_order(date(2024, 2, 1), date(2024, 1, 1), "Start", "End")
    with pytest.raises(ValueError):
        validation.validate_time_order(validation.parse_iso_time("11:00", "S"), validation.parse_iso_time("10:00", "E"), "Start", "End")
    assert validation.validate_member_name("  Ada   Lovelace  ") == "Ada Lovelace"
    with pytest.raises(ValueError):
        validation.validate_member_name("")
    with pytest.raises(ValueError):
        validation.validate_member_name("Ada 123")
    assert validation.normalize_country_code(None) == validation.DEFAULT_COUNTRY_CODE
    assert validation.normalize_country_code("+1") == "+1"
    with pytest.raises(ValueError):
        validation.normalize_country_code("1")
    assert validation.combine_contact_number("+91", "98765-43210") == "+919876543210"
    with pytest.raises(ValueError):
        validation.combine_contact_number("+91", "123")
    assert validation.normalize_contact_number("9876543210") == "+919876543210"
    assert validation.normalize_contact_number("+1 1234567890") == "+11234567890"
    assert validation.normalize_contact_number("555") == "555"
    with pytest.raises(ValueError):
        validation.normalize_contact_number("+999999")
    assert validation.split_contact_number("+919876543210") == ("+91", "9876543210")
    assert validation.split_contact_number("9876543210") == ("+91", "9876543210")
    assert validation.split_contact_number(None) == ("+91", "")
    assert validation.derive_tournament_status(date(2099, 1, 1), date(2099, 1, 2)) == "Upcoming"
    assert validation.derive_tournament_status(date(2000, 1, 1), date(2000, 1, 2)) == "Completed"
    assert validation.derive_medical_status("Chronic", date(2000, 1, 1), None) == "Chronic"
    assert validation.derive_medical_status("Active", date(2000, 1, 1), date(2000, 1, 2)) == "Recovered"
    with pytest.raises(ValueError):
        validation.derive_medical_status("Active", date(2099, 1, 1), None)
    assert "Username already exists" in validation.humanize_db_error(Exception("Duplicate entry 'x' for key 'username'"))
    assert "Email address already exists" in validation.humanize_db_error(Exception("Duplicate entry 'x' for key 'email'"))
    assert "Tournament name must be unique" in validation.humanize_db_error(Exception("Duplicate entry 'x' for key 'uq_tournament_name'"))
    assert "That ID already exists" in validation.humanize_db_error(Exception("Duplicate entry '1' for key 'PRIMARY'"))
    assert "invalid" in validation.humanize_db_error(Exception("foreign key constraint fails")).lower()
    assert "End date" in validation.humanize_db_error(Exception("check constraint enddate"))
    assert "End time" in validation.humanize_db_error(Exception("check constraint endtime"))
    assert "Recovery date" in validation.humanize_db_error(Exception("check constraint recoverydate"))
    assert validation.humanize_db_error(Exception("plain")) == "plain"


def test_database_cursor_helper_commits_and_rolls_back():
    from app import database
    orig_secret = database._api_secret
    database._api_secret = None
    class FakeCursor:
        def __init__(self, with_rows):
            self.with_rows = with_rows
            self.closed = False
            self.fetchall_called = False
        def execute(self, query, params=None):
            pass
        def fetchall(self):
            self.fetchall_called = True
            return []
        def close(self):
            self.closed = True
    class FakeConn:
        def __init__(self, cursor):
            self._cursor = cursor
            self.committed = False
            self.rolled_back = False
            self.closed = False
        def cursor(self, dictionary=True):
            return self._cursor
        def commit(self):
            self.committed = True
        def rollback(self):
            self.rolled_back = True
        def close(self):
            self.closed = True
    class FakePool:
        def __init__(self, conn):
            self.conn = conn
        def get_connection(self):
            return self.conn
    success_cursor = FakeCursor(with_rows=True)
    success_conn = FakeConn(success_cursor)
    gen = database._get_cursor(FakePool(success_conn))
    yielded = next(gen)
    assert yielded is success_cursor
    with pytest.raises(StopIteration):
        next(gen)
    assert success_cursor.fetchall_called is True
    assert success_conn.committed is True
    assert success_conn.closed is True
    failure_cursor = FakeCursor(with_rows=False)
    failure_conn = FakeConn(failure_cursor)
    gen = database._get_cursor(FakePool(failure_conn))
    next(gen)
    with pytest.raises(RuntimeError):
        gen.throw(RuntimeError("boom"))
    assert failure_conn.rolled_back is True
    assert failure_cursor.closed is True
    database._auth_pool = FakePool(FakeConn(FakeCursor(False)))
    database._track_pool = FakePool(FakeConn(FakeCursor(False)))
    auth_gen = database.get_auth_db()
    next(auth_gen)
    with pytest.raises(StopIteration):
        next(auth_gen)
    track_gen = database.get_track_db()
    next(track_gen)
    with pytest.raises(StopIteration):
        next(track_gen)
    database._api_secret = orig_secret


def test_auth_dependency_get_current_user_branches(monkeypatch):
    from app.auth import dependencies
    req = make_request("/test")
    # No tokens at all → 401
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(dependencies.get_current_user(request=req, access_token=None, db=ScriptedDB()))
    assert exc_info.value.status_code == 401
    # Bad access token → 401 "Invalid token"
    monkeypatch.setattr(dependencies, "decode_token", lambda _token: (_ for _ in ()).throw(Exception("bad token")))
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(dependencies.get_current_user(request=req, access_token="abc", db=ScriptedDB()))
    assert exc_info.value.detail == "Invalid token"
    # Valid access token with type=access → returns payload directly
    monkeypatch.setattr(dependencies, "decode_token", lambda _token: {"user_id": 1, "username": "admin", "role": "Admin", "member_id": 1, "type": "access"})
    result = asyncio.run(
        dependencies.get_current_user(request=req, access_token="valid", db=ScriptedDB())
    )
    assert result["username"] == "admin"
    assert len(dependencies._hash_token("abc")) == 64


def test_audit_write_and_verify_intact_chain(monkeypatch):
    from app.services import audit
    logged = []
    monkeypatch.setattr(audit._file_logger, "info", lambda message: logged.append(message))
    db = ScriptedDB(fetchone_values=[None])
    audit.write_audit_log(db, 1, "admin", "SELECT", "Member", "1", "SUCCESS", {"x": 1}, "127.0.0.1")
    assert any("INSERT INTO audit_log" in query for query, _ in db.executed)
    assert logged and "admin" in logged[0]
    ts = datetime(2024, 3, 1, 10, 0, 0, 123000)
    prev_hash = "0" * 64
    entry_hash = audit._compute_entry_hash(
        "2024-03-01 10:00:00.123", 1, "admin", "SELECT", "Member", "1", "SUCCESS", None, "127.0.0.1", prev_hash
    )
    result = audit.verify_audit_chain(
        ScriptedDB(
            fetchall_values=[[
                {
                    "log_id": 1,
                    "timestamp": ts,
                    "user_id": 1,
                    "username": "admin",
                    "action": "SELECT",
                    "table_name": "Member",
                    "record_id": "1",
                    "status": "SUCCESS",
                    "details": None,
                    "ip_address": "127.0.0.1",
                    "prev_hash": prev_hash,
                    "entry_hash": entry_hash,
                }
            ]]
        )
    )
    assert result == {"intact": True, "total_entries": 1}


def test_performance_and_registration_branch_guards(coach_user, admin_user, monkeypatch):
    from app.routers import performance, registration
    monkeypatch.setattr(performance, "write_audit_log", lambda *a, **kw: None)
    monkeypatch.setattr(registration, "write_audit_log", lambda *a, **kw: None)
    with pytest.raises(HTTPException) as exc_info:
        performance.get_performance_log(
            1,
            make_request("/api/performance-logs/1"),
            current_user=coach_user,
            track_db=ScriptedDB(
                fetchone_values=[{"LogID": 1, "MemberID": 44, "SportID": 1, "RecordDate": "2024-03-01"} , None]
            ),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 403
    player_user = {"user_id": 3, "username": "player", "role": "Player", "member_id": 3}
    with pytest.raises(HTTPException) as exc_info:
        performance.get_performance_log(
            1,
            make_request("/api/performance-logs/1"),
            current_user=player_user,
            track_db=ScriptedDB(fetchone_values=[{"LogID": 1, "MemberID": 44, "SportID": 1, "RecordDate": "2024-03-01"}]),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 403
    with pytest.raises(HTTPException) as exc_info:
        registration.register_team_for_tournament(
            1,
            4,
            make_request("/api/registrations/tournament/1/team/4", method="POST"),
            current_user=admin_user,
            track_db=ScriptedDB(fetchone_values=[{"TournamentID": 1}, {"TeamID": 4, "CoachID": 2}, {"RegID": 1}]),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 409
    with pytest.raises(HTTPException) as exc_info:
        registration.add_team_to_event(
            1,
            4,
            make_request("/api/registrations/event/1/team/4", method="POST"),
            current_user=admin_user,
            track_db=ScriptedDB(
                fetchone_values=[
                    {"EventID": 1, "TournamentID": 9, "SportID": 1},
                    {"TeamID": 4, "SportID": 1, "CoachID": 2},
                    None,
                ]
            ),
            auth_db=ScriptedDB(),
        )
    assert "registered for the tournament" in exc_info.value.detail
    with pytest.raises(HTTPException) as exc_info:
        registration.remove_team_from_event(
            1,
            4,
            make_request("/api/registrations/event/1/team/4", method="DELETE"),
            current_user=coach_user,
            track_db=ScriptedDB(fetchone_values=[{"CoachID": 99}]),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 403


def test_team_helper_and_guard_branches(admin_user, coach_user, monkeypatch):
    from app.routers import teams
    monkeypatch.setattr(teams, "write_audit_log", lambda *a, **kw: None)
    assert teams._normalize_members([]) == []
    with pytest.raises(HTTPException):
        teams._normalize_members([teams.TeamMemberEntry(member_id=1), teams.TeamMemberEntry(member_id=1)])
    with pytest.raises(HTTPException) as exc_info:
        teams._validate_team_members(
            ScriptedDB(fetchall_values=[[{"MemberID": 1, "Role": "Player"}]]),
            [1, 2],
            None,
        )
    assert "Invalid member ID" in exc_info.value.detail
    with pytest.raises(HTTPException) as exc_info:
        teams._validate_team_members(
            ScriptedDB(fetchall_values=[[{"MemberID": 1, "Role": "Coach"}]]),
            [1],
            None,
        )
    assert "must be players" in exc_info.value.detail
    track_db = ScriptedDB(
        fetchall_values=[[{"MemberID": 1, "JoinDate": "2024-01-01", "Position": None}, {"MemberID": 2, "JoinDate": "2024-01-01", "Position": None}]]
    )
    teams._sync_team_members(track_db, 7, [teams.TeamMemberEntry(member_id=1), teams.TeamMemberEntry(member_id=3)], 3, "2024-01-01")
    executed_sql = [query for query, _ in track_db.executed]
    assert any("DELETE FROM TeamMember" in query for query in executed_sql)
    assert any("UPDATE TeamMember SET IsCaptain = %s" in query for query in executed_sql)
    assert any("INSERT INTO TeamMember" in query for query in executed_sql)
    create_result = teams.create_team(
        teams.TeamCreate(team_name="Owned", sport_id=1, formed_date="2024-01-01"),
        make_request("/api/teams", method="POST"),
        current_user=coach_user,
        track_db=ScriptedDB(fetchone_values=[{"Role": "Coach"}, {"SportID": 1}, {"nid": 22}], fetchall_values=[[], []]),
        auth_db=ScriptedDB(),
    )
    assert create_result["data"]["team_id"] == 22
    with pytest.raises(HTTPException) as exc_info:
        teams.create_team(
            teams.TeamCreate(team_name="Wrong", sport_id=1, formed_date="2024-01-01", coach_id=99),
            make_request("/api/teams", method="POST"),
            current_user=coach_user,
            track_db=ScriptedDB(),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 403
    with pytest.raises(HTTPException) as exc_info:
        teams.update_team(
            1,
            teams.TeamUpdate(),
            make_request("/api/teams/1", method="PUT"),
            current_user=admin_user,
            track_db=ScriptedDB(fetchone_values=[{"TeamID": 1, "CoachID": 2, "CaptainID": 1, "FormedDate": "2024-01-01"}]),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.detail == "No fields to update"
    result = teams.update_team(
        1,
        teams.TeamUpdate(captain_id=2),
        make_request("/api/teams/1", method="PUT"),
        current_user=admin_user,
        track_db=ScriptedDB(
            fetchone_values=[{"TeamID": 1, "CoachID": 2, "CaptainID": 1, "FormedDate": "2024-01-01"}],
            fetchall_values=[
                [{"MemberID": 1}, {"MemberID": 2}],
                [{"MemberID": 1, "Role": "Player"}, {"MemberID": 2, "Role": "Player"}],
            ],
        ),
        auth_db=ScriptedDB(),
    )
    assert result["success"] is True
    with pytest.raises(HTTPException):
        teams.delete_team(
            1,
            make_request("/api/teams/1", method="DELETE"),
            current_user={"user_id": 9, "username": "player", "role": "Player", "member_id": 9},
            track_db=ScriptedDB(fetchone_values=[{"TeamID": 1, "CoachID": 2}]),
            auth_db=ScriptedDB(),
        )


def test_ui_helper_and_redirect_branches(monkeypatch, admin_user, coach_user):
    from app.ui import routes as ui_routes
    ctx = ui_routes._ctx(make_request("/ui/demo", query_string="success=ok"), admin_user, extra="x")
    assert ctx["flash"]["type"] == "success"
    ctx = ui_routes._ctx(make_request("/ui/demo", query_string="error=bad"), admin_user)
    assert ctx["flash"]["type"] == "danger"
    redirect = ui_routes._flash_redirect("/ui/demo", success="Saved")
    assert isinstance(redirect, RedirectResponse)
    assert redirect.headers["location"] == "/ui/demo?success=Saved"
    assert ui_routes._parse_required_int("5", "ID") == 5
    assert ui_routes._parse_optional_int("", "ID") is None
    with pytest.raises(ValueError):
        ui_routes._parse_required_int("", "ID")
    with pytest.raises(ValueError):
        ui_routes._parse_optional_int("abc", "ID")
    class DummyForm:
        def getlist(self, key):
            if key == "member_ids":
                return ["1", "", "2"]
            if key == "member_positions":
                return ["pos1", "", "pos2"]
            return []
    from app.routers.teams import TeamMemberEntry
    assert ui_routes._parse_members(DummyForm()) == [
        TeamMemberEntry(member_id=1, position="pos1"),
        TeamMemberEntry(member_id=2, position="pos2"),
    ]
    defaults = ui_routes._member_form_defaults(form_data={"ContactNumber": "+919876543210"})
    assert defaults["ContactCountryCode"] == "+91"
    assert ui_routes.login_page(make_request("/ui/login")).status_code == 200
    monkeypatch.setattr(ui_routes, "api_login", lambda *args, **kwargs: (_ for _ in ()).throw(HTTPException(status_code=401, detail="bad")))
    assert ui_routes.login_submit(make_request("/ui/login", method="POST"), username="u", password="p", db=GuardDB(allowed_substrings=())).status_code == 200
    assert ui_routes.member_new_form(make_request("/ui/members/new"), coach_user).status_code == 303
    assert ui_routes.team_new_form(make_request("/ui/teams/new"), {"user_id": 8, "username": "p", "role": "Player", "member_id": 8}, GuardDB(allowed_substrings=())).status_code == 303
    assert ui_routes.tournament_new_form(make_request("/ui/tournaments/new"), coach_user).status_code == 303
    assert ui_routes.event_new_form(make_request("/ui/events/new"), coach_user, GuardDB(allowed_substrings=()), GuardDB(allowed_substrings=())).status_code == 303
    assert ui_routes.equipment_create(
        make_request("/ui/equipment/new", method="POST"),
        coach_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
        equipment_name="Cone",
        total_quantity=1,
        equipment_condition="New",
        sport_id="",
    ).status_code == 303


def test_id_generation_auth_router_and_jwt_branches(monkeypatch):
    from app.auth import jwt_handler
    from app.auth import router as auth_router
    from app.services import id_generation
    monkeypatch.setattr(auth_router, "write_audit_log", lambda *a, **kw: None)
    with pytest.raises(HTTPException) as exc_info:
        auth_router.login(
            auth_router.LoginRequest(username="missing", password="secret"),
            make_request("/api/auth/login", method="POST"),
            Response(),
            db=ScriptedDB(fetchone_values=[None]),
        )
    assert exc_info.value.status_code == 401
    monkeypatch.setattr(auth_router.bcrypt, "checkpw", lambda *_: False)
    with pytest.raises(HTTPException) as exc_info:
        auth_router.login(
            auth_router.LoginRequest(username="ada", password="secret"),
            make_request("/api/auth/login", method="POST"),
            Response(),
            db=ScriptedDB(
                fetchone_values=[
                    {
                        "user_id": 1,
                        "username": "ada",
                        "password_hash": "hashed",
                        "role": "Admin",
                        "member_id": 1,
                        "is_active": True,
                    }
                ]
            ),
        )
    assert exc_info.value.status_code == 401
    token = jwt_handler.create_access_token(1, "admin", "Admin", 1)
    payload = jwt_handler.decode_token(token)
    assert payload["username"] == "admin"
    assert payload["member_id"] == 1
    inserted = []
    assert id_generation.insert_with_generated_id(
        ScriptedDB(),
        requested_id=9,
        next_id_sql="SELECT 1",
        insert_fn=lambda generated_id: inserted.append(generated_id),
    ) == 9
    assert inserted == [9]
    retry_db = ScriptedDB(fetchone_values=[{"nid": 4}, {"nid": 5}])
    attempts = {"count": 0}
    def flaky_insert(generated_id):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise Exception("Duplicate entry '4' for key 'PRIMARY'")
        inserted.append(generated_id)
    assert id_generation.insert_with_generated_id(
        retry_db,
        requested_id=None,
        next_id_sql="SELECT COALESCE(MAX(MemberID), 0) + 1 AS nid",
        insert_fn=flaky_insert,
    ) == 5
    with pytest.raises(ValueError):
        id_generation.insert_with_generated_id(
            ScriptedDB(fetchone_values=[{"nid": 6}]),
            requested_id=None,
            next_id_sql="SELECT 6 AS nid",
            insert_fn=lambda _generated_id: (_ for _ in ()).throw(ValueError("boom")),
        )
    with pytest.raises(Exception) as exc_info:
        id_generation.insert_with_generated_id(
            ScriptedDB(fetchone_values=[{"nid": 7}, {"nid": 8}]),
            requested_id=None,
            next_id_sql="SELECT COALESCE(MAX(MemberID), 0) + 1 AS nid",
            insert_fn=lambda _generated_id: (_ for _ in ()).throw(Exception("Duplicate entry 'x' for key 'PRIMARY'")),
            max_attempts=2,
        )
    assert "Duplicate entry" in str(exc_info.value)
    with pytest.raises(RuntimeError):
        id_generation.insert_with_generated_id(
            ScriptedDB(),
            requested_id=None,
            next_id_sql="SELECT 1",
            insert_fn=lambda _generated_id: None,
            max_attempts=0,
        )


def test_member_and_team_ui_error_branches(monkeypatch, admin_user, coach_user):
    from app.ui import routes as ui_routes
    with pytest.raises(ValueError):
        ui_routes._parse_required_int("abc", "ID")
    class BadForm:
        def getlist(self, _key):
            return ["x"]
    with pytest.raises(ValueError):
        ui_routes._parse_members(BadForm())
    prepared = ui_routes._member_form_defaults(
        form_data={"ContactCountryCode": "+1", "ContactNumberLocal": "1234567890"}
    )
    assert prepared == {"ContactCountryCode": "+1", "ContactNumberLocal": "1234567890"}
    response = ui_routes.member_create(
        make_request("/ui/members/new", method="POST"),
        admin_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
        member_id=None,
        name="Ada Runner",
        email="ada@example.com",
        age=23,
        contact_country_code="+91",
        contact_number="",
        contact_number_local="123",
        gender="F",
        role="Player",
        join_date="2024-01-01",
        username="ada",
        password="secret",
    )
    assert response.status_code == 200
    monkeypatch.setattr(ui_routes, "api_create_member", lambda *args, **kwargs: {"success": False, "message": "save failed"})
    response = ui_routes.member_create(
        make_request("/ui/members/new", method="POST"),
        admin_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
        member_id=None,
        name="Ada Runner",
        email="ada@example.com",
        age=23,
        contact_country_code="+91",
        contact_number="",
        contact_number_local="9876543210",
        gender="F",
        role="Player",
        join_date="2024-01-01",
        username="ada",
        password="secret",
    )
    assert response.status_code == 200
    monkeypatch.setattr(ui_routes, "api_get_member_portfolio", _raise_http(404, "missing"))
    response = ui_routes.member_portfolio(
        7,
        make_request("/ui/members/7"),
        admin_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
    )
    assert response.headers["location"] == "/ui/members"
    response = ui_routes.member_edit_form(
        7,
        make_request("/ui/members/7/edit"),
        coach_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
    )
    assert response.headers["location"] == "/ui/members/7"
    response = ui_routes.member_edit_form(
        7,
        make_request("/ui/members/7/edit"),
        admin_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
    )
    assert response.headers["location"] == "/ui/members"
    response = ui_routes.member_edit_submit(
        7,
        make_request("/ui/members/7/edit", method="POST"),
        admin_user,
        ScriptedDB(fetchone_values=[{"MemberID": 7, "ContactNumber": "+911234567890"}]),
        GuardDB(allowed_substrings=()),
        name="Ada Runner",
        email="ada@example.com",
        age=24,
        contact_country_code="+91",
        contact_number="",
        contact_number_local="123",
    )
    assert response.status_code == 200
    monkeypatch.setattr(ui_routes, "api_update_member", _raise_http(403, "forbidden"))
    response = ui_routes.member_edit_submit(
        7,
        make_request("/ui/members/7/edit", method="POST"),
        admin_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
        name="Ada Runner",
        email="ada@example.com",
        age=24,
        contact_number="555-1111",
    )
    assert response.headers["location"] == "/ui/members/7"
    monkeypatch.setattr(ui_routes, "api_update_member", _raise_http(404, "missing"))
    response = ui_routes.member_edit_submit(
        7,
        make_request("/ui/members/7/edit", method="POST"),
        admin_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
        name="Ada Runner",
        email="ada@example.com",
        age=24,
        contact_number="555-1111",
    )
    assert response.headers["location"] == "/ui/members"
    monkeypatch.setattr(ui_routes, "api_update_member", _raise_http(400, "bad member"))
    response = ui_routes.member_edit_submit(
        7,
        make_request("/ui/members/7/edit", method="POST"),
        admin_user,
        GuardDB(
            allowed_substrings=("SELECT * FROM Member WHERE MemberID=%s",),
            fetchone_values=[{"MemberID": 7, "ContactNumber": "+911234567890"}],
        ),
        GuardDB(allowed_substrings=()),
        name="Ada Runner",
        email="ada@example.com",
        age=24,
        contact_number="555-1111",
    )
    assert response.status_code == 200
    monkeypatch.setattr(
        ui_routes,
        "api_get_team",
        lambda *args, **kwargs: {
            "success": True,
            "data": {
                "team": {"TeamID": 1, "TeamName": "Sprinters", "SportID": 1, "CoachID": 99, "CaptainID": 5, "FormedDate": "2024-01-01"},
                "roster": [],
            },
        },
    )
    response = ui_routes.team_edit_form(
        1,
        make_request("/ui/teams/1/edit"),
        coach_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
    )
    assert response.headers["location"] == "/ui/teams/1"
    monkeypatch.setattr(ui_routes, "api_get_team", _raise_http(404, "missing"))
    response = ui_routes.team_edit_form(
        1,
        make_request("/ui/teams/1/edit"),
        admin_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
    )
    assert response.headers["location"] == "/ui/teams"
    response = asyncio.run(
        ui_routes.team_edit_submit(
            1,
            make_request(
                "/ui/teams/1/edit",
                method="POST",
                form_data={"team_name": "Sprinters", "sport_id": "1", "coach_id": "2", "captain_id": "1", "formed_date": "2024-01-01", "member_ids": ["x"]},
            ),
            admin_user,
            ScriptedDB(fetchall_values=[[]]),
            GuardDB(allowed_substrings=()),
        )
    )
    assert response.status_code == 200
    monkeypatch.setattr(ui_routes, "api_update_team", _raise_http(403, "forbidden"))
    response = asyncio.run(
        ui_routes.team_edit_submit(
            1,
            make_request(
                "/ui/teams/1/edit",
                method="POST",
                form_data={"team_name": "Sprinters", "sport_id": "1", "coach_id": "2", "captain_id": "1", "formed_date": "2024-01-01", "member_ids": ["1"]},
            ),
            admin_user,
            GuardDB(allowed_substrings=()),
            GuardDB(allowed_substrings=()),
        )
    )
    assert response.headers["location"] == "/ui/teams/1"
    monkeypatch.setattr(ui_routes, "api_update_team", _raise_http(404, "missing"))
    response = asyncio.run(
        ui_routes.team_edit_submit(
            1,
            make_request(
                "/ui/teams/1/edit",
                method="POST",
                form_data={"team_name": "Sprinters", "sport_id": "1", "coach_id": "2", "captain_id": "1", "formed_date": "2024-01-01", "member_ids": ["1"]},
            ),
            admin_user,
            GuardDB(allowed_substrings=()),
            GuardDB(allowed_substrings=()),
        )
    )
    assert response.headers["location"] == "/ui/teams"
    monkeypatch.setattr(ui_routes, "api_update_team", _raise_http(400, "bad team"))
    response = asyncio.run(
        ui_routes.team_edit_submit(
            1,
            make_request(
                "/ui/teams/1/edit",
                method="POST",
                form_data={"team_name": "Sprinters", "sport_id": "1", "coach_id": "2", "captain_id": "1", "formed_date": "2024-01-01", "member_ids": ["1"]},
            ),
            admin_user,
            ScriptedDB(fetchall_values=[[]]),
            GuardDB(allowed_substrings=()),
        )
    )
    assert response.status_code == 200


def test_tournament_event_equipment_and_admin_ui_error_branches(monkeypatch, admin_user, coach_user, player_user):
    from app.ui import routes as ui_routes
    monkeypatch.setattr(ui_routes, "_get_event_lookups", lambda *args, **kwargs: {"sports": [], "venues": [], "tournaments": []})
    monkeypatch.setattr(ui_routes, "api_create_tournament", _raise_http(400, "bad tournament"))
    response = ui_routes.tournament_create(
        make_request("/ui/tournaments/new", method="POST"),
        admin_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
        tournament_name="Open Meet",
        start_date="2024-03-01",
        end_date="2024-03-02",
        description="City meet",
        status="Upcoming",
    )
    assert response.status_code == 200
    monkeypatch.setattr(ui_routes, "api_get_tournament", _raise_http(404, "missing"))
    response = ui_routes.tournament_detail(
        1,
        make_request("/ui/tournaments/1"),
        admin_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
    )
    assert response.headers["location"] == "/ui/tournaments"
    response = ui_routes.tournament_edit_page(
        1,
        make_request("/ui/tournaments/1/edit-form"),
        coach_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
    )
    assert response.headers["location"] == "/ui/tournaments/1"
    response = ui_routes.tournament_edit_page(
        1,
        make_request("/ui/tournaments/1/edit-form"),
        admin_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
    )
    assert response.headers["location"] == "/ui/tournaments"
    response = ui_routes.tournament_edit_submit(
        1,
        make_request("/ui/tournaments/1/edit", method="POST"),
        coach_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
        tournament_name="Open Meet",
        start_date="2024-03-01",
        end_date="2024-03-02",
        description="City meet",
        status="Upcoming",
    )
    assert response.headers["location"] == "/ui/tournaments/1"
    monkeypatch.setattr(ui_routes, "api_update_tournament", _raise_http(400, "bad tournament"))
    response = ui_routes.tournament_edit_submit(
        1,
        make_request("/ui/tournaments/1/edit", method="POST"),
        admin_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
        tournament_name="Open Meet",
        start_date="2024-03-01",
        end_date="2024-03-02",
        description="City meet",
        status="Upcoming",
    )
    assert response.status_code == 200
    monkeypatch.setattr(ui_routes, "api_update_tournament", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))
    response = ui_routes.tournament_edit_submit(
        1,
        make_request("/ui/tournaments/1/edit", method="POST"),
        admin_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
        tournament_name="Open Meet",
        start_date="2024-03-01",
        end_date="2024-03-02",
        description="City meet",
        status="Upcoming",
    )
    assert "boom" in response.headers["location"]
    response = ui_routes.tournament_delete(
        1,
        make_request("/ui/tournaments/1/delete", method="POST"),
        coach_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
    )
    assert response.headers["location"] == "/ui/tournaments"
    monkeypatch.setattr(ui_routes, "api_delete_tournament", _raise_http(400, "cannot delete"))
    response = ui_routes.tournament_delete(
        1,
        make_request("/ui/tournaments/1/delete", method="POST"),
        admin_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
    )
    assert "cannot+delete" in response.headers["location"]
    response = ui_routes.tournament_register_team(
        1,
        make_request("/ui/tournaments/1/register", method="POST"),
        player_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
        team_id=1,
    )
    assert response.headers["location"] == "/ui/tournaments/1"
    monkeypatch.setattr(ui_routes, "api_register_team_for_tournament", _raise_http(400, "duplicate team"))
    response = ui_routes.tournament_register_team(
        1,
        make_request("/ui/tournaments/1/register", method="POST"),
        admin_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
        team_id=1,
    )
    assert "duplicate+team" in response.headers["location"]
    monkeypatch.setattr(ui_routes, "api_register_team_for_tournament", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))
    response = ui_routes.tournament_register_team(
        1,
        make_request("/ui/tournaments/1/register", method="POST"),
        admin_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
        team_id=1,
    )
    assert "boom" in response.headers["location"]
    response = ui_routes.tournament_unregister_team(
        1,
        1,
        make_request("/ui/tournaments/1/unregister/1", method="POST"),
        coach_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
    )
    assert response.headers["location"] == "/ui/tournaments/1"
    monkeypatch.setattr(ui_routes, "api_unregister_team_from_tournament", _raise_http(400, "not registered"))
    response = ui_routes.tournament_unregister_team(
        1,
        1,
        make_request("/ui/tournaments/1/unregister/1", method="POST"),
        admin_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
    )
    assert "not+registered" in response.headers["location"]
    response = ui_routes.event_create(
        make_request("/ui/events/new", method="POST"),
        coach_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
        event_name="Final Heat",
        tournament_id=1,
        event_date="2024-03-01",
        start_time="10:00",
        end_time="11:00",
        venue_id=1,
        sport_id=1,
        status="Scheduled",
        round_name="Final",
    )
    assert response.headers["location"] == "/ui/events"
    monkeypatch.setattr(ui_routes, "api_create_event", _raise_http(400, "bad event"))
    response = ui_routes.event_create(
        make_request("/ui/events/new", method="POST"),
        admin_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
        event_name="Final Heat",
        tournament_id=1,
        event_date="2024-03-01",
        start_time="10:00",
        end_time="11:00",
        venue_id=1,
        sport_id=1,
        status="Scheduled",
        round_name="Final",
    )
    assert response.status_code == 200
    response = ui_routes.event_edit_form(
        1,
        make_request("/ui/events/1/edit"),
        coach_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
    )
    assert response.headers["location"] == "/ui/events/1"
    monkeypatch.setattr(ui_routes, "api_get_event", _raise_http(404, "missing"))
    response = ui_routes.event_edit_form(
        1,
        make_request("/ui/events/1/edit"),
        admin_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
    )
    assert response.headers["location"] == "/ui/events"
    response = ui_routes.event_edit_submit(
        1,
        make_request("/ui/events/1/edit", method="POST"),
        coach_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
        event_name="Final Heat",
        tournament_id=1,
        event_date="2024-03-01",
        start_time="10:00",
        end_time="11:00",
        venue_id=1,
        sport_id=1,
        status="Scheduled",
        round_name="Final",
    )
    assert response.headers["location"] == "/ui/events/1"
    monkeypatch.setattr(ui_routes, "api_update_event", _raise_http(400, "bad event"))
    response = ui_routes.event_edit_submit(
        1,
        make_request("/ui/events/1/edit", method="POST"),
        admin_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
        event_name="Final Heat",
        tournament_id=1,
        event_date="2024-03-01",
        start_time="10:00",
        end_time="11:00",
        venue_id=1,
        sport_id=1,
        status="Scheduled",
        round_name="Final",
    )
    assert response.status_code == 200
    monkeypatch.setattr(ui_routes, "api_update_event", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))
    response = ui_routes.event_edit_submit(
        1,
        make_request("/ui/events/1/edit", method="POST"),
        admin_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
        event_name="Final Heat",
        tournament_id=1,
        event_date="2024-03-01",
        start_time="10:00",
        end_time="11:00",
        venue_id=1,
        sport_id=1,
        status="Scheduled",
        round_name="Final",
    )
    assert "boom" in response.headers["location"]
    response = ui_routes.event_delete(
        1,
        make_request("/ui/events/1/delete", method="POST"),
        coach_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
    )
    assert response.headers["location"] == "/ui/events"
    monkeypatch.setattr(ui_routes, "api_delete_event", _raise_http(400, "cannot delete"))
    response = ui_routes.event_delete(
        1,
        make_request("/ui/events/1/delete", method="POST"),
        admin_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
    )
    assert "cannot+delete" in response.headers["location"]
    response = ui_routes.event_add_team(
        1,
        make_request("/ui/events/1/add-team", method="POST"),
        player_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
        team_id=1,
    )
    assert response.headers["location"] == "/ui/events/1"
    monkeypatch.setattr(ui_routes, "api_add_team_to_event", _raise_http(404, "missing"))
    response = ui_routes.event_add_team(
        1,
        make_request("/ui/events/1/add-team", method="POST"),
        admin_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
        team_id=1,
    )
    assert response.headers["location"].startswith("/ui/events?error=")
    monkeypatch.setattr(ui_routes, "api_add_team_to_event", _raise_http(400, "duplicate team"))
    response = ui_routes.event_add_team(
        1,
        make_request("/ui/events/1/add-team", method="POST"),
        admin_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
        team_id=1,
    )
    assert response.headers["location"].startswith("/ui/events/1?error=")
    response = ui_routes.event_remove_team(
        1,
        1,
        make_request("/ui/events/1/remove-team/1", method="POST"),
        player_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
    )
    assert response.headers["location"] == "/ui/events/1"
    monkeypatch.setattr(ui_routes, "api_remove_team_from_event", _raise_http(400, "not participating"))
    response = ui_routes.event_remove_team(
        1,
        1,
        make_request("/ui/events/1/remove-team/1", method="POST"),
        admin_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
    )
    assert "not+participating" in response.headers["location"]
    response = ui_routes.event_detail(
        1,
        make_request("/ui/events/1"),
        admin_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
    )
    assert response.headers["location"] == "/ui/events"
    monkeypatch.setattr(ui_routes, "api_list_equipment", lambda *args, **kwargs: {"success": True, "data": []})
    monkeypatch.setattr(ui_routes, "api_list_issues", lambda *args, **kwargs: {"success": True, "data": []})
    response = ui_routes.equipment_list(
        make_request("/ui/equipment"),
        coach_user,
        GuardDB(
            allowed_substrings=("SELECT DISTINCT m.MemberID, m.Name, m.Role, m.Gender, m.JoinDate",),
            fetchall_values=[[{"MemberID": 10, "Name": "Ada Runner", "Role": "Player", "Gender": "F", "JoinDate": "2024-01-01"}]],
        ),
        GuardDB(allowed_substrings=()),
    )
    assert response.status_code == 200
    response = ui_routes.equipment_create(
        make_request("/ui/equipment/new", method="POST"),
        admin_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
        equipment_name="Baton",
        total_quantity=4,
        equipment_condition="Good",
        sport_id="abc",
    )
    assert "Sport+ID+must+be+a+valid+integer." in response.headers["location"]
    monkeypatch.setattr(ui_routes, "api_create_equipment", _raise_http(400, "bad equipment"))
    response = ui_routes.equipment_create(
        make_request("/ui/equipment/new", method="POST"),
        admin_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
        equipment_name="Baton",
        total_quantity=4,
        equipment_condition="Good",
        sport_id="1",
    )
    assert "bad+equipment" in response.headers["location"]
    response = ui_routes.equipment_issue(
        make_request("/ui/equipment/issue", method="POST"),
        player_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
        equipment_id=1,
        member_id=1,
        issue_date="2024-03-01",
        quantity=1,
    )
    assert response.headers["location"] == "/ui/equipment"
    monkeypatch.setattr(ui_routes, "api_issue_equipment", _raise_http(400, "out of stock"))
    response = ui_routes.equipment_issue(
        make_request("/ui/equipment/issue", method="POST"),
        admin_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
        equipment_id=1,
        member_id=1,
        issue_date="2024-03-01",
        quantity=1,
    )
    assert "out+of+stock" in response.headers["location"]
    response = ui_routes.equipment_return(
        1,
        make_request("/ui/equipment/issue/1/return", method="POST"),
        player_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
        return_date="2024-03-02",
    )
    assert response.headers["location"] == "/ui/equipment"
    monkeypatch.setattr(ui_routes, "api_return_equipment", _raise_http(400, "late return"))
    response = ui_routes.equipment_return(
        1,
        make_request("/ui/equipment/issue/1/return", method="POST"),
        admin_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
        return_date="2024-03-02",
    )
    assert "late+return" in response.headers["location"]
    response = ui_routes.perf_log_edit_form(
        1,
        make_request("/ui/performance-logs/1/edit"),
        player_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
    )
    assert response.headers["location"] == "/ui/dashboard"
    monkeypatch.setattr(ui_routes, "api_get_performance_log", _raise_http(404, "missing"))
    response = ui_routes.perf_log_edit_form(
        1,
        make_request("/ui/performance-logs/1/edit"),
        admin_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
    )
    assert response.headers["location"] == "/ui/members"
    response = ui_routes.perf_log_edit_submit(
        1,
        make_request("/ui/performance-logs/1/edit", method="POST"),
        player_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
        member_id=1,
        sport_id=1,
        metric_name="Speed",
        metric_value=9.8,
        record_date="2024-03-01",
    )
    assert response.headers["location"] == "/ui/members/1"
    monkeypatch.setattr(ui_routes, "api_update_performance_log", _raise_http(400, "bad log"))
    response = ui_routes.perf_log_edit_submit(
        1,
        make_request("/ui/performance-logs/1/edit", method="POST"),
        admin_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
        member_id=1,
        sport_id=1,
        metric_name="Speed",
        metric_value=9.8,
        record_date="2024-03-01",
    )
    assert "bad+log" in response.headers["location"]
    response = ui_routes.perf_log_delete(
        1,
        make_request("/ui/performance-logs/1/delete", method="POST"),
        player_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
        member_id=1,
    )
    assert response.headers["location"] == "/ui/members/1"
    monkeypatch.setattr(ui_routes, "api_delete_performance_log", _raise_http(400, "cannot delete"))
    response = ui_routes.perf_log_delete(
        1,
        make_request("/ui/performance-logs/1/delete", method="POST"),
        admin_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
        member_id=1,
    )
    assert "cannot+delete" in response.headers["location"]
    response = ui_routes.performance_log_create(
        make_request("/ui/performance-logs/new", method="POST"),
        player_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
        member_id=1,
        sport_id=1,
        metric_name="Speed",
        metric_value=9.8,
        record_date="2024-03-01",
    )
    assert response.headers["location"] == "/ui/members/1"
    monkeypatch.setattr(ui_routes, "api_create_performance_log", _raise_http(400, "bad log"))
    response = ui_routes.performance_log_create(
        make_request("/ui/performance-logs/new", method="POST"),
        admin_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
        member_id=1,
        sport_id=1,
        metric_name="Speed",
        metric_value=9.8,
        record_date="2024-03-01",
    )
    assert "bad+log" in response.headers["location"]
    response = ui_routes.medical_record_create(
        make_request("/ui/medical-records/new", method="POST"),
        coach_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
        member_id=1,
        medical_condition="Strain",
        diagnosis_date="2024-03-01",
        recovery_date="2024-03-10",
        status="Active",
    )
    assert response.headers["location"] == "/ui/members/1"
    monkeypatch.setattr(ui_routes, "api_create_medical_record", _raise_http(400, "bad medical"))
    response = ui_routes.medical_record_create(
        make_request("/ui/medical-records/new", method="POST"),
        admin_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
        member_id=1,
        medical_condition="Strain",
        diagnosis_date="2024-03-01",
        recovery_date="2024-03-10",
        status="Active",
    )
    assert "bad+medical" in response.headers["location"]
    response = ui_routes.medical_record_edit_form(
        1,
        make_request("/ui/medical-records/1/edit"),
        coach_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
    )
    assert response.headers["location"] == "/ui/members"
    monkeypatch.setattr(ui_routes, "api_get_medical_record", _raise_http(404, "missing"))
    response = ui_routes.medical_record_edit_form(
        1,
        make_request("/ui/medical-records/1/edit"),
        admin_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
    )
    assert response.headers["location"] == "/ui/members"
    response = ui_routes.medical_record_edit_submit(
        1,
        make_request("/ui/medical-records/1/edit", method="POST"),
        coach_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
        member_id=1,
        medical_condition="Strain",
        diagnosis_date="2024-03-01",
        recovery_date="2024-03-10",
        status="Active",
    )
    assert response.headers["location"] == "/ui/members/1"
    monkeypatch.setattr(ui_routes, "api_update_medical_record", _raise_http(400, "bad medical"))
    response = ui_routes.medical_record_edit_submit(
        1,
        make_request("/ui/medical-records/1/edit", method="POST"),
        admin_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
        member_id=1,
        medical_condition="Strain",
        diagnosis_date="2024-03-01",
        recovery_date="2024-03-10",
        status="Active",
    )
    assert "bad+medical" in response.headers["location"]
    response = ui_routes.medical_record_delete(
        1,
        make_request("/ui/medical-records/1/delete", method="POST"),
        coach_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
        member_id=1,
    )
    assert response.headers["location"] == "/ui/members/1"
    monkeypatch.setattr(ui_routes, "api_delete_medical_record", _raise_http(400, "cannot delete"))
    response = ui_routes.medical_record_delete(
        1,
        make_request("/ui/medical-records/1/delete", method="POST"),
        admin_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
        member_id=1,
    )
    assert "cannot+delete" in response.headers["location"]
    response = ui_routes.audit_page(
        make_request("/ui/admin/audit"),
        coach_user,
        GuardDB(allowed_substrings=()),
    )
    assert response.headers["location"] == "/ui/dashboard"
    assert ui_routes.verify_audit(make_request("/ui/admin/verify-audit", method="POST"), coach_user, GuardDB(allowed_substrings=())) == {"error": "Unauthorized"}
    monkeypatch.setattr(
        ui_routes,
        "api_get_audit_log",
        lambda **kwargs: {
            "success": True,
            "data": [
                {
                    "log_id": 1,
                    "timestamp": "2024-03-01 10:00:00",
                    "username": "admin",
                    "action": "LOGIN",
                    "table_name": "sessions",
                    "record_id": "1",
                    "status": "SUCCESS",
                    "ip_address": "127.0.0.1",
                    "entry_hash": "a" * 64,
                }
            ],
        },
    )
    monkeypatch.setattr(ui_routes, "api_verify_audit", lambda **kwargs: {"success": True, "intact": True})
    assert ui_routes.audit_page(make_request("/ui/admin/audit"), admin_user, GuardDB(allowed_substrings=())).status_code == 200
    assert ui_routes.verify_audit(make_request("/ui/admin/verify-audit", method="POST"), admin_user, GuardDB(allowed_substrings=())) == {"success": True, "intact": True}
    assert ui_routes.root().headers["location"] == "/ui/dashboard"


def test_performance_router_failure_and_branch_coverage(monkeypatch, admin_user, coach_user, player_user):
    from app.routers import performance
    monkeypatch.setattr(performance, "write_audit_log", lambda *args, **kwargs: None)
    rows = [{"LogID": 1, "MemberID": 3, "SportName": "Track", "MemberName": "Ada", "RecordDate": date(2024, 3, 1)}]
    admin_result = performance.list_performance_logs(
        make_request("/api/performance-logs"),
        current_user=admin_user,
        track_db=ScriptedDB(fetchall_values=[list(rows)]),
        auth_db=ScriptedDB(),
    )
    coach_result = performance.list_performance_logs(
        make_request("/api/performance-logs"),
        current_user=coach_user,
        track_db=ScriptedDB(fetchall_values=[list(rows)]),
        auth_db=ScriptedDB(),
    )
    player_result = performance.list_performance_logs(
        make_request("/api/performance-logs"),
        current_user=player_user,
        track_db=ScriptedDB(fetchall_values=[list(rows)]),
        auth_db=ScriptedDB(),
    )
    assert admin_result["data"][0]["RecordDate"] == "2024-03-01"
    assert coach_result["data"][0]["RecordDate"] == "2024-03-01"
    assert player_result["data"][0]["RecordDate"] == "2024-03-01"
    from app.services import rbac as rbac_module
    with pytest.raises(HTTPException) as exc_info:
        rbac_module.assert_coach_manages_member(ScriptedDB(fetchone_values=[None]), coach_user, 5)
    assert exc_info.value.status_code == 403
    rbac_module.assert_coach_manages_member(ScriptedDB(), admin_user, 5)
    with pytest.raises(HTTPException) as exc_info:
        performance.create_performance_log(
            performance.PerfLogCreate(member_id=3, sport_id=1, metric_name="Speed", metric_value=9.7, record_date="2099-01-01"),
            make_request("/api/performance-logs", method="POST"),
            current_user=admin_user,
            track_db=ScriptedDB(),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 400
    monkeypatch.setattr(performance, "insert_with_generated_id", lambda *args, **kwargs: (_ for _ in ()).throw(Exception("duplicate")))
    with pytest.raises(HTTPException) as exc_info:
        performance.create_performance_log(
            performance.PerfLogCreate(member_id=3, sport_id=1, metric_name="Speed", metric_value=9.7, record_date="2024-03-01"),
            make_request("/api/performance-logs", method="POST"),
            current_user=admin_user,
            track_db=ScriptedDB(),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 400
    with pytest.raises(HTTPException) as exc_info:
        performance.update_performance_log(
            1,
            performance.PerfLogUpdate(),
            make_request("/api/performance-logs/1", method="PUT"),
            current_user=admin_user,
            track_db=ScriptedDB(fetchone_values=[{"LogID": 1, "MemberID": 3}]),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.detail == "No fields to update"
    with pytest.raises(HTTPException) as exc_info:
        performance.update_performance_log(
            1,
            performance.PerfLogUpdate(record_date="2099-01-01"),
            make_request("/api/performance-logs/1", method="PUT"),
            current_user=admin_user,
            track_db=ScriptedDB(fetchone_values=[{"LogID": 1, "MemberID": 3}]),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 400
    with pytest.raises(HTTPException) as exc_info:
        performance.update_performance_log(
            1,
            performance.PerfLogUpdate(metric_name="Speed"),
            make_request("/api/performance-logs/1", method="PUT"),
            current_user=admin_user,
            track_db=ScriptedDB(fetchone_values=[{"LogID": 1, "MemberID": 3}], execute_side_effects=[None, Exception("boom")]),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 400
    with pytest.raises(HTTPException) as exc_info:
        performance.delete_performance_log(
            1,
            make_request("/api/performance-logs/1", method="DELETE"),
            current_user=admin_user,
            track_db=ScriptedDB(fetchone_values=[None]),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 404


def test_tournament_router_failure_and_branch_coverage(monkeypatch, admin_user, coach_user):
    from app.routers import tournaments
    monkeypatch.setattr(tournaments, "write_audit_log", lambda *args, **kwargs: None)
    with pytest.raises(HTTPException) as exc_info:
        tournaments._validate_tournament_fields(ScriptedDB(fetchall_values=[[{"TournamentID": 1}]]), "Open Meet", "2024-03-01", "2024-03-02")
    assert exc_info.value.status_code == 400
    result = tournaments.get_tournament(
        1,
        make_request("/api/tournaments/1"),
        current_user=coach_user,
        track_db=ScriptedDB(
            fetchone_values=[{"TournamentID": 1, "TournamentName": "Open Meet", "StartDate": date(2024, 3, 1), "EndDate": date(2024, 3, 2), "Status": "Upcoming", "Description": None}],
            fetchall_values=[
                [{"EventID": 1, "EventDate": date(2024, 3, 1), "StartTime": "10:00:00", "EndTime": "11:00:00"}],
                [{"TeamID": 2, "TeamName": "Already In", "SportName": "Track"}],
                [{"TeamID": 2, "TeamName": "Already In"}, {"TeamID": 3, "TeamName": "Eligible"}],
            ],
        ),
        auth_db=ScriptedDB(),
    )
    assert result["data"]["available_teams"] == [{"TeamID": 3, "TeamName": "Eligible"}]
    monkeypatch.setattr(tournaments, "insert_with_generated_id", lambda *args, **kwargs: (_ for _ in ()).throw(Exception("duplicate")))
    with pytest.raises(HTTPException) as exc_info:
        tournaments.create_tournament(
            tournaments.TournamentCreate(tournament_name="Open Meet", start_date="2024-03-01", end_date="2024-03-02", status="Upcoming"),
            make_request("/api/tournaments", method="POST"),
            current_user=admin_user,
            track_db=ScriptedDB(),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 400
    with pytest.raises(HTTPException) as exc_info:
        tournaments.update_tournament(
            1,
            tournaments.TournamentUpdate(),
            make_request("/api/tournaments/1", method="PUT"),
            current_user=admin_user,
            track_db=ScriptedDB(fetchone_values=[{"TournamentID": 1, "TournamentName": "Open Meet", "StartDate": date(2024, 3, 1), "EndDate": date(2024, 3, 2), "Status": "Upcoming"}]),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.detail == "No fields to update"
    with pytest.raises(HTTPException) as exc_info:
        tournaments.update_tournament(
            1,
            tournaments.TournamentUpdate(description="Updated"),
            make_request("/api/tournaments/1", method="PUT"),
            current_user=admin_user,
            track_db=ScriptedDB(
                fetchone_values=[{"TournamentID": 1, "TournamentName": "Open Meet", "StartDate": date(2024, 3, 1), "EndDate": date(2024, 3, 2), "Status": "Upcoming"}],
                fetchall_values=[[]],
                execute_side_effects=[None, None, Exception("boom")],
            ),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 400


def test_equipment_router_failure_and_branch_coverage(monkeypatch, admin_user, coach_user, player_user):
    from app.routers import equipment
    monkeypatch.setattr(equipment, "write_audit_log", lambda *args, **kwargs: None)
    from app.services import rbac as rbac_module
    with pytest.raises(HTTPException) as exc_info:
        rbac_module.assert_coach_manages_member(ScriptedDB(fetchone_values=[None]), coach_user, 4)
    assert exc_info.value.status_code == 403
    rbac_module.assert_coach_manages_member(ScriptedDB(), admin_user, 4)
    with pytest.raises(HTTPException):
        equipment.create_equipment(
            equipment.EquipmentCreate(equipment_name="Cone", total_quantity=-1, equipment_condition="New"),
            make_request("/api/equipment", method="POST"),
            current_user=admin_user,
            track_db=ScriptedDB(),
            auth_db=ScriptedDB(),
        )
    monkeypatch.setattr(equipment, "insert_with_generated_id", lambda *args, **kwargs: (_ for _ in ()).throw(Exception("duplicate")))
    with pytest.raises(HTTPException):
        equipment.create_equipment(
            equipment.EquipmentCreate(equipment_name="Cone", total_quantity=1, equipment_condition="New"),
            make_request("/api/equipment", method="POST"),
            current_user=admin_user,
            track_db=ScriptedDB(),
            auth_db=ScriptedDB(),
        )
    player_rows = equipment.list_issues(
        make_request("/api/equipment/issues"),
        current_user=player_user,
        track_db=ScriptedDB(fetchall_values=[[{"IssueDate": date(2024, 3, 1), "ReturnDate": date(2024, 3, 2)}]]),
        auth_db=ScriptedDB(),
        active_only=True,
    )
    coach_rows = equipment.list_issues(
        make_request("/api/equipment/issues"),
        current_user=coach_user,
        track_db=ScriptedDB(fetchall_values=[[{"IssueDate": date(2024, 3, 1), "ReturnDate": None}]]),
        auth_db=ScriptedDB(),
    )
    admin_rows = equipment.list_issues(
        make_request("/api/equipment/issues"),
        current_user=admin_user,
        track_db=ScriptedDB(fetchall_values=[[{"IssueDate": date(2024, 3, 1), "ReturnDate": None}]]),
        auth_db=ScriptedDB(),
    )
    assert player_rows["data"][0]["ReturnDate"] == "2024-03-02"
    assert coach_rows["data"][0]["IssueDate"] == "2024-03-01"
    assert admin_rows["data"][0]["IssueDate"] == "2024-03-01"
    with pytest.raises(HTTPException):
        equipment.issue_equipment(
            equipment.IssueCreate(equipment_id=1, member_id=3, issue_date="2024-03-01", quantity=0),
            make_request("/api/equipment/issue", method="POST"),
            current_user=admin_user,
            track_db=ScriptedDB(),
            auth_db=ScriptedDB(),
        )
    with pytest.raises(HTTPException) as exc_info:
        equipment.issue_equipment(
            equipment.IssueCreate(equipment_id=1, member_id=3, issue_date="2024-03-01", quantity=1),
            make_request("/api/equipment/issue", method="POST"),
            current_user=admin_user,
            track_db=ScriptedDB(fetchone_values=[None]),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 404
    with pytest.raises(HTTPException) as exc_info:
        equipment.issue_equipment(
            equipment.IssueCreate(equipment_id=1, member_id=3, issue_date="2024-03-01", quantity=5),
            make_request("/api/equipment/issue", method="POST"),
            current_user=admin_user,
            track_db=ScriptedDB(fetchone_values=[{"TotalQuantity": 3, "issued": 1}]),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 400
    monkeypatch.setattr(equipment, "insert_with_generated_id", lambda *args, **kwargs: (_ for _ in ()).throw(Exception("duplicate")))
    with pytest.raises(HTTPException):
        equipment.issue_equipment(
            equipment.IssueCreate(equipment_id=1, member_id=3, issue_date="2024-03-01", quantity=1),
            make_request("/api/equipment/issue", method="POST"),
            current_user=admin_user,
            track_db=ScriptedDB(fetchone_values=[{"TotalQuantity": 3, "issued": 1}]),
            auth_db=ScriptedDB(),
        )
    with pytest.raises(HTTPException) as exc_info:
        equipment.return_equipment(
            1,
            "2024-03-02",
            make_request("/api/equipment/issue/1/return", method="PUT"),
            current_user=admin_user,
            track_db=ScriptedDB(fetchone_values=[None]),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 404


def test_medical_router_failure_and_branch_coverage(monkeypatch, admin_user, player_user):
    from app.routers import medical
    monkeypatch.setattr(medical, "write_audit_log", lambda *args, **kwargs: None)
    with pytest.raises(HTTPException) as exc_info:
        medical.get_medical_record(
            1,
            make_request("/api/medical-records/record/1"),
            current_user=player_user,
            track_db=ScriptedDB(fetchone_values=[{"RecordID": 1, "MemberID": 99, "DiagnosisDate": date(2024, 3, 1), "RecoveryDate": None}]),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 403
    result = medical.get_medical_record(
        1,
        make_request("/api/medical-records/record/1"),
        current_user=admin_user,
        track_db=ScriptedDB(fetchone_values=[{"RecordID": 1, "MemberID": 99, "DiagnosisDate": date(2024, 3, 1), "RecoveryDate": None}]),
        auth_db=ScriptedDB(),
    )
    assert result["data"]["DiagnosisDate"] == "2024-03-01"
    with pytest.raises(HTTPException) as exc_info:
        medical.get_medical_records(
            7,
            make_request("/api/medical-records/7"),
            current_user=player_user,
            track_db=ScriptedDB(),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 403
    records = medical.get_medical_records(
        1,
        make_request("/api/medical-records/1"),
        current_user=admin_user,
        track_db=ScriptedDB(fetchall_values=[[{"DiagnosisDate": date(2024, 3, 1), "RecoveryDate": date(2024, 3, 2)}]]),
        auth_db=ScriptedDB(),
    )
    assert records["data"][0]["RecoveryDate"] == "2024-03-02"
    with pytest.raises(HTTPException):
        medical.create_medical_record(
            medical.MedicalCreate(member_id=1, medical_condition="Strain", diagnosis_date="2099-03-01", recovery_date=None, status="Active"),
            make_request("/api/medical-records", method="POST"),
            current_user=admin_user,
            track_db=ScriptedDB(),
            auth_db=ScriptedDB(),
        )
    monkeypatch.setattr(medical, "insert_with_generated_id", lambda *args, **kwargs: (_ for _ in ()).throw(Exception("duplicate")))
    with pytest.raises(HTTPException):
        medical.create_medical_record(
            medical.MedicalCreate(member_id=1, medical_condition="Strain", diagnosis_date="2024-03-01", recovery_date=None, status="Active"),
            make_request("/api/medical-records", method="POST"),
            current_user=admin_user,
            track_db=ScriptedDB(),
            auth_db=ScriptedDB(),
        )
    with pytest.raises(HTTPException) as exc_info:
        medical.update_medical_record(
            1,
            medical.MedicalUpdate(),
            make_request("/api/medical-records/1", method="PUT"),
            current_user=admin_user,
            track_db=ScriptedDB(fetchone_values=[{"RecordID": 1, "DiagnosisDate": date(2024, 3, 1), "RecoveryDate": None, "Status": "Active"}]),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.detail == "No fields to update"
    with pytest.raises(HTTPException):
        medical.update_medical_record(
            1,
            medical.MedicalUpdate(diagnosis_date="2099-03-01"),
            make_request("/api/medical-records/1", method="PUT"),
            current_user=admin_user,
            track_db=ScriptedDB(fetchone_values=[{"RecordID": 1, "DiagnosisDate": date(2024, 3, 1), "RecoveryDate": None, "Status": "Active"}]),
            auth_db=ScriptedDB(),
        )
    with pytest.raises(HTTPException):
        medical.update_medical_record(
            1,
            medical.MedicalUpdate(status="Recovered"),
            make_request("/api/medical-records/1", method="PUT"),
            current_user=admin_user,
            track_db=ScriptedDB(
                fetchone_values=[{"RecordID": 1, "DiagnosisDate": date(2024, 3, 1), "RecoveryDate": None, "Status": "Active"}],
                execute_side_effects=[None, Exception("boom")],
            ),
            auth_db=ScriptedDB(),
        )
    with pytest.raises(HTTPException) as exc_info:
        medical.delete_medical_record(
            1,
            make_request("/api/medical-records/1", method="DELETE"),
            current_user=admin_user,
            track_db=ScriptedDB(fetchone_values=[None]),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 404


def test_registration_router_branch_coverage(monkeypatch, admin_user, coach_user):
    from app.routers import registration
    monkeypatch.setattr(registration, "write_audit_log", lambda *args, **kwargs: None)
    with pytest.raises(HTTPException) as exc_info:
        registration.register_team_for_tournament(
            1,
            2,
            make_request("/api/registrations/tournament/1/team/2", method="POST"),
            current_user=admin_user,
            track_db=ScriptedDB(fetchone_values=[None]),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 404
    with pytest.raises(HTTPException) as exc_info:
        registration.register_team_for_tournament(
            1,
            2,
            make_request("/api/registrations/tournament/1/team/2", method="POST"),
            current_user=admin_user,
            track_db=ScriptedDB(fetchone_values=[{"TournamentID": 1}, None]),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 404
    with pytest.raises(HTTPException) as exc_info:
        registration.register_team_for_tournament(
            1,
            2,
            make_request("/api/registrations/tournament/1/team/2", method="POST"),
            current_user=coach_user,
            track_db=ScriptedDB(fetchone_values=[{"TournamentID": 1}, {"TeamID": 2, "CoachID": 99}]),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 403
    monkeypatch.setattr(registration, "insert_with_generated_id", lambda *args, **kwargs: 17)
    result = registration.register_team_for_tournament(
        1,
        2,
        make_request("/api/registrations/tournament/1/team/2", method="POST"),
        current_user=admin_user,
        track_db=ScriptedDB(fetchone_values=[{"TournamentID": 1}, {"TeamID": 2, "CoachID": 99}, None]),
        auth_db=ScriptedDB(),
    )
    assert result["data"]["reg_id"] == 17
    with pytest.raises(HTTPException) as exc_info:
        registration.unregister_team_from_tournament(
            1,
            2,
            make_request("/api/registrations/tournament/1/team/2", method="DELETE"),
            current_user=admin_user,
            track_db=ScriptedDB(fetchone_values=[None]),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 404
    result = registration.unregister_team_from_tournament(
        1,
        2,
        make_request("/api/registrations/tournament/1/team/2", method="DELETE"),
        current_user=admin_user,
        track_db=ScriptedDB(fetchone_values=[{"TeamID": 2, "CoachID": 99}, {"RegID": 7}]),
        auth_db=ScriptedDB(),
    )
    assert result["success"] is True
    with pytest.raises(HTTPException) as exc_info:
        registration.add_team_to_event(
            1,
            2,
            make_request("/api/registrations/event/1/team/2", method="POST"),
            current_user=admin_user,
            track_db=ScriptedDB(fetchone_values=[None]),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 404
    with pytest.raises(HTTPException) as exc_info:
        registration.add_team_to_event(
            1,
            2,
            make_request("/api/registrations/event/1/team/2", method="POST"),
            current_user=admin_user,
            track_db=ScriptedDB(fetchone_values=[{"EventID": 1, "TournamentID": None, "SportID": 1}, None]),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 404
    with pytest.raises(HTTPException) as exc_info:
        registration.add_team_to_event(
            1,
            2,
            make_request("/api/registrations/event/1/team/2", method="POST"),
            current_user=coach_user,
            track_db=ScriptedDB(fetchone_values=[{"EventID": 1, "TournamentID": None, "SportID": 1}, {"TeamID": 2, "SportID": 1, "CoachID": 99}]),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 403
    with pytest.raises(HTTPException) as exc_info:
        registration.add_team_to_event(
            1,
            2,
            make_request("/api/registrations/event/1/team/2", method="POST"),
            current_user=admin_user,
            track_db=ScriptedDB(fetchone_values=[{"EventID": 1, "TournamentID": None, "SportID": 1}, {"TeamID": 2, "SportID": 9, "CoachID": 2}]),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 400
    with pytest.raises(HTTPException) as exc_info:
        registration.add_team_to_event(
            1,
            2,
            make_request("/api/registrations/event/1/team/2", method="POST"),
            current_user=admin_user,
            track_db=ScriptedDB(fetchone_values=[{"EventID": 1, "TournamentID": 4, "SportID": 1}, {"TeamID": 2, "SportID": 1, "CoachID": 2}, None]),
            auth_db=ScriptedDB(),
        )
    assert "registered for the tournament" in exc_info.value.detail
    with pytest.raises(HTTPException) as exc_info:
        registration.add_team_to_event(
            1,
            2,
            make_request("/api/registrations/event/1/team/2", method="POST"),
            current_user=admin_user,
            track_db=ScriptedDB(fetchone_values=[{"EventID": 1, "TournamentID": 4, "SportID": 1}, {"TeamID": 2, "SportID": 1, "CoachID": 2}, {"RegID": 1}, {"ParticipationID": 8}]),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 409
    monkeypatch.setattr(registration, "insert_with_generated_id", lambda *args, **kwargs: 23)
    result = registration.add_team_to_event(
        1,
        2,
        make_request("/api/registrations/event/1/team/2", method="POST"),
        current_user=admin_user,
        track_db=ScriptedDB(fetchone_values=[{"EventID": 1, "TournamentID": None, "SportID": 1}, {"TeamID": 2, "SportID": 1, "CoachID": 2}, None]),
        auth_db=ScriptedDB(),
    )
    assert result["data"]["participation_id"] == 23
    with pytest.raises(HTTPException) as exc_info:
        registration.remove_team_from_event(
            1,
            2,
            make_request("/api/registrations/event/1/team/2", method="DELETE"),
            current_user=coach_user,
            track_db=ScriptedDB(fetchone_values=[None]),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 404
    with pytest.raises(HTTPException) as exc_info:
        registration.remove_team_from_event(
            1,
            2,
            make_request("/api/registrations/event/1/team/2", method="DELETE"),
            current_user=admin_user,
            track_db=ScriptedDB(fetchone_values=[None]),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 404
    result = registration.remove_team_from_event(
        1,
        2,
        make_request("/api/registrations/event/1/team/2", method="DELETE"),
        current_user=coach_user,
        track_db=ScriptedDB(fetchone_values=[{"CoachID": 2}, {"ParticipationID": 9}]),
        auth_db=ScriptedDB(),
    )
    assert result["success"] is True


def test_events_router_branch_coverage(monkeypatch, admin_user, coach_user):
    from app.routers import events
    monkeypatch.setattr(events, "write_audit_log", lambda *args, **kwargs: None)
    result = events.list_events(
        make_request("/api/events"),
        current_user=admin_user,
        track_db=ScriptedDB(fetchall_values=[[{"EventDate": date(2024, 3, 1), "StartTime": "10:00:00", "EndTime": "11:00:00"}]]),
        auth_db=ScriptedDB(),
        tournament_id=1,
        sport_id=2,
        status="Scheduled",
    )
    assert result["data"][0]["EventDate"] == "2024-03-01"
    result = events.get_event_form_options(
        make_request("/api/events/lookups"),
        current_user=admin_user,
        track_db=ScriptedDB(fetchall_values=[[{"SportID": 1}], [{"VenueID": 1}], [{"TournamentID": 1}]]),
        auth_db=ScriptedDB(),
    )
    assert result["data"]["sports"] == [{"SportID": 1}]
    result = events.get_event(
        1,
        make_request("/api/events/1"),
        current_user=coach_user,
        track_db=ScriptedDB(
            fetchone_values=[{"EventID": 1, "TournamentID": None, "SportID": 1, "EventDate": date(2024, 3, 1), "StartTime": "10:00:00", "EndTime": "11:00:00"}],
            fetchall_values=[
                [{"TeamID": 2, "TeamName": "Sprinters", "CoachID": 2}],
                [{"TeamID": 2, "TeamName": "Sprinters", "CoachID": 2}, {"TeamID": 3, "TeamName": "Comets", "CoachID": 2}],
            ],
        ),
        auth_db=ScriptedDB(),
    )
    assert result["data"]["eligible_teams"] == [{"TeamID": 3, "TeamName": "Comets", "CoachID": 2}]
    with pytest.raises(HTTPException) as exc_info:
        events.create_event(
            events.EventCreate(event_name="Final", event_date="2024-03-01", start_time="11:00", end_time="10:00", venue_id=1, sport_id=1, status="Scheduled"),
            make_request("/api/events", method="POST"),
            current_user=admin_user,
            track_db=ScriptedDB(),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 400
    monkeypatch.setattr(events, "insert_with_generated_id", lambda *args, **kwargs: (_ for _ in ()).throw(Exception("duplicate")))
    with pytest.raises(HTTPException) as exc_info:
        events.create_event(
            events.EventCreate(event_name="Final", event_date="2024-03-01", start_time="10:00", end_time="11:00", venue_id=1, sport_id=1, status="Scheduled"),
            make_request("/api/events", method="POST"),
            current_user=admin_user,
            track_db=ScriptedDB(),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 400
    with pytest.raises(HTTPException) as exc_info:
        events.update_event(
            1,
            events.EventUpdate(),
            make_request("/api/events/1", method="PUT"),
            current_user=admin_user,
            track_db=ScriptedDB(fetchone_values=[{"EventID": 1, "EventDate": date(2024, 3, 1), "StartTime": "10:00:00", "EndTime": "11:00:00"}]),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.detail == "No fields to update"
    with pytest.raises(HTTPException) as exc_info:
        events.update_event(
            1,
            events.EventUpdate(start_time="11:00", end_time="10:00"),
            make_request("/api/events/1", method="PUT"),
            current_user=admin_user,
            track_db=ScriptedDB(fetchone_values=[{"EventID": 1, "EventDate": date(2024, 3, 1), "StartTime": "10:00:00", "EndTime": "11:00:00"}]),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 400
    with pytest.raises(HTTPException) as exc_info:
        events.update_event(
            1,
            events.EventUpdate(status="Cancelled"),
            make_request("/api/events/1", method="PUT"),
            current_user=admin_user,
            track_db=ScriptedDB(fetchone_values=[{"EventID": 1, "EventDate": date(2024, 3, 1), "StartTime": "10:00:00", "EndTime": "11:00:00"}], execute_side_effects=[None, Exception("boom")]),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 400
    with pytest.raises(HTTPException) as exc_info:
        events.delete_event(
            1,
            make_request("/api/events/1", method="DELETE"),
            current_user=admin_user,
            track_db=ScriptedDB(fetchone_values=[None]),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 404


def test_members_router_branch_coverage(monkeypatch, admin_user, coach_user, player_user):
    from app.routers import members
    monkeypatch.setattr(members, "write_audit_log", lambda *args, **kwargs: None)
    assert members._can_view_member(coach_user, "Player", 8) is True
    assert members._can_view_member(coach_user, "Admin", 8) is False
    player_list = members.list_members(
        make_request("/api/members"),
        current_user=player_user,
        track_db=ScriptedDB(fetchall_values=[[{"MemberID": 3, "Role": "Player", "JoinDate": date(2024, 3, 1)}]]),
        auth_db=ScriptedDB(),
    )
    coach_list = members.list_members(
        make_request("/api/members"),
        current_user=coach_user,
        track_db=ScriptedDB(fetchall_values=[[{"MemberID": 2, "Role": "Coach", "JoinDate": date(2024, 3, 1)}]]),
        auth_db=ScriptedDB(),
    )
    assert player_list["data"][0]["JoinDate"] == "2024-03-01"
    assert coach_list["data"][0]["JoinDate"] == "2024-03-01"
    with pytest.raises(HTTPException) as exc_info:
        members.get_my_profile(
            make_request("/api/members/me"),
            current_user=admin_user,
            track_db=ScriptedDB(fetchone_values=[None]),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 404
    create_result = members.create_member(
        members.MemberCreate(
            name="Ada123",
            age=20,
            email="ada@example.com",
            contact_number="+911234567890",
            gender="F",
            role="Player",
            join_date="2024-03-01",
            username="ada",
            password="secret",
        ),
        make_request("/api/members", method="POST"),
        current_user=admin_user,
        cross_db=ScriptedDB(),
    )
    assert create_result["success"] is False
    with pytest.raises(HTTPException) as exc_info:
        members.update_member(
            7,
            members.MemberUpdate(name="Ada123"),
            make_request("/api/members/7", method="PUT"),
            current_user=admin_user,
            track_db=ScriptedDB(fetchone_values=[{"MemberID": 7}]),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 400
    with pytest.raises(HTTPException) as exc_info:
        members.update_member(
            7,
            members.MemberUpdate(contact_number="+999999"),
            make_request("/api/members/7", method="PUT"),
            current_user=admin_user,
            track_db=ScriptedDB(fetchone_values=[{"MemberID": 7}]),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 400
    with pytest.raises(HTTPException) as exc_info:
        members.update_member(
            7,
            members.MemberUpdate(email="ada@example.com"),
            make_request("/api/members/7", method="PUT"),
            current_user=admin_user,
            track_db=ScriptedDB(fetchone_values=[{"MemberID": 7}], execute_side_effects=[None, Exception("boom")]),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 400


def test_remaining_validation_performance_team_and_medical_branches(monkeypatch, admin_user, coach_user, player_user):
    from app.routers import medical, members, performance, teams
    from app.services import validation
    monkeypatch.setattr(performance, "write_audit_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(teams, "write_audit_log", lambda *args, **kwargs: None)
    with pytest.raises(ValueError):
        validation.normalize_contact_number("+123456789")
    assert validation.split_contact_number("+123456789") == ("+91", "123456789")
    assert validation.derive_medical_status("Active", date(2024, 3, 1), date(2099, 3, 1)) == "Active"
    assert "same unique value" in validation.humanize_db_error(Exception("Duplicate entry 'x' for key 'uq_generic'"))
    assert validation.humanize_db_error(Exception("check constraint other")) == "One of the provided values is invalid."
    assert validation.humanize_db_error(Exception("FOREIGN KEY CONSTRAINT FAILS")) == "One of the selected related records is invalid."
    with pytest.raises(HTTPException) as exc_info:
        performance.update_performance_log(
            1,
            performance.PerfLogUpdate(metric_name="Speed"),
            make_request("/api/performance-logs/1", method="PUT"),
            current_user=admin_user,
            track_db=ScriptedDB(fetchone_values=[None]),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 404
    result = performance.update_performance_log(
        1,
        performance.PerfLogUpdate(metric_name="Speed"),
        make_request("/api/performance-logs/1", method="PUT"),
        current_user=coach_user,
        track_db=ScriptedDB(fetchone_values=[{"LogID": 1, "MemberID": 2}, {"ok": 1}]),
        auth_db=ScriptedDB(),
    )
    assert result["success"] is True
    result = performance.delete_performance_log(
        1,
        make_request("/api/performance-logs/1", method="DELETE"),
        current_user=coach_user,
        track_db=ScriptedDB(fetchone_values=[{"LogID": 1, "MemberID": 2}, {"ok": 1}]),
        auth_db=ScriptedDB(),
    )
    assert result["success"] is True
    with pytest.raises(HTTPException) as exc_info:
        medical.update_medical_record(
            1,
            medical.MedicalUpdate(status="Active"),
            make_request("/api/medical-records/1", method="PUT"),
            current_user=admin_user,
            track_db=ScriptedDB(fetchone_values=[None]),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 404
    create_result = teams.create_team(
        teams.TeamCreate(team_id=33, team_name="Assigned", sport_id=1, formed_date="2024-01-01", coach_id=2),
        make_request("/api/teams", method="POST"),
        current_user=coach_user,
        track_db=ScriptedDB(fetchone_values=[{"Role": "Coach"}, {"SportID": 1}], fetchall_values=[[]]),
        auth_db=ScriptedDB(),
    )
    assert create_result["data"]["team_id"] == 33
    with pytest.raises(HTTPException) as exc_info:
        teams.create_team(
            teams.TeamCreate(team_name="Broken", sport_id=1, formed_date="2024-01-01"),
            make_request("/api/teams", method="POST"),
            current_user=admin_user,
            track_db=ScriptedDB(fetchone_values=[{"SportID": 1}, {"nid": 44}], fetchall_values=[[]], execute_side_effects=[None, None, Exception("boom")]),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 400
    with pytest.raises(HTTPException) as exc_info:
        teams.update_team(
            1,
            teams.TeamUpdate(team_name="Sprinters"),
            make_request("/api/teams/1", method="PUT"),
            current_user=coach_user,
            track_db=ScriptedDB(fetchone_values=[{"TeamID": 1, "CoachID": 99, "CaptainID": 1, "FormedDate": "2024-01-01"}]),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 403
    result = teams.update_team(
        1,
        teams.TeamUpdate(captain_id=None),
        make_request("/api/teams/1", method="PUT"),
        current_user=admin_user,
        track_db=ScriptedDB(fetchone_values=[{"TeamID": 1, "CoachID": 2, "CaptainID": 1, "FormedDate": "2024-01-01"}]),
        auth_db=ScriptedDB(),
    )
    assert result["success"] is True
    with pytest.raises(HTTPException) as exc_info:
        teams.delete_team(
            1,
            make_request("/api/teams/1", method="DELETE"),
            current_user=coach_user,
            track_db=ScriptedDB(fetchone_values=[{"TeamID": 1, "CoachID": 99}]),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 403
    no_join = members.list_members(
        make_request("/api/members"),
        current_user=player_user,
        track_db=ScriptedDB(fetchall_values=[[{"MemberID": 3, "Role": "Player", "JoinDate": None}]]),
        auth_db=ScriptedDB(),
    )
    assert no_join["data"][0]["JoinDate"] is None


def test_ui_remaining_route_branches(monkeypatch, admin_user, coach_user, player_user):
    from app.ui import routes as ui_routes
    monkeypatch.setattr(ui_routes, "api_list_members", lambda *args, **kwargs: {"success": False, "data": []})
    assert ui_routes.members_list(make_request("/ui/members"), admin_user, GuardDB(allowed_substrings=()), GuardDB(allowed_substrings=())) is None
    monkeypatch.setattr(
        ui_routes,
        "api_get_member_portfolio",
        lambda *args, **kwargs: {
            "success": True,
            "role": "Player",
            "data": {
                "member": {
                    "MemberID": 1,
                    "Name": "Ada Runner",
                    "Role": "Player",
                    "Gender": "F",
                    "Age": 23,
                    "Email": "ada@example.com",
                    "ContactNumber": "+911234567890",
                    "JoinDate": date(2024, 3, 1),
                },
                "teams": [],
                "performance": [{"RecordDate": date(2024, 3, 2)}],
                "medical": [
                    {"DiagnosisDate": date(2024, 3, 3), "RecoveryDate": date(2024, 3, 4)},
                    {"DiagnosisDate": date(2024, 3, 5), "RecoveryDate": None},
                ],
            },
        },
    )
    response = ui_routes.member_portfolio(
        1,
        make_request("/ui/members/1"),
        admin_user,
        GuardDB(allowed_substrings=("SELECT SportID, SportName FROM Sport ORDER BY SportName",), fetchall_values=[[{"SportID": 1, "SportName": "Track"}]]),
        GuardDB(allowed_substrings=()),
    )
    assert response.status_code == 200
    monkeypatch.setattr(ui_routes, "api_update_member", lambda *args, **kwargs: {"success": True})
    response = ui_routes.member_edit_submit(
        1,
        make_request("/ui/members/1/edit", method="POST"),
        admin_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
        name="Ada Runner",
        email="ada@example.com",
        age=24,
        contact_number=123,
    )
    assert response.headers["location"] == "/ui/members/1"
    monkeypatch.setattr(ui_routes, "api_delete_member", _raise_http(400, "cannot delete"))
    response = ui_routes.member_delete(1, make_request("/ui/members/1/delete", method="POST"), admin_user, GuardDB(allowed_substrings=()), GuardDB(allowed_substrings=()))
    assert response.headers["location"] == "/ui/members"
    monkeypatch.setattr(ui_routes, "api_list_teams", lambda *args, **kwargs: {"success": True, "data": [{"TeamID": 1, "FormedDate": None}]})
    response = ui_routes.teams_list(make_request("/ui/teams"), admin_user, GuardDB(allowed_substrings=()), GuardDB(allowed_substrings=()))
    assert response.status_code == 200
    response = asyncio.run(
        ui_routes.team_create(
            make_request("/ui/teams/new", method="POST", form_data={}),
            player_user,
            GuardDB(allowed_substrings=()),
            GuardDB(allowed_substrings=()),
        )
    )
    assert response.headers["location"] == "/ui/teams"
    response = asyncio.run(
        ui_routes.team_create(
            make_request("/ui/teams/new", method="POST", form_data={"team_name": "Sprinters", "sport_id": "x", "coach_id": "", "captain_id": "", "formed_date": "2024-01-01", "member_ids": ["1"]}),
            admin_user,
            ScriptedDB(fetchall_values=[[]]),
            GuardDB(allowed_substrings=()),
        )
    )
    assert response.status_code == 200
    monkeypatch.setattr(ui_routes, "api_create_team", _raise_http(400, "bad team"))
    response = asyncio.run(
        ui_routes.team_create(
            make_request("/ui/teams/new", method="POST", form_data={"team_name": "Sprinters", "sport_id": "1", "coach_id": "", "captain_id": "", "formed_date": "2024-01-01", "member_ids": ["1"]}),
            admin_user,
            ScriptedDB(fetchall_values=[[]]),
            GuardDB(allowed_substrings=()),
        )
    )
    assert response.status_code == 200
    response = ui_routes.team_edit_form(1, make_request("/ui/teams/1/edit"), player_user, GuardDB(allowed_substrings=()), GuardDB(allowed_substrings=()))
    assert response.headers["location"] == "/ui/teams/1"
    monkeypatch.setattr(
        ui_routes,
        "api_get_team",
        lambda *args, **kwargs: {"success": True, "data": {"team": {"TeamID": 1, "TeamName": "Sprinters", "SportID": 1, "CoachID": 2, "CaptainID": 1, "FormedDate": None}, "roster": []}},
    )
    response = ui_routes.team_edit_form(1, make_request("/ui/teams/1/edit"), admin_user, ScriptedDB(fetchall_values=[[]]), GuardDB(allowed_substrings=()))
    assert response.status_code == 200
    response = asyncio.run(
        ui_routes.team_edit_submit(
            1,
            make_request("/ui/teams/1/edit", method="POST", form_data={}),
            player_user,
            GuardDB(allowed_substrings=()),
            GuardDB(allowed_substrings=()),
        )
    )
    assert response.headers["location"] == "/ui/teams/1"
    monkeypatch.setattr(ui_routes, "api_delete_team", _raise_http(400, "cannot delete"))
    response = ui_routes.team_delete(1, make_request("/ui/teams/1/delete", method="POST"), admin_user, GuardDB(allowed_substrings=()), GuardDB(allowed_substrings=()))
    assert response.headers["location"] == "/ui/teams/1"
    monkeypatch.setattr(ui_routes, "api_get_team", _raise_http(404, "missing"))
    response = ui_routes.team_detail(1, make_request("/ui/teams/1"), admin_user, GuardDB(allowed_substrings=()), GuardDB(allowed_substrings=()))
    assert response.headers["location"] == "/ui/teams"
    monkeypatch.setattr(
        ui_routes,
        "api_get_team",
        lambda *args, **kwargs: {
            "success": True,
            "data": {
                "team": {"TeamID": 1, "FormedDate": None},
                "roster": [{"MemberID": 1, "JoinDate": None}],
                "events": [
                    {"EventDate": date(2024, 3, 1), "StartTime": "10:00:00", "EndTime": "11:00:00"},
                    {"EventDate": None, "StartTime": None, "EndTime": None},
                ],
            },
        },
    )
    response = ui_routes.team_detail(1, make_request("/ui/teams/1"), admin_user, GuardDB(allowed_substrings=()), GuardDB(allowed_substrings=()))
    assert response.status_code == 200
    response = ui_routes.tournament_create(
        make_request("/ui/tournaments/new", method="POST"),
        coach_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
        tournament_name="Open Meet",
        start_date="2024-03-01",
        end_date="2024-03-02",
        description=None,
        status="Upcoming",
    )
    assert response.headers["location"] == "/ui/tournaments"
    monkeypatch.setattr(ui_routes, "api_get_tournament", _raise_http(404, "missing"))
    response = ui_routes.tournament_detail(1, make_request("/ui/tournaments/1"), admin_user, GuardDB(allowed_substrings=()), GuardDB(allowed_substrings=()))
    assert response.headers["location"] == "/ui/tournaments"
    response = ui_routes.tournament_edit_form(1, make_request("/ui/tournaments/1/edit"), coach_user)
    assert response.headers["location"] == "/ui/tournaments/1"
    monkeypatch.setattr(ui_routes, "api_list_equipment", lambda *args, **kwargs: {"success": True, "data": []})
    monkeypatch.setattr(ui_routes, "api_list_issues", lambda *args, **kwargs: {"success": True, "data": []})
    response = ui_routes.equipment_list(make_request("/ui/equipment"), player_user, GuardDB(allowed_substrings=()), GuardDB(allowed_substrings=()))
    assert response.status_code == 200


def test_remaining_team_router_branches(monkeypatch, admin_user, coach_user):
    from app.routers import teams
    monkeypatch.setattr(teams, "write_audit_log", lambda *args, **kwargs: None)
    original_sync_team_members = teams._sync_team_members
    with pytest.raises(HTTPException) as exc_info:
        teams._get_team_or_404(ScriptedDB(fetchone_values=[None]), 1)
    assert exc_info.value.status_code == 404
    with pytest.raises(HTTPException) as exc_info:
        teams._validate_team_fields(ScriptedDB(fetchone_values=[None]), sport_id=1)
    assert exc_info.value.status_code == 400
    monkeypatch.setattr(teams, "_sync_team_members", lambda *args, **kwargs: (_ for _ in ()).throw(HTTPException(status_code=400, detail="sync failed")))
    with pytest.raises(HTTPException) as exc_info:
        teams.create_team(
            teams.TeamCreate(team_name="Sprinters", sport_id=1, formed_date="2024-01-01"),
            make_request("/api/teams", method="POST"),
            current_user=admin_user,
            track_db=ScriptedDB(fetchone_values=[{"SportID": 1}, {"nid": 22}], fetchall_values=[[]]),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.detail == "sync failed"
    monkeypatch.setattr(teams, "_sync_team_members", original_sync_team_members)
    result = teams.update_team(
        1,
        teams.TeamUpdate(members=[teams.TeamMemberEntry(member_id=1)]),
        make_request("/api/teams/1", method="PUT"),
        current_user=admin_user,
        track_db=ScriptedDB(
            fetchone_values=[{"TeamID": 1, "CoachID": 2, "CaptainID": 1, "FormedDate": "2024-01-01"}],
            fetchall_values=[
                [{"MemberID": 1, "Role": "Player"}],
                [],
            ],
        ),
        auth_db=ScriptedDB(),
    )
    assert result["success"] is True


def test_last_realistic_validation_and_allowed_branch_coverage(monkeypatch, admin_user, coach_user):
    from app.routers import equipment, performance
    from app.services import validation
    monkeypatch.setattr(equipment, "write_audit_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(performance, "write_audit_log", lambda *args, **kwargs: None)
    assert validation.derive_tournament_status(date.today(), date.today()) == "Ongoing"
    assert validation.split_contact_number("+1") == ("+91", "1")
    result = performance.get_performance_log(
        1,
        make_request("/api/performance-logs/1"),
        current_user=coach_user,
        track_db=ScriptedDB(fetchone_values=[{"LogID": 1, "MemberID": 2, "SportID": 1, "RecordDate": date(2024, 3, 1)}, {"ok": 1}]),
        auth_db=ScriptedDB(),
    )
    assert result["data"]["RecordDate"] == "2024-03-01"
    result = equipment.return_equipment(
        1,
        "2024-03-02",
        make_request("/api/equipment/issue/1/return", method="PUT"),
        current_user=coach_user,
        track_db=ScriptedDB(fetchone_values=[{"IssueID": 1, "MemberID": 2}, {"ok": 1}]),
        auth_db=ScriptedDB(),
    )
    assert result["success"] is True


def test_final_uncovered_branch_paths(monkeypatch, admin_user, player_user):
    from app.routers import admin, events, members
    from app.services import audit, validation
    admin_result = admin.get_audit_log(
        limit=2,
        current_user=admin_user,
        db=ScriptedDB(fetchall_values=[[{"log_id": 1, "timestamp": None}, {"log_id": 2, "timestamp": datetime(2024, 3, 1, 10, 0, 0)}]]),
    )
    assert admin_result["data"][0]["timestamp"] is None
    assert admin_result["data"][1]["timestamp"] == "2024-03-01 10:00:00"
    event_result = events.get_event(
        1,
        make_request("/api/events/1"),
        current_user=admin_user,
        track_db=ScriptedDB(
            fetchone_values=[{"EventID": 1, "TournamentID": None, "SportID": 1, "EventDate": None, "StartTime": None, "EndTime": None}],
            fetchall_values=[[], []],
        ),
        auth_db=ScriptedDB(),
    )
    assert event_result["data"]["event"]["EventDate"] is None
    monkeypatch.setattr(members, "_can_view_member", lambda *args, **kwargs: True)
    portfolio = members.get_member_portfolio(
        7,
        make_request("/api/members/7"),
        current_user=player_user,
        track_db=ScriptedDB(
            fetchone_values=[{"MemberID": 7, "Role": "Player"}],
            fetchall_values=[[], [], []],
        ),
        auth_db=ScriptedDB(),
    )
    assert portfolio["data"]["performance"] == []
    original_fullmatch = validation.re.fullmatch
    def fake_fullmatch(pattern, value):
        if pattern == r"\+\d{11,14}":
            return True
        if pattern == r"\d{10}":
            return False
        return original_fullmatch(pattern, value)
    monkeypatch.setattr(validation.re, "fullmatch", fake_fullmatch)
    with pytest.raises(ValueError):
        validation.normalize_contact_number("+1")
    with pytest.raises(ValueError):
        validation.normalize_contact_number("+1234567890")
    logger = logging.getLogger("audit")
    if not logger.handlers:
        logger.addHandler(logging.NullHandler())
    reloaded_audit = importlib.reload(audit)
    assert reloaded_audit._file_logger.handlers
