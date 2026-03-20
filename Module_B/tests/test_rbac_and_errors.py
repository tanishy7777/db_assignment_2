"""RBAC enforcement and error-path tests.

RBAC is tested two ways:
  1. require_role() dependency called directly via asyncio.run — verifies the
     access-control layer in isolation.
  2. Inline 403 checks inside handler functions called directly — verifies the
     per-function authorization logic (e.g. "Player can only update themselves").
Error paths cover: invalid input, missing resources (404), insufficient stock,
and duplicate/validation failures surfaced through the handler.
"""

from __future__ import annotations
import asyncio
import pytest
from fastapi import HTTPException
from .conftest import ScriptedDB, make_request


def test_require_admin_blocks_player(player_user):
    from app.auth.dependencies import require_admin
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(require_admin(current_user=player_user))
    assert exc_info.value.status_code == 403


def test_require_admin_blocks_coach(coach_user):
    from app.auth.dependencies import require_admin
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(require_admin(current_user=coach_user))
    assert exc_info.value.status_code == 403


def test_require_admin_allows_admin(admin_user):
    from app.auth.dependencies import require_admin
    result = asyncio.run(require_admin(current_user=admin_user))
    assert result["role"] == "Admin"


def test_require_admin_or_coach_blocks_player(player_user):
    from app.auth.dependencies import require_admin_or_coach
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(require_admin_or_coach(current_user=player_user))
    assert exc_info.value.status_code == 403


def test_require_admin_or_coach_allows_coach(coach_user):
    from app.auth.dependencies import require_admin_or_coach
    result = asyncio.run(require_admin_or_coach(current_user=coach_user))
    assert result["role"] == "Coach"


def test_require_admin_or_coach_allows_admin(admin_user):
    from app.auth.dependencies import require_admin_or_coach
    result = asyncio.run(require_admin_or_coach(current_user=admin_user))
    assert result["role"] == "Admin"


