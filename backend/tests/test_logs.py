"""Every admin change is written to the activity log, in the same transaction as the change itself.

What each action writes (action name, then old -> new):
  add a login ........... login_created             -> "user" or "admin"
  delete a login ........ login_deleted             "user"/"admin" ->
  reset a password ...... password_reset            (never the password)
  bulk logins ........... login_created (one per person)
  department request .... department_request_confirmed  old dept -> new dept
                          department_request_dismissed  requested dept ->
  add a person .......... person_added              -> department
  change a department ... department_changed        old -> new
  rename ................ person_renamed            old name -> new name
  remove / restore ...... person_removed (with the login it unlinked) / person_restored
  link / unlink a login . login_linked              previous person -> person
                          login_unlinked            person ->
  attendance (admin) .... attendance_set            "2026-10-13 Out" -> "2026-10-13 In"
                          attendance_removed        "2026-10-13 In" ->
  command line .......... login_created / password_reset / backup_restored, done by "system"
"""
import pytest
from sqlalchemy import event

import activity_log
from activity_log import ActivityLog, verify_chain
from conftest import login, make_record
from extensions import db

NOW = "2026-10-14T11:00:00+08:00"        # conftest's fixed clock: Wed 14 Oct 2026, 11:00 in UTC+8
DAY = "2026-10-13"                       # the Tuesday before


@pytest.fixture(autouse=True)
def key(monkeypatch):
    monkeypatch.setenv("ACTIVITY_LOG_KEY", "test-key")
    monkeypatch.delenv("ENV", raising=False)


@pytest.fixture
def w(world):
    """The usual world (admin, alice linked to Ana, bob unlinked; Ana, Ben, Cai) with the log's triggers installed
    as in production, and a pending department request on alice."""
    from users import User
    with world.app.app_context():
        activity_log.ensure_activity_log()
        User.query.filter_by(username="alice").one().requested_department = "Manpower"
        db.session.commit()
    return world


FIELDS = ("seq", "admin_id", "admin_name", "logged_at", "action", "person_id", "person_name", "login_id",
          "login_name", "old_value", "new_value")


def rows(app):
    with app.app_context():
        return [{f: getattr(r, f) for f in FIELDS} for r in ActivityLog.query.order_by(ActivityLog.seq)]


def logged(w, send, who="admin"):
    """Run send(client) as `who` and return (response, only the log rows it added)."""
    before = len(rows(w.app))
    response = send(login(w.app, who))
    return response, rows(w.app)[before:]


def brief(new_rows):
    """(action, person_name, login_name, old, new) for each row: the part that says what happened."""
    return [(r["action"], r["person_name"], r["login_name"], r["old_value"], r["new_value"]) for r in new_rows]


def state(app):
    """Everything an admin action can change, so a refused or failed one can be shown to change nothing."""
    from users import AttendanceRecord, Person, User
    with app.app_context():
        return (
            sorted((u.username, u.person_id, u.is_admin, u.password, u.requested_department) for u in User.query),
            sorted((p.name, p.department, p.former) for p in Person.query),
            sorted((r.person_id, r.date.isoformat(), r.status) for r in AttendanceRecord.query),
        )


def actions(w):
    """One request that works, for each admin action that writes to the log."""
    u, p = w.users, w.people
    return {
        "add_login": lambda c: c.post("/api/users", json={"username": "newbie", "password": "password123"}),
        "delete_login": lambda c: c.delete(f"/api/users/{u['bob']}"),
        "reset_password": lambda c: c.post(f"/api/users/{u['bob']}/password", json={}),
        "bulk_logins": lambda c: c.post("/api/users/bulk", json={"person_ids": [p["Ben"], p["Cai"]]}),
        "confirm_department_request": lambda c: c.post(f"/api/users/{u['alice']}/department-request",
                                                       json={"action": "confirm"}),
        "dismiss_department_request": lambda c: c.post(f"/api/users/{u['alice']}/department-request",
                                                       json={"action": "dismiss"}),
        "add_person": lambda c: c.post("/api/table_persons", json={"name": "Zed", "department": "Finance"}),
        "change_department": lambda c: c.patch(f"/api/people/{p['Ben']}", json={"department": "Manpower"}),
        "rename": lambda c: c.patch(f"/api/people/{p['Ben']}", json={"name": "Benjamin"}),
        "remove_person": lambda c: c.patch(f"/api/people/{p['Ana']}", json={"former": True}),
        "link_login": lambda c: c.patch(f"/api/people/{p['Ben']}", json={"user_id": u["bob"]}),
        "unlink_login": lambda c: c.patch(f"/api/people/{p['Ana']}", json={"user_id": None}),
        "record_attendance": lambda c: c.put("/api/attendance", json={"date": DAY, "records": [
            {"person_id": p["Ana"], "status": 1}]}),
    }


