from __future__ import annotations
import asyncio
from collections import Counter
import pytest
from fastapi import HTTPException, Response
from fastapi.responses import RedirectResponse
from .conftest import GuardDB, ScriptedDB, make_request


def _stub(monkeypatch, module, attr, calls, result=None, mutator=None):
    def _impl(*args, **kwargs):
        calls.append(attr)
        if mutator is not None:
            mutator(*args, **kwargs)
        if callable(result):
            return result(*args, **kwargs)
        return result
    monkeypatch.setattr(module, attr, _impl)


def _run(coro):
    return asyncio.run(coro)


def test_login_members_and_teams_ui_routes_delegate_to_api(monkeypatch, admin_user):
    from app.ui import routes as ui_routes
    calls = []
    _stub(
        monkeypatch,
        ui_routes,
        "api_login",
        calls,
        result={"success": True},
        mutator=lambda _body, _request, response, _db: response.headers.__setitem__(
            "set-cookie", "access_token=test-token; Path=/; HttpOnly"
        ),
    )
    _stub(
        monkeypatch,
        ui_routes,
        "api_list_members",
        calls,
        result={
            "success": True,
            "data": [
                {
                    "MemberID": 1,
                    "Name": "Ada Runner",
                    "Age": 23,
                    "Email": "ada@example.com",
                    "Role": "Player",
                    "Gender": "F",
                    "JoinDate": "2024-01-01",
                }
            ],
        },
    )
    _stub(monkeypatch, ui_routes, "api_create_member", calls, result={"success": True, "data": {"member_id": 8}})
    _stub(
        monkeypatch,
        ui_routes,
        "api_get_member_portfolio",
        calls,
        result={
            "success": True,
            "data": {
                "member": {
                    "MemberID": 1,
                    "Name": "Ada Runner",
                    "Age": 23,
                    "Email": "ada@example.com",
                    "Role": "Player",
                    "Gender": "F",
                    "ContactNumber": "555-1111",
                    "JoinDate": "2024-01-01",
                },
                "teams": [],
                "performance": [],
                "medical": [],
            },
        },
    )
    _stub(monkeypatch, ui_routes, "api_update_member", calls, result={"success": True})
    _stub(monkeypatch, ui_routes, "api_delete_member", calls, result={"success": True})
    _stub(
        monkeypatch,
        ui_routes,
        "api_list_teams",
        calls,
        result={
            "success": True,
            "data": [
                {
                    "TeamID": 1,
                    "TeamName": "Sprinters",
                    "SportID": 1,
                    "SportName": "Track",
                    "CoachID": 2,
                    "CoachName": "Coach Lee",
                    "CaptainID": 1,
                    "FormedDate": "2024-01-10",
                }
            ],
        },
    )
    _stub(monkeypatch, ui_routes, "api_create_team", calls, result={"success": True, "data": {"team_id": 1}})
    _stub(
        monkeypatch,
        ui_routes,
        "api_get_team",
        calls,
        result={
            "success": True,
            "data": {
                "team": {
                    "TeamID": 1,
                    "TeamName": "Sprinters",
                    "SportID": 1,
                    "SportName": "Track",
                    "CoachID": 2,
                    "CoachName": "Coach Lee",
                    "CaptainID": 1,
                    "FormedDate": "2024-01-10",
                },
                "roster": [
                    {
                        "MemberID": 1,
                        "Name": "Ada Runner",
                        "Role": "Player",
                        "Email": "ada@example.com",
                        "JoinDate": "2024-01-10",
                        "Position": "Lead",
                        "IsCaptain": True,
                    }
                ],
            },
        },
    )
    _stub(monkeypatch, ui_routes, "api_update_team", calls, result={"success": True})
    _stub(monkeypatch, ui_routes, "api_delete_team", calls, result={"success": True})
    track_db = GuardDB(
        allowed_substrings=("SELECT SportID, SportName FROM Sport ORDER BY SportName",),
        fetchall_values=[[{"SportID": 1, "SportName": "Track"}]],
    )
    auth_db = GuardDB(allowed_substrings=())
    response = ui_routes.login_submit(make_request("/ui/login", method="POST"), username="admin", password="secret", db=auth_db)
    assert response.status_code == 303
    assert response.headers["location"] == "/ui/dashboard"
    assert ui_routes.members_list(make_request("/ui/members"), admin_user, track_db, auth_db).status_code == 200
    assert ui_routes.member_new_form(make_request("/ui/members/new"), admin_user).status_code == 200
    assert ui_routes.member_create(
        make_request("/ui/members/new", method="POST"),
        admin_user,
        track_db,
        auth_db,
        member_id=8,
        name="Ada Runner",
        email="ada@example.com",
        age=23,
        contact_number="555-1111",
        gender="F",
        role="Player",
        join_date="2024-01-01",
        username="adar",
        password="secret",
    ).status_code == 303
    assert ui_routes.member_portfolio(1, make_request("/ui/members/1"), admin_user, track_db, auth_db).status_code == 200
    assert ui_routes.member_edit_form(1, make_request("/ui/members/1/edit"), admin_user, track_db, auth_db).status_code == 200
    assert ui_routes.member_edit_submit(
        1,
        make_request("/ui/members/1/edit", method="POST"),
        admin_user,
        track_db,
        auth_db,
        name="Ada Runner",
        email="ada@example.com",
        age=24,
        contact_number="555-1111",
    ).status_code == 303
    assert ui_routes.member_delete(1, make_request("/ui/members/1/delete", method="POST"), admin_user, track_db, auth_db).status_code == 303
    assert ui_routes.teams_list(make_request("/ui/teams"), admin_user, track_db, auth_db).status_code == 200
    assert ui_routes.team_new_form(make_request("/ui/teams/new"), admin_user, track_db).status_code == 200
    assert _run(
        ui_routes.team_create(
            make_request(
                "/ui/teams/new",
                method="POST",
                form_data={
                    "team_name": "Sprinters",
                    "sport_id": 1,
                    "coach_id": 2,
                    "captain_id": 1,
                    "formed_date": "2024-01-10",
                    "member_ids": ["1"],
                },
            ),
            admin_user,
            track_db,
            auth_db,
        )
    ).status_code == 303
    assert ui_routes.team_detail(1, make_request("/ui/teams/1"), admin_user, track_db, auth_db).status_code == 200
    assert ui_routes.team_edit_form(1, make_request("/ui/teams/1/edit"), admin_user, track_db, auth_db).status_code == 200
    assert _run(
        ui_routes.team_edit_submit(
            1,
            make_request(
                "/ui/teams/1/edit",
                method="POST",
                form_data={
                    "team_name": "Sprinters",
                    "sport_id": 1,
                    "coach_id": 2,
                    "captain_id": 1,
                    "formed_date": "2024-01-10",
                    "member_ids": ["1"],
                },
            ),
            admin_user,
            track_db,
            auth_db,
        )
    ).status_code == 303
    assert ui_routes.team_delete(1, make_request("/ui/teams/1/delete", method="POST"), admin_user, track_db, auth_db).status_code == 303
    counts = Counter(calls)
    assert counts["api_login"] == 1
    assert counts["api_list_members"] == 1
    assert counts["api_create_member"] == 1
    assert counts["api_get_member_portfolio"] == 2
    assert counts["api_update_member"] == 1
    assert counts["api_delete_member"] == 1
    assert counts["api_list_teams"] == 1
    assert counts["api_create_team"] == 1
    assert counts["api_get_team"] == 2
    assert counts["api_update_team"] == 1
    assert counts["api_delete_team"] == 1