def test_update_member_non_admin_updating_other_member_is_403(coach_user, monkeypatch):
    from app.routers import members
    monkeypatch.setattr(members, "write_audit_log", lambda *a, **kw: None)
    with pytest.raises(HTTPException) as exc_info:
        members.update_member(
            1,
            members.MemberUpdate(name="Hacker"),
            make_request("/api/members/1", method="PUT"),
            current_user=coach_user,
            track_db=ScriptedDB(),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 403


def test_update_member_player_updating_own_profile_is_allowed(player_user, monkeypatch):
    from app.routers import members
    monkeypatch.setattr(members, "write_audit_log", lambda *a, **kw: None)
    result = members.update_member(
        3,
        members.MemberUpdate(name="Self Update"),
        make_request("/api/members/3", method="PUT"),
        current_user=player_user,
        track_db=ScriptedDB(fetchone_values=[{"MemberID": 3, "Role": "Player"}]),
        auth_db=ScriptedDB(),
    )
    assert result["success"] is True


def test_get_member_portfolio_player_viewing_admin_is_403(player_user, monkeypatch):
    from app.routers import members
    monkeypatch.setattr(members, "write_audit_log", lambda *a, **kw: None)
    with pytest.raises(HTTPException) as exc_info:
        members.get_member_portfolio(
            1,
            make_request("/api/members/1"),
            current_user=player_user,
            track_db=ScriptedDB(fetchone_values=[{"MemberID": 1, "Role": "Admin"}]),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 403


def test_get_member_portfolio_player_viewing_own_is_allowed(player_user, monkeypatch):
    from app.routers import members
    monkeypatch.setattr(members, "write_audit_log", lambda *a, **kw: None)
    result = members.get_member_portfolio(
        3,
        make_request("/api/members/3"),
        current_user=player_user,
        track_db=ScriptedDB(
            fetchone_values=[{"MemberID": 3, "Role": "Player"}],
            fetchall_values=[[], [], []],
        ),
        auth_db=ScriptedDB(),
    )
    assert result["success"] is True
    assert result["data"]["member"]["MemberID"] == 3


def test_get_member_portfolio_coach_viewing_player_has_no_medical(coach_user, monkeypatch):
    """Coach viewing another player's portfolio receives an empty medical list."""
    from app.routers import members
    monkeypatch.setattr(members, "write_audit_log", lambda *a, **kw: None)
    track_db = ScriptedDB(
        fetchone_values=[{"MemberID": 1, "Role": "Player"}],
        fetchall_values=[[], []],
    )
    result = members.get_member_portfolio(
        1,
        make_request("/api/members/1"),
        current_user=coach_user,
        track_db=track_db,
        auth_db=ScriptedDB(),
    )
    assert result["data"]["medical"] == []
    assert all("MedicalRecord" not in query for query, _ in track_db.executed)


def test_get_medical_record_player_accessing_other_member_is_403(player_user, monkeypatch):
    from app.routers import medical
    monkeypatch.setattr(medical, "write_audit_log", lambda *a, **kw: None)
    with pytest.raises(HTTPException) as exc_info:
        medical.get_medical_record(
            6,
            make_request("/api/medical-records/record/6"),
            current_user=player_user,
            track_db=ScriptedDB(
                fetchone_values=[{"RecordID": 6, "MemberID": 1, "DiagnosisDate": "2024-03-01"}]
            ),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 403


def test_create_member_age_zero_returns_failure(admin_user, monkeypatch):
    from app.routers import members
    monkeypatch.setattr(members, "write_audit_log", lambda *a, **kw: None)
    track_db = ScriptedDB()
    auth_db = ScriptedDB()
    result = members.create_member(
        members.MemberCreate(
            member_id=99, name="X", age=0, email="x@e.com",
            contact_number="555", gender="M", role="Player",
            join_date="2024-01-01", username="x", password="x",
        ),
        make_request("/api/members", method="POST"),
        current_user=admin_user,
        track_db=track_db,
        auth_db=auth_db,
    )
    assert result["success"] is False
    assert "age" in result["message"].lower()
    assert track_db.executed == []
    assert auth_db.executed == []


def test_create_member_duplicate_member_id_returns_failure(admin_user, monkeypatch):
    from app.routers import members
    monkeypatch.setattr(members, "write_audit_log", lambda *a, **kw: None)
    result = members.create_member(
        members.MemberCreate(
            member_id=1, name="Dup", age=20, email="dup@e.com",
            contact_number="555", gender="M", role="Player",
            join_date="2024-01-01", username="dup", password="x",
        ),
        make_request("/api/members", method="POST"),
        current_user=admin_user,
        track_db=ScriptedDB(
            execute_side_effects=[Exception("Duplicate entry '1' for key 'PRIMARY'")]
        ),
        auth_db=ScriptedDB(),
    )
    assert result["success"] is False
    assert "Duplicate" in result["message"]


def test_create_member_duplicate_username_returns_failure(admin_user, monkeypatch):
    from app.routers import members
    monkeypatch.setattr(members, "write_audit_log", lambda *a, **kw: None)
    result = members.create_member(
        members.MemberCreate(
            member_id=50, name="New", age=22, email="new@e.com",
            contact_number="555", gender="M", role="Player",
            join_date="2024-01-01", username="existing_user", password="x",
        ),
        make_request("/api/members", method="POST"),
        current_user=admin_user,
        track_db=ScriptedDB(),
        auth_db=ScriptedDB(
            execute_side_effects=[Exception("Duplicate entry 'existing_user' for key 'username'")]
        ),
    )
    assert result["success"] is False
    assert "existing_user" in result["message"]


def test_update_member_no_fields_is_400(admin_user, monkeypatch):
    from app.routers import members
    monkeypatch.setattr(members, "write_audit_log", lambda *a, **kw: None)
    track_db = ScriptedDB(fetchone_values=[{"MemberID": 1, "Role": "Player"}])
    with pytest.raises(HTTPException) as exc_info:
        members.update_member(
            1,
            members.MemberUpdate(),
            make_request("/api/members/1", method="PUT"),
            current_user=admin_user,
            track_db=track_db,
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "No fields to update"
    assert all("UPDATE Member" not in query for query, _ in track_db.executed)


def test_update_member_negative_age_is_400(admin_user, monkeypatch):
    from app.routers import members
    monkeypatch.setattr(members, "write_audit_log", lambda *a, **kw: None)
    track_db = ScriptedDB(fetchone_values=[{"MemberID": 1, "Role": "Player"}])
    with pytest.raises(HTTPException) as exc_info:
        members.update_member(
            1,
            members.MemberUpdate(age=-1),
            make_request("/api/members/1", method="PUT"),
            current_user=admin_user,
            track_db=track_db,
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 400
    assert "positive" in exc_info.value.detail.lower()
    assert all("UPDATE Member" not in query for query, _ in track_db.executed)


def test_get_team_not_found_is_404(admin_user, monkeypatch):
    from app.routers import teams
    monkeypatch.setattr(teams, "write_audit_log", lambda *a, **kw: None)
    with pytest.raises(HTTPException) as exc_info:
        teams.get_team(
            999,
            make_request("/api/teams/999"),
            current_user=admin_user,
            track_db=ScriptedDB(fetchone_values=[None]),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 404


def test_create_team_invalid_coach_is_400(admin_user, monkeypatch):
    from app.routers import teams
    monkeypatch.setattr(teams, "write_audit_log", lambda *a, **kw: None)
    with pytest.raises(HTTPException) as exc_info:
        teams.create_team(
            teams.TeamCreate(
                team_name="X", sport_id=1, formed_date="2024-01-01", coach_id=99
            ),
            make_request("/api/teams", method="POST"),
            current_user=admin_user,
            track_db=ScriptedDB(fetchone_values=[{"Role": "Player"}]),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 400
    assert "coach" in exc_info.value.detail.lower()


def test_create_team_future_formed_date_is_400(admin_user, monkeypatch):
    from app.routers import teams
    monkeypatch.setattr(teams, "write_audit_log", lambda *a, **kw: None)
    with pytest.raises(HTTPException) as exc_info:
        teams.create_team(
            teams.TeamCreate(
                team_name="X", sport_id=1, formed_date="2099-12-31", coach_id=2
            ),
            make_request("/api/teams", method="POST"),
            current_user=admin_user,
            track_db=ScriptedDB(fetchone_values=[{"Role": "Coach"}]),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 400
    assert "future" in exc_info.value.detail.lower()


def test_create_team_duplicate_member_ids_is_400(admin_user, monkeypatch):
    from app.routers import teams
    monkeypatch.setattr(teams, "write_audit_log", lambda *a, **kw: None)
    with pytest.raises(HTTPException) as exc_info:
        teams.create_team(
            teams.TeamCreate(
                team_name="X", sport_id=1, formed_date="2024-01-01", member_ids=[1, 1]
            ),
            make_request("/api/teams", method="POST"),
            current_user=admin_user,
            track_db=ScriptedDB(fetchone_values=[{"SportID": 1}]),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 400
    assert "duplicate" in exc_info.value.detail.lower()


def test_create_team_captain_not_in_members_is_400(admin_user, monkeypatch):
    from app.routers import teams
    monkeypatch.setattr(teams, "write_audit_log", lambda *a, **kw: None)
    with pytest.raises(HTTPException) as exc_info:
        teams.create_team(
            teams.TeamCreate(
                team_name="X", sport_id=1, formed_date="2024-01-01",
                member_ids=[1, 2], captain_id=99,
            ),
            make_request("/api/teams", method="POST"),
            current_user=admin_user,
            track_db=ScriptedDB(fetchone_values=[{"SportID": 1}]),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 400
    assert "captain" in exc_info.value.detail.lower()


def test_issue_equipment_zero_quantity_is_400(admin_user, monkeypatch):
    from app.routers import equipment
    monkeypatch.setattr(equipment, "write_audit_log", lambda *a, **kw: None)
    track_db = ScriptedDB()
    with pytest.raises(HTTPException) as exc_info:
        equipment.issue_equipment(
            equipment.IssueCreate(equipment_id=1, member_id=1, issue_date="2024-03-01", quantity=0),
            make_request("/api/equipment/issue", method="POST"),
            current_user=admin_user,
            track_db=track_db,
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Quantity must be a positive number."
    assert track_db.executed == []


def test_issue_equipment_not_found_is_404(admin_user, monkeypatch):
    from app.routers import equipment
    monkeypatch.setattr(equipment, "write_audit_log", lambda *a, **kw: None)
    with pytest.raises(HTTPException) as exc_info:
        equipment.issue_equipment(
            equipment.IssueCreate(equipment_id=999, member_id=1, issue_date="2024-03-01", quantity=1),
            make_request("/api/equipment/issue", method="POST"),
            current_user=admin_user,
            track_db=ScriptedDB(fetchone_values=[None]),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Equipment not found."


def test_issue_equipment_insufficient_stock_is_400(admin_user, monkeypatch):
    from app.routers import equipment
    monkeypatch.setattr(equipment, "write_audit_log", lambda *a, **kw: None)
    with pytest.raises(HTTPException) as exc_info:
        equipment.issue_equipment(
            equipment.IssueCreate(equipment_id=1, member_id=1, issue_date="2024-03-01", quantity=10),
            make_request("/api/equipment/issue", method="POST"),
            current_user=admin_user,
            track_db=ScriptedDB(fetchone_values=[{"TotalQuantity": 5, "issued": 4}]),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 400
    assert "available" in exc_info.value.detail.lower()


def test_return_equipment_not_found_is_404(admin_user, monkeypatch):
    from app.routers import equipment
    monkeypatch.setattr(equipment, "write_audit_log", lambda *a, **kw: None)
    with pytest.raises(HTTPException) as exc_info:
        equipment.return_equipment(
            999,
            "2024-03-05",
            make_request("/api/equipment/issue/999/return", method="PUT"),
            current_user=admin_user,
            track_db=ScriptedDB(fetchone_values=[None]),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Issue record not found"


def test_get_tournament_not_found_is_404(admin_user, monkeypatch):
    from app.routers import tournaments
    monkeypatch.setattr(tournaments, "write_audit_log", lambda *a, **kw: None)
    with pytest.raises(HTTPException) as exc_info:
        tournaments.get_tournament(
            999,
            make_request("/api/tournaments/999"),
            current_user=admin_user,
            track_db=ScriptedDB(fetchone_values=[None]),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Tournament not found"


def test_get_event_not_found_is_404(admin_user, monkeypatch):
    from app.routers import events
    monkeypatch.setattr(events, "write_audit_log", lambda *a, **kw: None)
    with pytest.raises(HTTPException) as exc_info:
        events.get_event(
            999,
            make_request("/api/events/999"),
            current_user=admin_user,
            track_db=ScriptedDB(fetchone_values=[None]),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Event not found"


def test_get_medical_record_not_found_is_404(admin_user, monkeypatch):
    from app.routers import medical
    monkeypatch.setattr(medical, "write_audit_log", lambda *a, **kw: None)
    with pytest.raises(HTTPException) as exc_info:
        medical.get_medical_record(
            999,
            make_request("/api/medical-records/record/999"),
            current_user=admin_user,
            track_db=ScriptedDB(fetchone_values=[None]),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Record not found"


def test_get_performance_log_not_found_is_404(admin_user, monkeypatch):
    from app.routers import performance
    monkeypatch.setattr(performance, "write_audit_log", lambda *a, **kw: None)
    with pytest.raises(HTTPException) as exc_info:
        performance.get_performance_log(
            999,
            make_request("/api/performance-logs/999"),
            current_user=admin_user,
            track_db=ScriptedDB(fetchone_values=[None]),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Log not found"


def test_register_team_for_tournament_coach_other_team_is_403(coach_user, monkeypatch):
    from app.routers import registration
    monkeypatch.setattr(registration, "write_audit_log", lambda *a, **kw: None)
    with pytest.raises(HTTPException) as exc_info:
        registration.register_team_for_tournament(
            1,
            9,
            make_request("/api/registrations/tournament/1/team/9", method="POST"),
            current_user=coach_user,
            track_db=ScriptedDB(
                fetchone_values=[
                    {"TournamentID": 1},
                    {"TeamID": 9, "CoachID": 99},
                ]
            ),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 403
    assert "own teams" in exc_info.value.detail.lower()


def test_add_team_to_event_coach_other_team_is_403(coach_user, monkeypatch):
    from app.routers import registration
    monkeypatch.setattr(registration, "write_audit_log", lambda *a, **kw: None)
    with pytest.raises(HTTPException) as exc_info:
        registration.add_team_to_event(
            1,
            9,
            make_request("/api/registrations/event/1/team/9", method="POST"),
            current_user=coach_user,
            track_db=ScriptedDB(
                fetchone_values=[
                    {"EventID": 1, "TournamentID": None, "SportID": 4},
                    {"TeamID": 9, "SportID": 4, "CoachID": 99},
                ]
            ),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 403
    assert "own teams" in exc_info.value.detail.lower()


def test_create_medical_record_future_diagnosis_date_is_400(admin_user, monkeypatch):
    from app.routers import medical
    monkeypatch.setattr(medical, "write_audit_log", lambda *a, **kw: None)
    with pytest.raises(HTTPException) as exc_info:
        medical.create_medical_record(
            medical.MedicalCreate(
                member_id=1,
                medical_condition="Sprain",
                diagnosis_date="2099-03-01",
                recovery_date="2099-03-20",
                status="Active",
            ),
            make_request("/api/medical-records", method="POST"),
            current_user=admin_user,
            track_db=ScriptedDB(),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 400
    assert "future" in exc_info.value.detail.lower()


def test_create_performance_log_future_record_date_is_400(admin_user, monkeypatch):
    from app.routers import performance
    monkeypatch.setattr(performance, "write_audit_log", lambda *a, **kw: None)
    with pytest.raises(HTTPException) as exc_info:
        performance.create_performance_log(
            performance.PerfLogCreate(
                member_id=1,
                sport_id=1,
                metric_name="Speed",
                metric_value=9.8,
                record_date="2099-03-01",
            ),
            make_request("/api/performance-logs", method="POST"),
            current_user=admin_user,
            track_db=ScriptedDB(),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 400
    assert "future" in exc_info.value.detail.lower()