ACTION_NAMES = [
    "add_login", "delete_login", "reset_password", "bulk_logins", "confirm_department_request",
    "dismiss_department_request", "add_person", "change_department", "rename", "remove_person", "link_login",
    "unlink_login", "record_attendance",
]


def test_the_action_names_match_the_actions_table(w):
    assert sorted(actions(w)) == sorted(ACTION_NAMES)


# ---- every write route is accounted for -------------------------------------------------------------

LOGGED_ROUTES = {
    ("POST", "/api/users"), ("DELETE", "/api/users/<int:user_id>"), ("POST", "/api/users/<int:user_id>/password"),
    ("POST", "/api/users/bulk"), ("POST", "/api/users/<int:user_id>/department-request"),
    ("POST", "/api/table_persons"), ("PATCH", "/api/people/<int:person_id>"), ("PUT", "/api/attendance"),
}
NOT_LOGGED_ROUTES = {                                   # each one with the reason it is not an admin change
    ("POST", "/api/login"): "not a change",
    ("POST", "/api/logout"): "not a change",
    ("POST", "/api/register"): "open sign-up (off by default): the new user is not an admin",
    ("POST", "/api/me/password"): "a user changing their own password",
    ("PUT", "/api/me/attendance"): "a user's own attendance, not an admin change",
}
LOGGED_COMMANDS = {"create-admin", "set-password", "import-backup"}
NOT_LOGGED_COMMANDS = {"export-backup": "reads, changes nothing"}


def test_every_route_that_writes_is_logged_or_has_a_reason_not_to_be(flask_app):
    writes = {
        (method, rule.rule)
        for rule in flask_app.url_map.iter_rules() if rule.rule.startswith("/api/")
        for method in rule.methods - {"GET", "HEAD", "OPTIONS"}
    }
    known = LOGGED_ROUTES | set(NOT_LOGGED_ROUTES)
    assert writes == known, (
        f"a write route is neither logged nor excused: {sorted(writes - known)}; "
        f"listed here but not registered: {sorted(known - writes)}"
    )


def test_every_command_is_logged_or_has_a_reason_not_to_be(flask_app):
    commands = set(flask_app.cli.commands)
    known = LOGGED_COMMANDS | set(NOT_LOGGED_COMMANDS)
    assert commands == known, f"a command is neither logged nor excused: {sorted(commands - known)}"


# ---- what each action writes -----------------------------------------------------------------------

def test_adding_a_login(w):
    response, new = logged(w, lambda c: c.post("/api/users", json={"username": "newbie", "password": "password123"}))
    assert response.status_code == 201
    assert brief(new) == [("login_created", None, "newbie", None, "user")]
    assert new[0]["login_id"] == response.get_json()["id"]
    assert (new[0]["admin_id"], new[0]["admin_name"], new[0]["logged_at"]) == (w.users["admin"], "admin", NOW)


def test_adding_an_admin_login_says_admin(w):
    _, new = logged(w, lambda c: c.post("/api/users", json={"username": "boss", "password": "password123",
                                                            "is_admin": True}))
    assert brief(new) == [("login_created", None, "boss", None, "admin")]


def test_the_log_names_the_admin_who_did_it(w, add_users):
    ids = add_users(("boss", True))
    _, new = logged(w, lambda c: c.post("/api/table_persons", json={"name": "Zed"}), who="boss")
    assert (new[0]["admin_id"], new[0]["admin_name"]) == (ids["boss"], "boss")


def test_deleting_a_login(w):
    response, new = logged(w, lambda c: c.delete(f"/api/users/{w.users['bob']}"))
    assert response.status_code == 200
    assert brief(new) == [("login_deleted", None, "bob", "user", None)]
    assert new[0]["login_id"] == w.users["bob"]


def test_deleting_a_linked_login_names_its_person_and_deleting_an_admin_says_admin(w, add_users):
    boss = add_users(("boss", True))["boss"]
    _, new = logged(w, lambda c: c.delete(f"/api/users/{w.users['alice']}"))
    assert brief(new) == [("login_deleted", "Ana", "alice", "user", None)]
    assert new[0]["person_id"] == w.people["Ana"]
    _, new = logged(w, lambda c: c.delete(f"/api/users/{boss}"))
    assert brief(new) == [("login_deleted", None, "boss", "admin", None)]