def test_tournaments_events_and_equipment_ui_routes_delegate_to_api(monkeypatch, admin_user):
    from app.ui import routes as ui_routes
    calls = []
    tournament_payload = {
        "success": True,
        "data": {
            "tournament": {
                "TournamentID": 1,
                "TournamentName": "Open Meet",
                "StartDate": "2024-03-01",
                "EndDate": "2024-03-03",
                "Status": "Upcoming",
                "Description": "City meet",
            },
            "events": [],
            "registered_teams": [],
            "available_teams": [{"TeamID": 1, "TeamName": "Sprinters"}],
        },
    }
    event_payload = {
        "success": True,
        "data": {
            "event": {
                "EventID": 1,
                "EventName": "Final Heat",
                "EventDate": "2024-03-01",
                "StartTime": "10:00:00",
                "EndTime": "11:00:00",
                "SportID": 1,
                "SportName": "Track",
                "VenueID": 1,
                "VenueName": "Olympia Arena",
                "TournamentID": 1,
                "TournamentName": "Open Meet",
                "Status": "Scheduled",
                "Round": "Final",
            },
            "participation": [],
            "eligible_teams": [{"TeamID": 1, "TeamName": "Sprinters"}],
        },
    }
    lookups_payload = {
        "success": True,
        "data": {
            "sports": [{"SportID": 1, "SportName": "Track"}],
            "venues": [{"VenueID": 1, "VenueName": "Olympia Arena"}],
            "tournaments": [{"TournamentID": 1, "TournamentName": "Open Meet"}],
        },
    }
    _stub(monkeypatch, ui_routes, "api_list_tournaments", calls, result={"success": True, "data": [tournament_payload["data"]["tournament"]]})
    _stub(monkeypatch, ui_routes, "api_create_tournament", calls, result={"success": True, "data": {"tournament_id": 1}})
    _stub(monkeypatch, ui_routes, "api_get_tournament", calls, result=tournament_payload)
    _stub(monkeypatch, ui_routes, "api_update_tournament", calls, result={"success": True})
    _stub(monkeypatch, ui_routes, "api_delete_tournament", calls, result={"success": True})
    _stub(monkeypatch, ui_routes, "api_register_team_for_tournament", calls, result={"success": True})
    _stub(monkeypatch, ui_routes, "api_unregister_team_from_tournament", calls, result={"success": True})
    _stub(monkeypatch, ui_routes, "api_list_events", calls, result={"success": True, "data": [event_payload["data"]["event"]]})
    _stub(monkeypatch, ui_routes, "api_get_event_form_options", calls, result=lookups_payload)
    _stub(monkeypatch, ui_routes, "api_create_event", calls, result={"success": True, "data": {"event_id": 1}})
    _stub(monkeypatch, ui_routes, "api_get_event", calls, result=event_payload)
    _stub(monkeypatch, ui_routes, "api_update_event", calls, result={"success": True})
    _stub(monkeypatch, ui_routes, "api_delete_event", calls, result={"success": True})
    _stub(monkeypatch, ui_routes, "api_add_team_to_event", calls, result={"success": True})
    _stub(monkeypatch, ui_routes, "api_remove_team_from_event", calls, result={"success": True})
    _stub(
        monkeypatch,
        ui_routes,
        "api_list_equipment",
        calls,
        result={
            "success": True,
            "data": [
                {
                    "EquipmentID": 1,
                    "EquipmentName": "Baton",
                    "SportName": "Track",
                    "TotalQuantity": 10,
                    "AvailableQuantity": 8,
                    "EquipmentCondition": "Good",
                }
            ],
        },
    )
    _stub(
        monkeypatch,
        ui_routes,
        "api_list_issues",
        calls,
        result={
            "success": True,
            "data": [
                {
                    "IssueID": 4,
                    "EquipmentID": 1,
                    "EquipmentName": "Baton",
                    "MemberID": 1,
                    "MemberName": "Ada Runner",
                    "IssueDate": "2024-03-01",
                    "Quantity": 2,
                }
            ],
        },
    )
    _stub(monkeypatch, ui_routes, "api_list_members", calls, result={"success": True, "data": [{"MemberID": 1, "Name": "Ada Runner"}]})
    _stub(monkeypatch, ui_routes, "api_issue_equipment", calls, result={"success": True})
    _stub(monkeypatch, ui_routes, "api_return_equipment", calls, result={"success": True})
    track_db = GuardDB(allowed_substrings=())
    auth_db = GuardDB(allowed_substrings=())
    assert ui_routes.tournaments_list(make_request("/ui/tournaments"), admin_user, track_db, auth_db).status_code == 200
    assert ui_routes.tournament_new_form(make_request("/ui/tournaments/new"), admin_user).status_code == 200
    assert ui_routes.tournament_create(
        make_request("/ui/tournaments/new", method="POST"),
        admin_user,
        track_db,
        auth_db,
        tournament_name="Open Meet",
        start_date="2024-03-01",
        end_date="2024-03-03",
        description="City meet",
        status="Upcoming",
    ).status_code == 303
    assert ui_routes.tournament_detail(1, make_request("/ui/tournaments/1"), admin_user, track_db, auth_db).status_code == 200
    assert ui_routes.tournament_edit_form(1, make_request("/ui/tournaments/1/edit"), admin_user).status_code == 303
    assert ui_routes.tournament_edit_page(1, make_request("/ui/tournaments/1/edit-form"), admin_user, track_db, auth_db).status_code == 200
    assert ui_routes.tournament_edit_submit(
        1,
        make_request("/ui/tournaments/1/edit", method="POST"),
        admin_user,
        track_db,
        auth_db,
        tournament_name="Open Meet",
        start_date="2024-03-01",
        end_date="2024-03-03",
        description="Updated",
        status="Ongoing",
    ).status_code == 303
    assert ui_routes.tournament_delete(1, make_request("/ui/tournaments/1/delete", method="POST"), admin_user, track_db, auth_db).status_code == 303
    assert ui_routes.tournament_register_team(1, make_request("/ui/tournaments/1/register", method="POST"), admin_user, track_db, auth_db, team_id=1).status_code == 303
    assert ui_routes.tournament_unregister_team(1, 1, make_request("/ui/tournaments/1/unregister/1", method="POST"), admin_user, track_db, auth_db).status_code == 303
    assert ui_routes.events_list(make_request("/ui/events"), admin_user, track_db, auth_db).status_code == 200
    assert ui_routes.event_new_form(make_request("/ui/events/new"), admin_user, track_db, auth_db).status_code == 200
    assert ui_routes.event_create(
        make_request("/ui/events/new", method="POST"),
        admin_user,
        track_db,
        auth_db,
        event_name="Final Heat",
        tournament_id=1,
        event_date="2024-03-01",
        start_time="10:00",
        end_time="11:00",
        venue_id=1,
        sport_id=1,
        status="Scheduled",
        round_name="Final",
    ).status_code == 303
    assert ui_routes.event_detail(1, make_request("/ui/events/1"), admin_user, track_db, auth_db).status_code == 200
    assert ui_routes.event_edit_form(1, make_request("/ui/events/1/edit"), admin_user, track_db, auth_db).status_code == 200
    assert ui_routes.event_edit_submit(
        1,
        make_request("/ui/events/1/edit", method="POST"),
        admin_user,
        track_db,
        auth_db,
        event_name="Final Heat",
        tournament_id=1,
        event_date="2024-03-01",
        start_time="10:00",
        end_time="11:00",
        venue_id=1,
        sport_id=1,
        status="Scheduled",
        round_name="Final",
    ).status_code == 303
    assert ui_routes.event_delete(1, make_request("/ui/events/1/delete", method="POST"), admin_user, track_db, auth_db).status_code == 303
    assert ui_routes.event_add_team(1, make_request("/ui/events/1/add-team", method="POST"), admin_user, track_db, auth_db, team_id=1).status_code == 303
    assert ui_routes.event_remove_team(1, 1, make_request("/ui/events/1/remove-team/1", method="POST"), admin_user, track_db, auth_db).status_code == 303
    assert ui_routes.equipment_list(make_request("/ui/equipment"), admin_user, track_db, auth_db).status_code == 200
    assert ui_routes.equipment_issue(
        make_request("/ui/equipment/issue", method="POST"),
        admin_user,
        track_db,
        auth_db,
        equipment_id=1,
        member_id=1,
        issue_date="2024-03-01",
        quantity=2,
    ).status_code == 303
    assert ui_routes.equipment_return(
        4,
        make_request("/ui/equipment/issue/4/return", method="POST"),
        admin_user,
        track_db,
        auth_db,
        return_date="2024-03-05",
    ).status_code == 303
    counts = Counter(calls)
    assert counts["api_list_tournaments"] == 1
    assert counts["api_create_tournament"] == 1
    assert counts["api_get_tournament"] == 2
    assert counts["api_update_tournament"] == 1
    assert counts["api_delete_tournament"] == 1
    assert counts["api_register_team_for_tournament"] == 1
    assert counts["api_unregister_team_from_tournament"] == 1
    assert counts["api_list_events"] == 1
    assert counts["api_get_event_form_options"] == 2
    assert counts["api_create_event"] == 1
    assert counts["api_get_event"] == 2
    assert counts["api_update_event"] == 1
    assert counts["api_delete_event"] == 1
    assert counts["api_add_team_to_event"] == 1
    assert counts["api_remove_team_from_event"] == 1
    assert counts["api_list_equipment"] == 1
    assert counts["api_list_issues"] == 1
    assert counts["api_list_members"] == 1
    assert counts["api_issue_equipment"] == 1
    assert counts["api_return_equipment"] == 1


