"""Who may call what. Every /api route must appear in the table below, so adding a route forces a decision."""
import pytest

from conftest import login

BODY_PUT_ATTENDANCE = {"date": "2026-10-13", "records": [{"person_id": 1, "status": 1}]}

# (method, route as Flask spells it, a concrete URL, JSON body)
ADMIN_ONLY = [
    ("GET", "/api/users", "/api/users", None),
    ("POST", "/api/users", "/api/users", {"username": "newbie", "password": "password123"}),
    ("DELETE", "/api/users/<int:user_id>", "/api/users/{bob}", None),
    ("POST", "/api/table_persons", "/api/table_persons", {"name": "Zed"}),
    ("PATCH", "/api/people/<int:person_id>", "/api/people/1", {"department": "Finance"}),
    ("PUT", "/api/attendance", "/api/attendance", BODY_PUT_ATTENDANCE),
    ("GET", "/api/attendance", "/api/attendance?date=2026-10-13", None),
    ("GET", "/api/export/attendance.csv", "/api/export/attendance.csv", None),
    ("GET", "/api/people", "/api/people", None),
    ("GET", "/api/predict", "/api/predict?date=2030-01-07", None),
    ("GET", "/api/evaluate", "/api/evaluate", None),
    ("POST", "/api/users/<int:user_id>/password", "/api/users/{bob}/password", {"password": "temporary-pass"}),
    ("POST", "/api/users/bulk", "/api/users/bulk", {"person_ids": [1]}),
    ("POST", "/api/users/<int:user_id>/department-request", "/api/users/{bob}/department-request", {"action": "dismiss"}),
]

# Open to any logged-in user. Everyone's names and predictions are admin-only (see ADMIN_ONLY above); what is left
# here is a user's own page: who they are, today's date, their own attendance and the head-count.
ANY_LOGIN = [
    ("GET", "/api/me", "/api/me", None),
    ("POST", "/api/me/password", "/api/me/password", {"current_password": "password123", "new_password": "another-pass1"}),
    ("POST", "/api/logout", "/api/logout", None),
    ("GET", "/api/departments", "/api/departments", None),
    ("GET", "/api/today", "/api/today", None),
    ("GET", "/api/me/attendance", "/api/me/attendance", None),
]

# The head-count is for admins and for logins linked to a person. A login with no person behind it (never linked,
# or its person was removed) gets 403.
LINKED_OR_ADMIN = [
    ("GET", "/api/attendance/summary", "/api/attendance/summary", None),
]

# Open to any login, but it can still answer 403 for a business reason (not linked yet, cutoff passed).
# test_self_service.py covers those answers.
OWN_ATTENDANCE = ("PUT", "/api/me/attendance", "/api/me/attendance", {"status": 1})

PUBLIC = [
    ("POST", "/api/login"),
    ("POST", "/api/register"),             # answers 403 unless sign-up is open (test_auth.py)
    ("GET", "/api/auth/options"),
]


def call(client, method, url, body, world):
    # replace, not str.format: any other brace in a URL (say a {day} placeholder) is left alone instead of raising KeyError
    url = url.replace("{bob}", str(world.users["bob"]))
    return client.open(url, method=method, json=body)


def test_every_route_is_in_the_table(flask_app):
    registered = {
        (method, rule.rule)
        for rule in flask_app.url_map.iter_rules() if rule.rule.startswith("/api/")
        for method in rule.methods - {"HEAD", "OPTIONS"}
    }
    listed = {(m, route) for m, route, _, _ in ADMIN_ONLY + ANY_LOGIN + LINKED_OR_ADMIN + [OWN_ATTENDANCE]} | set(PUBLIC)
    assert registered == listed, (
        f"not in the table: {sorted(registered - listed)}; in the table but not registered: {sorted(listed - registered)}"
    )


@pytest.mark.parametrize("method,route,url,body", ADMIN_ONLY + ANY_LOGIN + LINKED_OR_ADMIN + [OWN_ATTENDANCE],
                         ids=lambda v: v if isinstance(v, str) else "")
def test_anonymous_gets_401(world, method, route, url, body):
    response = call(login(world.app), method, url, body, world)
    assert response.status_code == 401
    assert response.get_json()["code"] == 401


@pytest.mark.parametrize("method,route,url,body", ADMIN_ONLY, ids=lambda v: v if isinstance(v, str) else "")
def test_non_admin_gets_403_on_admin_routes(world, method, route, url, body):
    response = call(login(world.app, "alice"), method, url, body, world)
    assert response.status_code == 403
    assert response.get_json()["code"] == 403