def test_resetting_a_password_never_writes_the_password(w):
    response, new = logged(w, lambda c: c.post(f"/api/users/{w.users['alice']}/password", json={}))
    assert response.status_code == 200
    assert brief(new) == [("password_reset", "Ana", "alice", None, None)]
    assert response.get_json()["temporary_password"] not in str(new)


def test_bulk_logins_log_one_row_per_login(w):
    response, new = logged(w, lambda c: c.post("/api/users/bulk", json={"person_ids": [w.people["Cai"], w.people["Ben"]]}))
    assert response.status_code == 201
    made = {entry["username"]: entry for entry in response.get_json()["created"]}
    assert sorted(brief(new)) == sorted([("login_created", "Ben", "ben", None, "user"),
                                         ("login_created", "Cai", "cai", None, "user")])
    assert {r["person_id"] for r in new} == {w.people["Ben"], w.people["Cai"]}
    assert not any(entry["temporary_password"] in str(new) for entry in made.values())


def test_confirming_a_department_request(w):
    response, new = logged(w, lambda c: c.post(f"/api/users/{w.users['alice']}/department-request",
                                              json={"action": "confirm"}))
    assert response.status_code == 200
    assert brief(new) == [("department_request_confirmed", "Ana", "alice", "Finance", "Manpower")]


def test_dismissing_a_department_request(w):
    _, new = logged(w, lambda c: c.post(f"/api/users/{w.users['alice']}/department-request",
                                       json={"action": "dismiss"}))
    assert brief(new) == [("department_request_dismissed", "Ana", "alice", "Manpower", None)]


def test_adding_a_person(w):
    response, new = logged(w, lambda c: c.post("/api/table_persons", json={"name": "Zed", "department": "Finance"}))
    assert response.status_code == 201
    assert brief(new) == [("person_added", "Zed", None, None, "Finance")]
    assert new[0]["person_id"] == response.get_json()["id"]


def test_changing_a_department_and_not_changing_it(w):
    ben = f"/api/people/{w.people['Ben']}"
    _, new = logged(w, lambda c: c.patch(ben, json={"department": "Manpower"}))
    assert brief(new) == [("department_changed", "Ben", None, "Finance", "Manpower")]
    _, new = logged(w, lambda c: c.patch(ben, json={"department": "Manpower"}))
    assert new == []                                                    # nothing changed, so nothing to record


def test_renaming_a_person_and_giving_the_same_name_back(w):
    ben = f"/api/people/{w.people['Ben']}"
    _, new = logged(w, lambda c: c.patch(ben, json={"name": "Benjamin"}))
    assert brief(new) == [("person_renamed", "Benjamin", None, "Ben", "Benjamin")]
    _, new = logged(w, lambda c: c.patch(ben, json={"name": "Benjamin"}))
    assert new == []


def test_removing_a_person_records_the_login_it_unlinked_and_restoring_records_the_restore(w):
    ana = f"/api/people/{w.people['Ana']}"
    _, new = logged(w, lambda c: c.patch(ana, json={"former": True}))
    assert brief(new) == [("person_removed", "Ana", "alice", None, None)]
    assert new[0]["login_id"] == w.users["alice"]
    _, new = logged(w, lambda c: c.patch(ana, json={"former": False}))
    assert brief(new) == [("person_restored", "Ana", None, None, None)]
    _, new = logged(w, lambda c: c.patch(f"/api/people/{w.people['Ben']}", json={"former": True}))
    assert brief(new) == [("person_removed", "Ben", None, None, None)]    # nobody was linked, so no login is named


def test_linking_a_login(w):
    _, new = logged(w, lambda c: c.patch(f"/api/people/{w.people['Ben']}", json={"user_id": w.users["bob"]}))
    assert brief(new) == [("login_linked", "Ben", "bob", None, "Ben")]
    _, new = logged(w, lambda c: c.patch(f"/api/people/{w.people['Ben']}", json={"user_id": w.users["bob"]}))
    assert new == []                                                    # already linked: no change


def test_moving_a_login_from_one_person_to_another_says_where_it_came_from(w):
    _, new = logged(w, lambda c: c.patch(f"/api/people/{w.people['Ben']}", json={"user_id": w.users["alice"]}))
    assert brief(new) == [("login_linked", "Ben", "alice", "Ana", "Ben")]