def test_performance_medical_admin_and_misc_ui_routes(monkeypatch, admin_user):
    from app import main as app_main
    from app.ui import routes as ui_routes
    calls = []
    _stub(
        monkeypatch,
        ui_routes,
        "api_get_performance_log",
        calls,
        result={
            "success": True,
            "data": {
                "LogID": 5,
                "MemberID": 1,
                "SportID": 1,
                "SportName": "Track",
                "MetricName": "Speed",
                "MetricValue": 9.87,
                "RecordDate": "2024-03-01",
            },
        },
    )
    _stub(monkeypatch, ui_routes, "api_update_performance_log", calls, result={"success": True})
    _stub(monkeypatch, ui_routes, "api_delete_performance_log", calls, result={"success": True})
    _stub(monkeypatch, ui_routes, "api_create_performance_log", calls, result={"success": True})
    _stub(monkeypatch, ui_routes, "api_create_medical_record", calls, result={"success": True})
    _stub(
        monkeypatch,
        ui_routes,
        "api_get_medical_record",
        calls,
        result={
            "success": True,
            "data": {
                "RecordID": 6,
                "MemberID": 1,
                "MedicalCondition": "Hamstring strain",
                "DiagnosisDate": "2024-03-01",
                "RecoveryDate": "2024-03-20",
                "Status": "Active",
            },
        },
    )
    _stub(monkeypatch, ui_routes, "api_update_medical_record", calls, result={"success": True})
    _stub(monkeypatch, ui_routes, "api_delete_medical_record", calls, result={"success": True})
    _stub(
        monkeypatch,
        ui_routes,
        "api_get_audit_log",
        calls,
        result={
            "success": True,
            "data": [
                {
                    "log_id": 1,
                    "timestamp": "2024-03-01 10:00:00.000",
                    "username": "admin",
                    "action": "SELECT",
                    "table_name": "Member",
                    "record_id": "1",
                    "status": "SUCCESS",
                    "ip_address": "127.0.0.1",
                    "entry_hash": "abc123def456",
                }
            ],
        },
    )
    _stub(
        monkeypatch,
        ui_routes,
        "api_verify_audit",
        calls,
        result={"success": True, "data": {"intact": True, "total_entries": 1}},
    )
    perf_track_db = GuardDB(
        allowed_substrings=("SELECT SportID, SportName FROM Sport ORDER BY SportName",),
        fetchall_values=[[{"SportID": 1, "SportName": "Track"}]],
    )
    no_sql_db = GuardDB(allowed_substrings=())
    assert ui_routes.perf_log_edit_form(5, make_request("/ui/performance-logs/5/edit"), admin_user, perf_track_db, no_sql_db).status_code == 200
    assert ui_routes.perf_log_edit_submit(
        5,
        make_request("/ui/performance-logs/5/edit", method="POST"),
        admin_user,
        no_sql_db,
        no_sql_db,
        member_id=1,
        sport_id=1,
        metric_name="Speed",
        metric_value=9.9,
        record_date="2024-03-02",
    ).status_code == 303
    assert ui_routes.perf_log_delete(5, make_request("/ui/performance-logs/5/delete", method="POST"), admin_user, no_sql_db, no_sql_db, member_id=1).status_code == 303
    assert ui_routes.performance_log_create(
        make_request("/ui/performance-logs/new", method="POST"),
        admin_user,
        no_sql_db,
        no_sql_db,
        member_id=1,
        sport_id=1,
        metric_name="Speed",
        metric_value=9.9,
        record_date="2024-03-02",
    ).status_code == 303
    assert ui_routes.medical_record_create(
        make_request("/ui/medical-records/new", method="POST"),
        admin_user,
        no_sql_db,
        no_sql_db,
        member_id=1,
        medical_condition="Hamstring strain",
        diagnosis_date="2024-03-01",
        recovery_date="2024-03-20",
        status="Active",
    ).status_code == 303
    assert ui_routes.medical_record_edit_form(6, make_request("/ui/medical-records/6/edit"), admin_user, no_sql_db, no_sql_db).status_code == 200
    assert ui_routes.medical_record_edit_submit(
        6,
        make_request("/ui/medical-records/6/edit", method="POST"),
        admin_user,
        no_sql_db,
        no_sql_db,
        member_id=1,
        medical_condition="Hamstring strain",
        diagnosis_date="2024-03-01",
        recovery_date="2024-03-20",
        status="Recovered",
    ).status_code == 303
    assert ui_routes.medical_record_delete(6, make_request("/ui/medical-records/6/delete", method="POST"), admin_user, no_sql_db, no_sql_db, member_id=1).status_code == 303
    assert ui_routes.audit_page(make_request("/ui/admin/audit"), admin_user, no_sql_db).status_code == 200
    assert ui_routes.verify_audit(make_request("/ui/admin/verify-audit", method="POST"), admin_user, no_sql_db)["data"]["intact"] is True
    assert ui_routes.root().status_code == 303
    assert app_main.root().status_code == 303
    health_track_db = ScriptedDB(
        fetchone_values=[
            {"c": 5},
            {"c": 2},
            {"c": 3},
            {"c": 2},
            {"c": 1},
            {"c": 1},
            {"c": 4},
            {"c": 1},
        ],
        fetchall_values=[
            [{"Role": "Player", "c": 3}, {"Role": "Coach", "c": 1}, {"Role": "Admin", "c": 1}],
            [{"EventID": 1, "EventName": "Meet", "EventDate": "2024-03-01", "Status": "Scheduled", "VenueName": "Arena"}],
            [{"TournamentName": "Open Meet", "Status": "Upcoming", "EndDate": "2024-03-03"}],
        ],
    )
    assert app_main.health(auth_db=ScriptedDB(), track_db=ScriptedDB()) == {"olympia_auth": "ok", "olympia_track": "ok"}
    assert ui_routes.dashboard(make_request("/ui/dashboard"), admin_user, health_track_db).status_code == 200
    counts = Counter(calls)
    assert counts["api_get_performance_log"] == 1
    assert counts["api_update_performance_log"] == 1
    assert counts["api_delete_performance_log"] == 1
    assert counts["api_create_performance_log"] == 1
    assert counts["api_create_medical_record"] == 1
    assert counts["api_get_medical_record"] == 1
    assert counts["api_update_medical_record"] == 1
    assert counts["api_delete_medical_record"] == 1
    assert counts["api_get_audit_log"] == 1
    assert counts["api_verify_audit"] == 1