@pytest.mark.parametrize("method,route,url,body", ADMIN_ONLY, ids=lambda v: v if isinstance(v, str) else "")
def test_admin_is_let_through_admin_routes(world, method, route, url, body):
    response = call(login(world.app, "admin"), method, url, body, world)
    assert response.status_code not in (401, 403), response.get_data(as_text=True)


@pytest.mark.parametrize("method,route,url,body", ANY_LOGIN, ids=lambda v: v if isinstance(v, str) else "")
@pytest.mark.parametrize("who", ["alice", "bob", "admin"])
def test_any_login_is_let_through(world, who, method, route, url, body):
    response = call(login(world.app, who), method, url, body, world)
    assert response.status_code not in (401, 403), f"{who}: {response.get_data(as_text=True)}"


@pytest.mark.parametrize("method,route,url,body", LINKED_OR_ADMIN, ids=lambda v: v if isinstance(v, str) else "")
@pytest.mark.parametrize("who", ["alice", "admin"])      # alice is linked to Ana
def test_linked_logins_and_admins_are_let_through_the_head_count(world, who, method, route, url, body):
    response = call(login(world.app, who), method, url, body, world)
    assert response.status_code == 200, f"{who}: {response.get_data(as_text=True)}"


@pytest.mark.parametrize("method,route,url,body", LINKED_OR_ADMIN, ids=lambda v: v if isinstance(v, str) else "")
def test_a_login_with_no_person_gets_403_on_the_head_count(world, method, route, url, body):
    response = call(login(world.app, "bob"), method, url, body, world)      # bob is not linked
    assert response.status_code == 403
    assert "total" not in response.get_data(as_text=True)


def test_a_refused_non_admin_changes_nothing(world):
    from users import AttendanceRecord, Person, User

    client = login(world.app, "alice")
    client.put("/api/attendance", json=BODY_PUT_ATTENDANCE)
    client.post("/api/table_persons", json={"name": "Zed"})
    client.patch(f"/api/people/{world.people['Ben']}", json={"department": "Manpower"})
    client.delete(f"/api/users/{world.users['bob']}")
    client.post("/api/users", json={"username": "newbie", "password": "password123", "is_admin": True})
    with world.app.app_context():
        assert AttendanceRecord.query.count() == 0
        assert Person.query.count() == 3
        assert Person.query.filter_by(name="Ben").one().department == "Finance"
        assert {u.username for u in User.query.all()} == {"admin", "alice", "bob"}


def test_an_admin_made_by_registration_is_impossible(flask_app):
    from users import User

    flask_app.config["ALLOW_REGISTRATION"] = True
    client = flask_app.test_client()
    client.post("/api/register", json={"username": "sneaky", "password": "password123",
                                       "confirmPassword": "password123", "is_admin": True})
    with flask_app.app_context():
        assert User.query.filter_by(username="sneaky").one().is_admin is False


def test_admins_see_who_is_linked_to_whom(world):
    people = login(world.app, "admin").get("/api/people").get_json()
    assert next(p for p in people if p["name"] == "Ana")["user_id"] == world.users["alice"]
    assert next(p for p in people if p["name"] == "Ben")["user_id"] is None


def test_a_user_cannot_read_anyones_name_or_prediction(world):
    """The roster, the predictions and the accuracy report all name people, so none may leak to a non-admin,
    not even in the error body. (alice is linked to Ana, so her own name is the one that must stay out too.)"""
    from conftest import make_record
    from datetime import date, timedelta

    start = date(2026, 8, 31)
    for week in range(3):                                   # enough history that predictions exist
        for offset in range(5):
            make_record(world.app, world.people["Ana"], start + timedelta(days=7 * week + offset), 1)
    client = login(world.app, "alice")
    for url in ("/api/people", "/api/predict?date=2030-01-07", "/api/evaluate"):
        response = client.get(url)
        assert response.status_code == 403, url
        body = response.get_data(as_text=True)
        assert not any(name in body for name in ("Ana", "Ben", "Cai", "Finance", "Manpower")), url
    admin = login(world.app, "admin")
    assert "Ana" in admin.get("/api/predict?date=2030-01-07").get_data(as_text=True)    # the data exists, so the 403 is what hid it


def test_user_list_never_contains_password_hashes(world):
    response = login(world.app, "admin").get("/api/users")
    assert all("password" not in entry for entry in response.get_json())     # no field of that name
    body = response.get_data(as_text=True)
    assert "pbkdf2" not in body and "scrypt" not in body                     # and no hash in any value