def test_unlinking_a_login(w):
    ana = f"/api/people/{w.people['Ana']}"
    _, new = logged(w, lambda c: c.patch(ana, json={"user_id": None}))
    assert brief(new) == [("login_unlinked", "Ana", "alice", "Ana", None)]
    _, new = logged(w, lambda c: c.patch(ana, json={"user_id": None}))
    assert new == []


def test_replacing_a_persons_login_is_an_unlink_then_a_link(w):
    _, new = logged(w, lambda c: c.patch(f"/api/people/{w.people['Ana']}", json={"user_id": w.users["bob"]}))
    assert brief(new) == [("login_unlinked", "Ana", "alice", "Ana", None), ("login_linked", "Ana", "bob", None, "Ana")]


def test_removing_and_unlinking_in_one_request_records_the_unlink_once(w):
    _, new = logged(w, lambda c: c.patch(f"/api/people/{w.people['Ana']}", json={"former": True, "user_id": None}))
    assert brief(new) == [("person_removed", "Ana", "alice", None, None)]


def test_restoring_and_linking_in_one_request_records_both(w):
    ana = f"/api/people/{w.people['Ana']}"
    login(w.app, "admin").patch(ana, json={"former": True})
    _, new = logged(w, lambda c: c.patch(ana, json={"former": False, "user_id": w.users["bob"]}))
    assert brief(new) == [("person_restored", "Ana", None, None, None), ("login_linked", "Ana", "bob", None, "Ana")]


def test_several_changes_in_one_request_each_get_their_own_row_in_a_fixed_order(w):
    _, new = logged(w, lambda c: c.patch(f"/api/people/{w.people['Ben']}",
                                         json={"name": "Benjamin", "department": "Manpower", "user_id": w.users["bob"]}))
    assert [r["action"] for r in new] == ["department_changed", "person_renamed", "login_linked"]


def test_attendance_set_changed_unchanged_and_removed(w):
    make_record(w.app, w.people["Ben"], DAY, 0)                         # Ben was Out
    make_record(w.app, w.people["Cai"], DAY, 2)                         # Cai was Partial Day
    ana, ben, cai = w.people["Ana"], w.people["Ben"], w.people["Cai"]
    response, new = logged(w, lambda c: c.put("/api/attendance", json={"date": DAY, "records": [
        {"person_id": ana, "status": 1},       # a new record
        {"person_id": ben, "status": 1},       # Out -> In
        {"person_id": cai, "status": 2},       # the same as before
    ]}))
    assert response.status_code == 200
    assert brief(new) == [("attendance_set", "Ana", None, None, f"{DAY} In"),
                          ("attendance_set", "Ben", None, f"{DAY} Out", f"{DAY} In")]
    _, new = logged(w, lambda c: c.put("/api/attendance", json={"date": DAY, "records": [
        {"person_id": ana, "status": None},    # removes Ana's record
        {"person_id": cai, "status": 0},       # Partial Day -> Out
        {"person_id": ben, "status": None},    # removes Ben's record (he was In after the first request)
    ]}))
    assert brief(new) == [("attendance_removed", "Ana", None, f"{DAY} In", None),
                          ("attendance_set", "Cai", None, f"{DAY} Partial Day", f"{DAY} Out"),
                          ("attendance_removed", "Ben", None, f"{DAY} In", None)]


def test_removing_a_record_that_does_not_exist_writes_nothing(w):
    response, new = logged(w, lambda c: c.put("/api/attendance", json={"date": DAY, "records": [
        {"person_id": w.people["Ana"], "status": None}]}))
    assert response.status_code == 200 and new == []


# ---- the command line, done by "system" -------------------------------------------------------------

def run(w, *args, input=None):
    return w.app.test_cli_runner().invoke(args=list(args), input=input)


def test_create_admin_on_the_command_line_is_logged_as_the_system(w):
    before = len(rows(w.app))
    result = run(w, "create-admin", "root2", input="password123\npassword123\n")
    assert result.exit_code == 0, result.output
    new = rows(w.app)[before:]
    assert brief(new) == [("login_created", None, "root2", None, "admin")]
    assert (new[0]["admin_id"], new[0]["admin_name"], new[0]["logged_at"]) == (None, "system", NOW)


def test_set_password_on_the_command_line_is_logged_without_the_password(w):
    before = len(rows(w.app))
    result = run(w, "set-password", "alice", input="long-enough-1\nlong-enough-1\n")
    assert result.exit_code == 0, result.output
    new = rows(w.app)[before:]
    assert brief(new) == [("password_reset", "Ana", "alice", None, None)]
    assert new[0]["admin_name"] == "system" and "long-enough-1" not in str(new)


