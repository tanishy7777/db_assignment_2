from __future__ import annotations
import bcrypt
import pytest
from fastapi import HTTPException, Response
from .conftest import ScriptedDB, make_request


def test_auth_router_endpoints(admin_user, monkeypatch):
    from app.auth import router as auth_router
    monkeypatch.setattr(auth_router, "write_audit_log", lambda *args, **kwargs: None)
    password_hash = bcrypt.hashpw(b"secret", bcrypt.gensalt()).decode()
    db = ScriptedDB(
        fetchone_values=[
            {
                "user_id": 1,
                "username": "admin",
                "password_hash": password_hash,
                "role": "Admin",
                "member_id": 1,
                "is_active": True,
            }
        ]
    )
    response = Response()
    login_result = auth_router.login(
        auth_router.LoginRequest(username="admin", password="secret"),
        make_request("/auth/login", method="POST"),
        response,
        db,
    )
    assert login_result["success"] is True
    assert "access_token=" in response.headers["set-cookie"]
    assert any("INSERT INTO sessions" in query for query, _ in db.executed)
    is_auth_result = auth_router.is_auth(admin_user)
    assert is_auth_result["data"]["username"] == "admin"
    logout_db = ScriptedDB()
    logout_response = auth_router.logout(
        make_request("/auth/logout", cookies={"access_token": "test-token"}),
        Response(),
        current_user=admin_user,
        db=logout_db,
    )
    assert logout_response.status_code == 303
    assert logout_response.headers["location"] == "/ui/login"