def test_ui_error_handling_and_cookie_forwarding(monkeypatch, admin_user):
    from app.ui import routes as ui_routes
    track_db = GuardDB(allowed_substrings=())
    auth_db = GuardDB(allowed_substrings=())
    source = Response()
    source.raw_headers = [
        (b"set-cookie", b"access_token=token; Path=/; HttpOnly"),
        (b"set-cookie", b"refresh_token=refresh; Path=/; HttpOnly"),
    ]
    target = RedirectResponse("/ui/dashboard", status_code=303)
    ui_routes._copy_set_cookie(source, target)
    assert target.headers.getlist("set-cookie") == [
        "access_token=token; Path=/; HttpOnly",
        "refresh_token=refresh; Path=/; HttpOnly",
    ]
    monkeypatch.setattr(ui_routes, "api_update_tournament", lambda *args, **kwargs: (_ for _ in ()).throw(Exception("db exploded")))
    response = ui_routes.tournament_edit_submit(
        1,
        make_request("/ui/tournaments/1/edit", method="POST"),
        admin_user,
        track_db,
        auth_db,
        tournament_name="Open Meet",
        start_date="2024-03-01",
        end_date="2024-03-03",
        description="Updated",
        status="Ongoing",
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/ui/tournaments/1/edit-form?error=db+exploded"
    monkeypatch.setattr(ui_routes, "api_register_team_for_tournament", lambda *args, **kwargs: (_ for _ in ()).throw(Exception("registration failed")))
    response = ui_routes.tournament_register_team(
        1,
        make_request("/ui/tournaments/1/register", method="POST"),
        admin_user,
        track_db,
        auth_db,
        team_id=1,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/ui/tournaments/1?error=registration+failed"
    monkeypatch.setattr(ui_routes, "api_update_event", lambda *args, **kwargs: (_ for _ in ()).throw(Exception("event update failed")))
    response = ui_routes.event_edit_submit(
        1,
        make_request("/ui/events/1/edit", method="POST"),
        admin_user,
        track_db,
        auth_db,
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
    assert response.status_code == 303
    assert response.headers["location"] == "/ui/events/1/edit?error=event+update+failed"
    monkeypatch.setattr(
        ui_routes,
        "api_add_team_to_event",
        lambda *args, **kwargs: (_ for _ in ()).throw(HTTPException(status_code=404, detail="Event not found")),
    )
    response = ui_routes.event_add_team(
        999,
        make_request("/ui/events/999/add-team", method="POST"),
        admin_user,
        track_db,
        auth_db,
        team_id=1,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/ui/events?error=Event+not+found"


def test_member_create_ui_supports_split_phone_fields(monkeypatch, admin_user):
    from app.ui import routes as ui_routes
    captured = {}
    def _create_member(body, *_args, **_kwargs):
        captured["body"] = body
        return {"success": True, "data": {"member_id": 42}}
    monkeypatch.setattr(ui_routes, "api_create_member", _create_member)
    response = ui_routes.member_create(
        make_request("/ui/members/new", method="POST"),
        admin_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
        name="Split Phone",
        email="split@example.com",
        age=21,
        contact_country_code="+91",
        contact_number_local="9876543210",
        gender="F",
        role="Player",
        join_date="2024-01-01",
        username="splitphone",
        password="secret",
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/ui/members/42"
    assert captured["body"].contact_number == "+919876543210"


def test_member_create_ui_blocks_non_admin(monkeypatch, player_user):
    from app.ui import routes as ui_routes
    called = {"api": False}
    monkeypatch.setattr(
        ui_routes,
        "api_create_member",
        lambda *args, **kwargs: called.__setitem__("api", True),
    )
    response = ui_routes.member_create(
        make_request("/ui/members/new", method="POST"),
        player_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
        member_id=None,
        name="Blocked",
        email="blocked@example.com",
        age=21,
        contact_country_code="+91",
        contact_number_local="9876543210",
        gender="F",
        role="Player",
        join_date="2024-01-01",
        username="blocked",
        password="secret",
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/ui/members"
    assert called["api"] is False


def test_member_delete_ui_blocks_non_admin(monkeypatch, player_user):
    from app.ui import routes as ui_routes
    called = {"api": False}
    monkeypatch.setattr(
        ui_routes,
        "api_delete_member",
        lambda *args, **kwargs: called.__setitem__("api", True),
    )
    response = ui_routes.member_delete(
        1,
        make_request("/ui/members/1/delete", method="POST"),
        player_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/ui/members"
    assert called["api"] is False


def test_event_edit_submit_rerenders_form_with_data_on_http_error(monkeypatch, admin_user):
    from app.ui import routes as ui_routes
    monkeypatch.setattr(
        ui_routes,
        "api_update_event",
        lambda *args, **kwargs: (_ for _ in ()).throw(HTTPException(status_code=400, detail="End time must be after start time.")),
    )
    monkeypatch.setattr(
        ui_routes,
        "api_get_event_form_options",
        lambda *args, **kwargs: {
            "success": True,
            "data": {
                "sports": [{"SportID": 1, "SportName": "Track"}],
                "venues": [{"VenueID": 1, "VenueName": "Arena"}],
                "tournaments": [{"TournamentID": 1, "TournamentName": "Open Meet"}],
            },
        },
    )
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
        end_time="09:00",
        venue_id=1,
        sport_id=1,
        status="Scheduled",
        round_name="Final",
    )
    assert response.status_code == 200
    assert response.context["form_data"]["event_name"] == "Final Heat"
    assert response.context["error"] == "End time must be after start time."


def test_coach_can_use_event_add_team_ui_route(monkeypatch, coach_user):
    from app.ui import routes as ui_routes
    monkeypatch.setattr(ui_routes, "api_add_team_to_event", lambda *args, **kwargs: {"success": True})
    response = ui_routes.event_add_team(
        1,
        make_request("/ui/events/1/add-team", method="POST"),
        coach_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
        team_id=7,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/ui/events/1?success=Team+added+to+event."


def test_equipment_create_ui_route_calls_api(monkeypatch, admin_user):
    from app.ui import routes as ui_routes
    captured = {}
    def _create_equipment(body, *_args, **_kwargs):
        captured["body"] = body
        return {"success": True, "data": {"equipment_id": 9}}
    monkeypatch.setattr(ui_routes, "api_create_equipment", _create_equipment)
    response = ui_routes.equipment_create(
        make_request("/ui/equipment/new", method="POST"),
        admin_user,
        GuardDB(allowed_substrings=()),
        GuardDB(allowed_substrings=()),
        equipment_name="Marker Cone",
        total_quantity=6,
        equipment_condition="New",
        sport_id="1",
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/ui/equipment?success=Equipment+added+successfully."
    assert captured["body"].sport_id == 1