def test_a_failed_command_writes_nothing(w):
    before = rows(w.app)
    assert run(w, "set-password", "nobody", input="long-enough-1\nlong-enough-1\n").exit_code != 0
    assert run(w, "set-password", "alice", input="short\nshort\n").exit_code != 0
    assert rows(w.app) == before


def test_importing_a_backup_is_logged_as_the_system(w, tmp_path):
    path = tmp_path / "backup.csv"
    path.write_text("person,department,date,status,former\r\nZed,Finance,2026-10-12,1,0\r\n", encoding="utf-8")
    before = len(rows(w.app))
    result = run(w, "import-backup", str(path), "--replace", "--yes")
    assert result.exit_code == 0, result.output
    new = rows(w.app)[before:]
    assert brief(new) == [("backup_restored", None, None, "replaced existing data", "1 people, 1 attendance records")]
    assert new[0]["admin_name"] == "system"


def test_a_refused_import_writes_nothing(w, tmp_path):
    path = tmp_path / "backup.csv"
    path.write_text("person,department,date,status,former\r\nZed,Finance,2026-10-12,1,0\r\n", encoding="utf-8")
    before = rows(w.app)
    assert run(w, "import-backup", str(path)).exit_code != 0            # the database already has people, and no --replace
    assert rows(w.app) == before


# ---- what must NOT be logged -----------------------------------------------------------------------

@pytest.mark.parametrize("name", ACTION_NAMES)
def test_a_non_admin_is_refused_and_nothing_is_written(w, name):
    before_state, before_rows = state(w.app), rows(w.app)
    response = actions(w)[name](login(w.app, "alice"))
    assert response.status_code == 403
    assert state(w.app) == before_state and rows(w.app) == before_rows


@pytest.mark.parametrize("name", ACTION_NAMES)
def test_an_anonymous_request_is_refused_and_nothing_is_written(w, name):
    before_state, before_rows = state(w.app), rows(w.app)
    assert actions(w)[name](login(w.app)).status_code == 401
    assert state(w.app) == before_state and rows(w.app) == before_rows


FAILING_REQUESTS = {
    "blank person name": lambda w, c: c.post("/api/table_persons", json={"name": " "}),
    "duplicate person": lambda w, c: c.post("/api/table_persons", json={"name": "Ana"}),
    "duplicate login": lambda w, c: c.post("/api/users", json={"username": "bob", "password": "password123"}),
    "short password": lambda w, c: c.post("/api/users", json={"username": "newbie", "password": "short"}),
    "delete your own login": lambda w, c: c.delete(f"/api/users/{w.users['admin']}"),
    "delete a missing login": lambda w, c: c.delete("/api/users/99999"),
    "reset your own password": lambda w, c: c.post(f"/api/users/{w.users['admin']}/password", json={}),
    "reset a missing login": lambda w, c: c.post("/api/users/99999/password", json={}),
    "bulk for a person who has a login": lambda w, c: c.post("/api/users/bulk", json={"person_ids": [w.people["Ben"], w.people["Ana"]]}),
    "bulk for nobody": lambda w, c: c.post("/api/users/bulk", json={"person_ids": []}),
    "request answered with nonsense": lambda w, c: c.post(f"/api/users/{w.users['alice']}/department-request", json={"action": "maybe"}),
    "request nobody made": lambda w, c: c.post(f"/api/users/{w.users['bob']}/department-request", json={"action": "dismiss"}),
    "patch with nothing in it": lambda w, c: c.patch(f"/api/people/{w.people['Ben']}", json={}),
    "patch a missing person": lambda w, c: c.patch("/api/people/99999", json={"name": "Q"}),
    "rename onto a taken name": lambda w, c: c.patch(f"/api/people/{w.people['Ben']}", json={"name": "Ana"}),
    "bad department": lambda w, c: c.patch(f"/api/people/{w.people['Ben']}", json={"department": "Nowhere"}),
    "link a missing login": lambda w, c: c.patch(f"/api/people/{w.people['Ben']}", json={"user_id": 99999}),
    "attendance for a future day": lambda w, c: c.put("/api/attendance", json={"date": "2026-10-30", "records": [
        {"person_id": w.people["Ana"], "status": 1}]}),
    "attendance with a bad status": lambda w, c: c.put("/api/attendance", json={"date": DAY, "records": [
        {"person_id": w.people["Ana"], "status": 7}]}),
    "attendance for an unknown person": lambda w, c: c.put("/api/attendance", json={"date": DAY, "records": [
        {"person_id": 99999, "status": 1}]}),
}