def test_members_router_endpoints(admin_user, monkeypatch):
    from app.routers import members
    monkeypatch.setattr(members, "write_audit_log", lambda *args, **kwargs: None)
    list_result = members.list_members(
        make_request("/api/members"),
        current_user=admin_user,
        track_db=ScriptedDB(
            fetchall_values=[
                [
                    {
                        "MemberID": 1,
                        "Name": "Ada Runner",
                        "Age": 23,
                        "Email": "ada@example.com",
                        "Role": "Player",
                        "Gender": "F",
                        "JoinDate": "2024-01-01",
                    }
                ]
            ]
        ),
        auth_db=ScriptedDB(),
    )
    assert list_result["success"] is True
    profile_result = members.get_my_profile(
        make_request("/api/members/me"),
        current_user=admin_user,
        track_db=ScriptedDB(
            fetchone_values=[
                {
                    "MemberID": 1,
                    "Name": "Ada Runner",
                    "Age": 23,
                    "Email": "ada@example.com",
                    "Role": "Player",
                    "Gender": "F",
                    "JoinDate": "2024-01-01",
                }
            ]
        ),
        auth_db=ScriptedDB(),
    )
    assert profile_result["data"]["MemberID"] == 1
    portfolio_result = members.get_member_portfolio(
        1,
        make_request("/api/members/1"),
        current_user=admin_user,
        track_db=ScriptedDB(
            fetchone_values=[
                {
                    "MemberID": 1,
                    "Name": "Ada Runner",
                    "Age": 23,
                    "Email": "ada@example.com",
                    "Role": "Player",
                    "Gender": "F",
                    "ContactNumber": "555-1111",
                    "JoinDate": "2024-01-01",
                }
            ],
            fetchall_values=[[], [], []],
        ),
        auth_db=ScriptedDB(),
    )
    assert portfolio_result["data"]["member"]["Name"] == "Ada Runner"
    create_result = members.create_member(
        members.MemberCreate(
            member_id=8,
            name="New Athlete",
            age=21,
            email="new@example.com",
            contact_number="555-2222",
            gender="F",
            role="Player",
            join_date="2024-02-01",
            username="newathlete",
            password="secret",
        ),
        make_request("/api/members", method="POST"),
        current_user=admin_user,
        track_db=ScriptedDB(),
        auth_db=ScriptedDB(),
    )
    assert create_result["success"] is True
    update_result = members.update_member(
        1,
        members.MemberUpdate(name="Ada Fast", age=24),
        make_request("/api/members/1", method="PUT"),
        current_user=admin_user,
        track_db=ScriptedDB(fetchone_values=[{"MemberID": 1, "Role": "Player"}]),
        auth_db=ScriptedDB(),
    )
    assert update_result["success"] is True
    delete_result = members.delete_member(
        1,
        make_request("/api/members/1", method="DELETE"),
        current_user=admin_user,
        track_db=ScriptedDB(fetchone_values=[{"MemberID": 1, "Role": "Player"}]),
        auth_db=ScriptedDB(),
    )
    assert delete_result["success"] is True
    with pytest.raises(HTTPException) as exc_info:
        members.get_member_portfolio(
            999,
            make_request("/api/members/999"),
            current_user=admin_user,
            track_db=ScriptedDB(fetchone_values=[None]),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 404


def test_teams_router_endpoints(admin_user, monkeypatch):
    from app.routers import teams
    monkeypatch.setattr(teams, "write_audit_log", lambda *args, **kwargs: None)
    list_result = teams.list_teams(
        make_request("/api/teams"),
        current_user=admin_user,
        track_db=ScriptedDB(fetchall_values=[[{"TeamID": 1, "TeamName": "Sprinters", "SportName": "Track"}]]),
        auth_db=ScriptedDB(),
    )
    assert list_result["success"] is True
    detail_result = teams.get_team(
        1,
        make_request("/api/teams/1"),
        current_user=admin_user,
        track_db=ScriptedDB(
            fetchone_values=[{"TeamID": 1, "TeamName": "Sprinters", "SportID": 1, "CoachID": 2}],
            fetchall_values=[[{"MemberID": 1, "Name": "Ada Runner", "Role": "Player"}]],
        ),
        auth_db=ScriptedDB(),
    )
    assert detail_result["data"]["team"]["TeamID"] == 1
    create_result = teams.create_team(
        teams.TeamCreate(
            team_name="Sprinters",
            sport_id=1,
            formed_date="2024-01-10",
            coach_id=2,
            captain_id=1,
            member_ids=[1, 3],
        ),
        make_request("/api/teams", method="POST"),
        current_user=admin_user,
        track_db=ScriptedDB(
            fetchone_values=[{"Role": "Coach"}, {"SportID": 1}, {"nid": 21}],
            fetchall_values=[
                [{"MemberID": 1, "Role": "Player"}, {"MemberID": 3, "Role": "Player"}],
                [],
            ],
        ),
        auth_db=ScriptedDB(),
    )
    assert create_result["data"]["team_id"] == 21
    update_result = teams.update_team(
        1,
        teams.TeamUpdate(team_name="Sprinters Elite"),
        make_request("/api/teams/1", method="PUT"),
        current_user=admin_user,
        track_db=ScriptedDB(fetchone_values=[{"TeamID": 1, "CoachID": 2, "CaptainID": 1, "FormedDate": "2024-01-10"}]),
        auth_db=ScriptedDB(),
    )
    assert update_result["success"] is True
    delete_result = teams.delete_team(
        1,
        make_request("/api/teams/1", method="DELETE"),
        current_user=admin_user,
        track_db=ScriptedDB(fetchone_values=[{"TeamID": 1, "CoachID": 2}]),
        auth_db=ScriptedDB(),
    )
    assert delete_result["success"] is True


def test_tournaments_router_endpoints(admin_user, monkeypatch):
    from app.routers import tournaments
    monkeypatch.setattr(tournaments, "write_audit_log", lambda *args, **kwargs: None)
    list_result = tournaments.list_tournaments(
        make_request("/api/tournaments"),
        current_user=admin_user,
        track_db=ScriptedDB(
            fetchall_values=[[{"TournamentID": 1, "TournamentName": "Open Meet", "StartDate": "2024-03-01", "EndDate": "2024-03-03"}]]
        ),
        auth_db=ScriptedDB(),
    )
    assert list_result["success"] is True
    detail_result = tournaments.get_tournament(
        1,
        make_request("/api/tournaments/1"),
        current_user=admin_user,
        track_db=ScriptedDB(
            fetchone_values=[{"TournamentID": 1, "TournamentName": "Open Meet", "StartDate": "2024-03-01", "EndDate": "2024-03-03"}],
            fetchall_values=[
                [{"EventID": 1, "EventName": "Heat", "EventDate": "2024-03-01", "StartTime": "10:00:00", "EndTime": "11:00:00"}],
                [{"TeamID": 1, "TeamName": "Sprinters", "SportName": "Track"}],
                [{"TeamID": 1, "TeamName": "Sprinters"}, {"TeamID": 2, "TeamName": "Throwers"}],
            ],
        ),
        auth_db=ScriptedDB(),
    )
    assert detail_result["data"]["available_teams"] == [{"TeamID": 2, "TeamName": "Throwers"}]
    create_result = tournaments.create_tournament(
        tournaments.TournamentCreate(
            tournament_name="Open Meet",
            start_date="2024-03-01",
            end_date="2024-03-03",
            status="Upcoming",
            description="City meet",
        ),
        make_request("/api/tournaments", method="POST"),
        current_user=admin_user,
        track_db=ScriptedDB(fetchone_values=[{"nid": 12}]),
        auth_db=ScriptedDB(),
    )
    assert create_result["data"]["tournament_id"] == 12
    retry_db = ScriptedDB(
        fetchone_values=[{"nid": 12}, {"nid": 13}],
        execute_side_effects=[None, Exception("Duplicate entry '12' for key 'PRIMARY'"), None, None],
    )
    retry_result = tournaments.create_tournament(
        tournaments.TournamentCreate(
            tournament_name="Retry Meet",
            start_date="2024-04-01",
            end_date="2024-04-03",
            status="Upcoming",
        ),
        make_request("/api/tournaments", method="POST"),
        current_user=admin_user,
        track_db=retry_db,
        auth_db=ScriptedDB(),
    )
    assert retry_result["data"]["tournament_id"] == 13
    update_result = tournaments.update_tournament(
        1,
        tournaments.TournamentUpdate(status="Ongoing"),
        make_request("/api/tournaments/1", method="PUT"),
        current_user=admin_user,
        track_db=ScriptedDB(fetchone_values=[{"TournamentID": 1}]),
        auth_db=ScriptedDB(),
    )
    assert update_result["success"] is True
    delete_result = tournaments.delete_tournament(
        1,
        make_request("/api/tournaments/1", method="DELETE"),
        current_user=admin_user,
        track_db=ScriptedDB(fetchone_values=[{"TournamentID": 1}]),
        auth_db=ScriptedDB(),
    )
    assert delete_result["success"] is True


def test_events_router_endpoints(admin_user, monkeypatch):
    from app.routers import events
    monkeypatch.setattr(events, "write_audit_log", lambda *args, **kwargs: None)
    lookup_result = events.get_event_form_options(
        make_request("/api/events/lookups"),
        current_user=admin_user,
        track_db=ScriptedDB(
            fetchall_values=[
                [{"SportID": 1, "SportName": "Track"}],
                [{"VenueID": 1, "VenueName": "Arena"}],
                [{"TournamentID": 1, "TournamentName": "Open Meet"}],
            ]
        ),
        auth_db=ScriptedDB(),
    )
    assert lookup_result["data"]["sports"][0]["SportName"] == "Track"
    list_result = events.list_events(
        make_request("/api/events"),
        current_user=admin_user,
        track_db=ScriptedDB(
            fetchall_values=[[{"EventID": 1, "EventDate": "2024-03-01", "StartTime": "10:00:00", "EndTime": "11:00:00"}]]
        ),
        auth_db=ScriptedDB(),
    )
    assert list_result["success"] is True
    tournament_detail_db = ScriptedDB(
        fetchone_values=[
            {
                "EventID": 1,
                "EventName": "Heat",
                "EventDate": "2024-03-01",
                "StartTime": "10:00:00",
                "EndTime": "11:00:00",
                "TournamentID": 1,
                "SportID": 1,
            }
        ],
        fetchall_values=[
            [{"TeamID": 1, "TeamName": "Sprinters"}],
            [{"TeamID": 1, "TeamName": "Sprinters"}, {"TeamID": 2, "TeamName": "Throwers"}],
        ],
    )
    detail_result = events.get_event(
        1,
        make_request("/api/events/1"),
        current_user=admin_user,
        track_db=tournament_detail_db,
        auth_db=ScriptedDB(),
    )
    assert detail_result["data"]["eligible_teams"] == [{"TeamID": 2, "TeamName": "Throwers"}]
    assert "t.SportID = %s" in tournament_detail_db.executed[2][0]
    assert tournament_detail_db.executed[2][1] == (1, 1)
    standalone_detail_db = ScriptedDB(
        fetchone_values=[
            {
                "EventID": 2,
                "EventName": "Sprint Final",
                "EventDate": "2024-03-02",
                "StartTime": "12:00:00",
                "EndTime": "13:00:00",
                "TournamentID": None,
                "SportID": 4,
            }
        ],
        fetchall_values=[
            [{"TeamID": 7, "TeamName": "Cyclers"}],
            [{"TeamID": 7, "TeamName": "Cyclers"}, {"TeamID": 8, "TeamName": "Sprinters"}],
        ],
    )
    events.get_event(
        2,
        make_request("/api/events/2"),
        current_user=admin_user,
        track_db=standalone_detail_db,
        auth_db=ScriptedDB(),
    )
    assert "WHERE SportID = %s" in standalone_detail_db.executed[2][0]
    assert standalone_detail_db.executed[2][1] == (4,)
    create_result = events.create_event(
        events.EventCreate(
            event_name="Final",
            event_date="2024-03-01",
            start_time="10:00:00",
            end_time="11:00:00",
            venue_id=1,
            sport_id=1,
            status="Scheduled",
            tournament_id=1,
            round="Final",
        ),
        make_request("/api/events", method="POST"),
        current_user=admin_user,
        track_db=ScriptedDB(fetchone_values=[{"nid": 30}]),
        auth_db=ScriptedDB(),
    )
    assert create_result["data"]["event_id"] == 30
    update_result = events.update_event(
        1,
        events.EventUpdate(status="Completed"),
        make_request("/api/events/1", method="PUT"),
        current_user=admin_user,
        track_db=ScriptedDB(fetchone_values=[{"EventID": 1}]),
        auth_db=ScriptedDB(),
    )
    assert update_result["success"] is True
    delete_result = events.delete_event(
        1,
        make_request("/api/events/1", method="DELETE"),
        current_user=admin_user,
        track_db=ScriptedDB(fetchone_values=[{"EventID": 1}]),
        auth_db=ScriptedDB(),
    )
    assert delete_result["success"] is True


def test_equipment_router_endpoints(admin_user, monkeypatch):
    from app.routers import equipment
    monkeypatch.setattr(equipment, "write_audit_log", lambda *args, **kwargs: None)
    list_db = ScriptedDB(fetchall_values=[[{"EquipmentID": 1, "AvailableQuantity": 8, "SportName": "Track"}]])
    list_result = equipment.list_equipment(
        make_request("/api/equipment"),
        current_user=admin_user,
        track_db=list_db,
        auth_db=ScriptedDB(),
    )
    assert list_result["data"][0]["AvailableQuantity"] == 8
    assert "AvailableQuantity" in list_db.executed[0][0]
    issues_db = ScriptedDB(fetchall_values=[[{"IssueID": 4, "IssueDate": "2024-03-01", "ReturnDate": None}]])
    issues_result = equipment.list_issues(
        make_request("/api/equipment/issues"),
        current_user=admin_user,
        track_db=issues_db,
        auth_db=ScriptedDB(),
        active_only=True,
    )
    assert issues_result["success"] is True
    assert "ei.ReturnDate IS NULL" in issues_db.executed[0][0]
    issue_result = equipment.issue_equipment(
        equipment.IssueCreate(equipment_id=1, member_id=1, issue_date="2024-03-01", quantity=2),
        make_request("/api/equipment/issue", method="POST"),
        current_user=admin_user,
        track_db=ScriptedDB(fetchone_values=[{"TotalQuantity": 10, "issued": 3}, {"nid": 5}]),
        auth_db=ScriptedDB(),
    )
    assert issue_result["data"]["issue_id"] == 5
    return_result = equipment.return_equipment(
        5,
        "2024-03-05",
        make_request("/api/equipment/issue/5/return", method="PUT"),
        current_user=admin_user,
        track_db=ScriptedDB(fetchone_values=[{"IssueID": 5}]),
        auth_db=ScriptedDB(),
    )
    assert return_result["success"] is True


def test_performance_router_endpoints(admin_user, monkeypatch):
    from app.routers import performance
    monkeypatch.setattr(performance, "write_audit_log", lambda *args, **kwargs: None)
    detail_result = performance.get_performance_log(
        5,
        make_request("/api/performance-logs/5"),
        current_user=admin_user,
        track_db=ScriptedDB(
            fetchone_values=[
                {
                    "LogID": 5,
                    "MemberID": 1,
                    "SportID": 1,
                    "SportName": "Track",
                    "MetricName": "Speed",
                    "MetricValue": 9.87,
                    "RecordDate": "2024-03-01",
                }
            ]
        ),
        auth_db=ScriptedDB(),
    )
    assert detail_result["data"]["LogID"] == 5
    list_result = performance.list_performance_logs(
        make_request("/api/performance-logs"),
        current_user=admin_user,
        track_db=ScriptedDB(fetchall_values=[[{"LogID": 5, "RecordDate": "2024-03-01"}]]),
        auth_db=ScriptedDB(),
    )
    assert list_result["success"] is True
    create_result = performance.create_performance_log(
        performance.PerfLogCreate(
            member_id=1,
            sport_id=1,
            metric_name="Speed",
            metric_value=9.87,
            record_date="2024-03-01",
        ),
        make_request("/api/performance-logs", method="POST"),
        current_user=admin_user,
        track_db=ScriptedDB(fetchone_values=[{"nid": 6}]),
        auth_db=ScriptedDB(),
    )
    assert create_result["data"]["log_id"] == 6
    update_result = performance.update_performance_log(
        5,
        performance.PerfLogUpdate(metric_value=9.91),
        make_request("/api/performance-logs/5", method="PUT"),
        current_user=admin_user,
        track_db=ScriptedDB(fetchone_values=[{"LogID": 5, "MemberID": 1}]),
        auth_db=ScriptedDB(),
    )
    assert update_result["success"] is True
    delete_result = performance.delete_performance_log(
        5,
        make_request("/api/performance-logs/5", method="DELETE"),
        current_user=admin_user,
        track_db=ScriptedDB(fetchone_values=[{"LogID": 5}]),
        auth_db=ScriptedDB(),
    )
    assert delete_result["success"] is True


def test_medical_router_endpoints(admin_user, monkeypatch):
    from app.routers import medical
    monkeypatch.setattr(medical, "write_audit_log", lambda *args, **kwargs: None)
    detail_result = medical.get_medical_record(
        6,
        make_request("/api/medical-records/record/6"),
        current_user=admin_user,
        track_db=ScriptedDB(
            fetchone_values=[
                {
                    "RecordID": 6,
                    "MemberID": 1,
                    "MedicalCondition": "Hamstring strain",
                    "DiagnosisDate": "2024-03-01",
                    "RecoveryDate": "2024-03-20",
                    "Status": "Active",
                }
            ]
        ),
        auth_db=ScriptedDB(),
    )
    assert detail_result["data"]["RecordID"] == 6
    list_result = medical.get_medical_records(
        1,
        make_request("/api/medical-records/1"),
        current_user=admin_user,
        track_db=ScriptedDB(fetchall_values=[[{"RecordID": 6, "DiagnosisDate": "2024-03-01", "RecoveryDate": None}]]),
        auth_db=ScriptedDB(),
    )
    assert list_result["success"] is True
    create_result = medical.create_medical_record(
        medical.MedicalCreate(
            member_id=1,
            medical_condition="Hamstring strain",
            diagnosis_date="2024-03-01",
            recovery_date="2024-03-20",
            status="Active",
        ),
        make_request("/api/medical-records", method="POST"),
        current_user=admin_user,
        track_db=ScriptedDB(fetchone_values=[{"nid": 7}]),
        auth_db=ScriptedDB(),
    )
    assert create_result["data"]["record_id"] == 7
    update_result = medical.update_medical_record(
        6,
        medical.MedicalUpdate(status="Recovered"),
        make_request("/api/medical-records/6", method="PUT"),
        current_user=admin_user,
        track_db=ScriptedDB(fetchone_values=[{"RecordID": 6}]),
        auth_db=ScriptedDB(),
    )
    assert update_result["success"] is True
    delete_result = medical.delete_medical_record(
        6,
        make_request("/api/medical-records/6", method="DELETE"),
        current_user=admin_user,
        track_db=ScriptedDB(fetchone_values=[{"RecordID": 6, "MemberID": 1}]),
        auth_db=ScriptedDB(),
    )
    assert delete_result["success"] is True


def test_registration_and_admin_router_endpoints(admin_user, monkeypatch):
    from app.routers import admin, registration
    from app.services import audit
    monkeypatch.setattr(registration, "write_audit_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(admin, "verify_audit_chain", lambda db: {"intact": True, "total_entries": 1})
    register_result = registration.register_team_for_tournament(
        1,
        2,
        make_request("/api/registrations/tournament/1/team/2", method="POST"),
        current_user=admin_user,
        track_db=ScriptedDB(fetchone_values=[{"TournamentID": 1}, {"TeamID": 2}, None, {"nid": 9}]),
        auth_db=ScriptedDB(),
    )
    assert register_result["data"]["reg_id"] == 9
    unregister_result = registration.unregister_team_from_tournament(
        1,
        2,
        make_request("/api/registrations/tournament/1/team/2", method="DELETE"),
        current_user=admin_user,
        track_db=ScriptedDB(fetchone_values=[{"RegID": 9}]),
        auth_db=ScriptedDB(),
    )
    assert unregister_result["success"] is True
    add_result = registration.add_team_to_event(
        1,
        2,
        make_request("/api/registrations/event/1/team/2", method="POST"),
        current_user=admin_user,
        track_db=ScriptedDB(
            fetchone_values=[
                {"EventID": 1, "TournamentID": 1, "SportID": 4},
                {"TeamID": 2, "SportID": 4},
                {"RegID": 9},
                None,
                {"nid": 11},
            ]
        ),
        auth_db=ScriptedDB(),
    )
    assert add_result["data"]["participation_id"] == 11
    with pytest.raises(HTTPException) as exc_info:
        registration.add_team_to_event(
            1,
            3,
            make_request("/api/registrations/event/1/team/3", method="POST"),
            current_user=admin_user,
            track_db=ScriptedDB(
                fetchone_values=[
                    {"EventID": 1, "TournamentID": None, "SportID": 4},
                    {"TeamID": 3, "SportID": 2},
                ]
            ),
            auth_db=ScriptedDB(),
        )
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Team sport does not match the event sport."
    remove_result = registration.remove_team_from_event(
        1,
        2,
        make_request("/api/registrations/event/1/team/2", method="DELETE"),
        current_user=admin_user,
        track_db=ScriptedDB(fetchone_values=[{"ParticipationID": 11}]),
        auth_db=ScriptedDB(),
    )
    assert remove_result["success"] is True
    audit_log_result = admin.get_audit_log(limit=10, current_user=admin_user, db=ScriptedDB(fetchall_values=[[{"timestamp": "2024-03-01 10:00:00.000"}]]))
    assert audit_log_result["success"] is True
    verify_result = admin.verify_audit(current_user=admin_user, db=ScriptedDB())
    assert verify_result["data"]["intact"] is True
    entries = [
        {
            "log_id": 1,
            "timestamp": "2024-03-01 10:00:00.000",
            "user_id": 1,
            "username": "admin",
            "action": "SELECT",
            "table_name": "Member",
            "record_id": "1",
            "status": "SUCCESS",
            "details": None,
            "ip_address": "127.0.0.1",
            "entry_hash": "bad-hash",
        }
    ]
    chain_result = audit.verify_audit_chain(ScriptedDB(fetchall_values=[entries]))
    assert chain_result["intact"] is False


def test_member_create_auto_generates_id_and_rolls_back_auth_failure(admin_user, monkeypatch):
    from app.routers import members
    monkeypatch.setattr(members, "write_audit_log", lambda *args, **kwargs: None)
    track_db = ScriptedDB(fetchone_values=[{"nid": 42}])
    auth_db = ScriptedDB(
        execute_side_effects=[Exception("Duplicate entry 'runner42' for key 'username'")]
    )
    result = members.create_member(
        members.MemberCreate(
            name="Auto Runner",
            age=20,
            email="auto@example.com",
            contact_number="+919876543210",
            gender="F",
            role="Player",
            join_date="2024-02-01",
            username="runner42",
            password="secret",
        ),
        make_request("/api/members", method="POST"),
        current_user=admin_user,
        track_db=track_db,
        auth_db=auth_db,
    )
    assert result["success"] is False
    assert any("SELECT COALESCE(MAX(MemberID), 0) + 1 AS nid FROM Member" in query for query, _ in track_db.executed)
    assert any("DELETE FROM Member WHERE MemberID = %s" in query for query, _ in track_db.executed)


def test_team_detail_returns_linked_events(admin_user, monkeypatch):
    from app.routers import teams
    monkeypatch.setattr(teams, "write_audit_log", lambda *args, **kwargs: None)
    result = teams.get_team(
        1,
        make_request("/api/teams/1"),
        current_user=admin_user,
        track_db=ScriptedDB(
            fetchone_values=[{"TeamID": 1, "TeamName": "Sprinters", "SportID": 1, "CoachID": 2}],
            fetchall_values=[
                [{"MemberID": 1, "Name": "Ada Runner", "Role": "Player", "JoinDate": "2024-01-10"}],
                [{"EventID": 7, "EventName": "Final", "EventDate": "2024-03-01", "Status": "Scheduled"}],
            ],
        ),
        auth_db=ScriptedDB(),
    )
    assert result["data"]["events"][0]["EventID"] == 7


def test_get_event_filters_eligible_teams_for_coach(coach_user, monkeypatch):
    from app.routers import events
    monkeypatch.setattr(events, "write_audit_log", lambda *args, **kwargs: None)
    track_db = ScriptedDB(
        fetchone_values=[
            {
                "EventID": 1,
                "EventName": "Heat",
                "EventDate": "2024-03-01",
                "StartTime": "10:00:00",
                "EndTime": "11:00:00",
                "TournamentID": 1,
                "SportID": 1,
            }
        ],
        fetchall_values=[[], []],
    )
    events.get_event(
        1,
        make_request("/api/events/1"),
        current_user=coach_user,
        track_db=track_db,
        auth_db=ScriptedDB(),
    )
    assert "t.CoachID = %s" in track_db.executed[2][0]
    assert track_db.executed[2][1] == (1, 1, coach_user["member_id"])


def test_create_equipment_endpoint(admin_user, monkeypatch):
    from app.routers import equipment
    monkeypatch.setattr(equipment, "write_audit_log", lambda *args, **kwargs: None)
    result = equipment.create_equipment(
        equipment.EquipmentCreate(
            equipment_name="Training Cone",
            total_quantity=12,
            equipment_condition="New",
            sport_id=1,
        ),
        make_request("/api/equipment", method="POST"),
        current_user=admin_user,
        track_db=ScriptedDB(fetchone_values=[{"nid": 15}]),
        auth_db=ScriptedDB(),
    )
    assert result["success"] is True
    assert result["data"]["equipment_id"] == 15


def test_verify_audit_chain_detects_prev_hash_tampering():
    from app.services import audit
    entry = {
        "log_id": 2,
        "timestamp": "2024-03-01 10:00:00.000",
        "user_id": 1,
        "username": "admin",
        "action": "SELECT",
        "table_name": "Member",
        "record_id": "1",
        "status": "SUCCESS",
        "details": None,
        "ip_address": "127.0.0.1",
        "prev_hash": "tampered-prev-hash",
        "entry_hash": audit._compute_entry_hash(
            "2024-03-01 10:00:00.000",
            1,
            "admin",
            "SELECT",
            "Member",
            "1",
            "SUCCESS",
            None,
            "127.0.0.1",
            "tampered-prev-hash",
        ),
    }
    result = audit.verify_audit_chain(ScriptedDB(fetchall_values=[[entry]]))
    assert result["intact"] is False
    assert result["tampered_at_log_id"] == 2