@pytest.mark.parametrize("name", sorted(FAILING_REQUESTS))
def test_a_request_that_fails_writes_nothing(w, name):
    before_state, before_rows = state(w.app), rows(w.app)
    response = FAILING_REQUESTS[name](w, login(w.app, "admin"))
    assert response.status_code in (400, 404, 409), response.get_data(as_text=True)
    assert state(w.app) == before_state and rows(w.app) == before_rows


def test_reading_writes_nothing(w):
    client = login(w.app, "admin")
    before = rows(w.app)
    for url in ("/api/people", "/api/people?include_former=1", "/api/users", f"/api/attendance?date={DAY}",
                "/api/attendance/summary", "/api/export/attendance.csv", "/api/predict?date=2030-01-07",
                "/api/evaluate", "/api/me", "/api/today", "/api/departments"):
        client.get(url)
    assert rows(w.app) == before == []


def test_a_users_own_changes_are_not_admin_changes_and_are_not_logged(w, set_clock):
    set_clock("2026-10-14T00:00:00")                                    # 08:00 local: before the 08:15 cutoff
    alice = login(w.app, "alice")
    assert alice.put("/api/me/attendance", json={"status": 1}).status_code == 200
    assert alice.post("/api/me/password", json={"current_password": "password123",
                                                "new_password": "another-pass1"}).status_code == 200
    assert rows(w.app) == []


# ---- the change and its log row are one transaction -------------------------------------------------

@pytest.mark.parametrize("name", ACTION_NAMES)
def test_if_the_log_cannot_be_written_the_change_is_not_saved(w, monkeypatch, name):
    def broken(*args, **kwargs):
        raise RuntimeError("the log is broken")
    monkeypatch.setattr(activity_log, "append_entry", broken)
    before_state = state(w.app)
    response = actions(w)[name](login(w.app, "admin"))
    assert response.status_code == 500
    assert state(w.app) == before_state
    assert rows(w.app) == []


@pytest.mark.parametrize("name", ACTION_NAMES)
def test_the_change_is_written_before_the_log_position_is_chosen(w, name):
    """With the change already written, SQLite holds its write lock when the log looks up the newest entry, so two
    admins acting at once cannot both pick the same position. This pins that order: first the change (INSERT, UPDATE
    or DELETE), then the lookup in activity_log."""
    client = login(w.app, "admin")
    with w.app.app_context():
        engine = db.engine
    seen = []

    def spy(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(engine, "before_cursor_execute", spy)
    try:
        response = actions(w)[name](client)
    finally:
        event.remove(engine, "before_cursor_execute", spy)
    assert response.status_code in (200, 201), response.get_data(as_text=True)
    first_write = next(i for i, s in enumerate(seen) if s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")))
    first_lookup = next(i for i, s in enumerate(seen) if "FROM activity_log" in s)
    assert first_write < first_lookup, seen


# ---- the whole log ---------------------------------------------------------------------------------

def test_a_busy_day_leaves_a_numbered_chain_that_verifies(w):
    client = login(w.app, "admin")
    for name in ("add_person", "change_department", "rename", "record_attendance", "reset_password",
                 "bulk_logins", "confirm_department_request", "remove_person", "delete_login", "add_login"):
        response = actions(w)[name](client)
        assert response.status_code in (200, 201), (name, response.get_data(as_text=True))
    log = rows(w.app)
    assert [r["seq"] for r in log] == list(range(1, 12))                # 10 requests, bulk logins made two rows
    with w.app.app_context():
        assert verify_chain() is None


def test_nothing_the_admin_types_is_trusted_to_end_up_in_a_password_column(w):
    """No row holds a password or a password hash, whatever route wrote it."""
    client = login(w.app, "admin")
    secret = "hunter2-hunter2"
    client.post("/api/users", json={"username": "newbie", "password": secret})
    client.post(f"/api/users/{w.users['bob']}/password", json={"password": "another-secret-9"})
    client.post("/api/users/bulk", json={"person_ids": [w.people["Ben"]]})
    run(w, "set-password", "alice", input="cli-secret-123\ncli-secret-123\n")
    text = str(rows(w.app))
    assert len(rows(w.app)) == 4
    for forbidden in (secret, "another-secret-9", "cli-secret-123", "pbkdf2", "scrypt"):
        assert forbidden not in text