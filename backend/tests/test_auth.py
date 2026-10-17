"""Login, logout, sign-up and account management."""
import pytest

from conftest import PASSWORD, login


def test_login_works_and_me_reports_the_user(world):
    client = login(world.app, "admin")
    me = client.get("/api/me").get_json()
    assert me["username"] == "admin" and me["is_admin"] is True
    assert login(world.app, "alice").get("/api/me").get_json()["is_admin"] is False


def test_wrong_password_and_unknown_user_look_the_same(world):
    client = world.app.test_client()
    wrong = client.post("/api/login", json={"username": "admin", "password": "nope-nope"})
    unknown = client.post("/api/login", json={"username": "nobody", "password": PASSWORD})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.get_json()["error"] == unknown.get_json()["error"]  # does not reveal which usernames exist


@pytest.mark.parametrize("body", [{}, {"username": "admin"}, {"password": PASSWORD}, {"username": "", "password": ""}])
def test_login_needs_both_fields(world, body):
    assert world.app.test_client().post("/api/login", json=body).status_code == 400


def test_logout_ends_the_session(world):
    client = login(world.app, "alice")
    assert client.post("/api/logout").status_code == 200
    assert client.get("/api/me").status_code == 401


# ---- sign-up (closed unless ALLOW_REGISTRATION is set) --------------------------------------------

def register(client, **overrides):
    body = {"username": "newbie", "password": "password123", "confirmPassword": "password123"}
    body.update(overrides)
    return client.post("/api/register", json=body)


@pytest.fixture
def open_signup(flask_app):
    flask_app.config["ALLOW_REGISTRATION"] = True
    return flask_app


def test_signup_is_closed_by_default_and_creates_nothing(flask_app):
    from users import User

    response = register(flask_app.test_client())
    assert response.status_code == 403 and "closed" in response.get_json()["error"]
    with flask_app.app_context():
        assert User.query.count() == 0


def test_the_setting_opens_signup_from_the_environment(monkeypatch, make_app):
    monkeypatch.setenv("ALLOW_REGISTRATION", "true")
    assert register(make_app("opened").test_client()).status_code == 200
    monkeypatch.setenv("ALLOW_REGISTRATION", "no")
    assert register(make_app("closed").test_client(), username="other").status_code == 403


def test_the_login_screen_can_ask_whether_signup_is_open(flask_app):
    closed = flask_app.test_client().get("/api/auth/options").get_json()
    assert closed == {"registration_open": False, "departments": [], "default_department": "Holding"}
    flask_app.config["ALLOW_REGISTRATION"] = True
    opened = flask_app.test_client().get("/api/auth/options").get_json()
    assert opened["registration_open"] is True and "Finance" in opened["departments"] and "Holding" in opened["departments"]


def test_registration_makes_an_unlinked_non_admin_login_with_a_hashed_password(open_signup):
    from users import User

    flask_app = open_signup
    assert register(flask_app.test_client()).status_code == 200
    with flask_app.app_context():
        user = User.query.filter_by(username="newbie").one()
        assert user.is_admin is False and user.person_id is None
        assert user.password != "password123" and user.password.startswith(("scrypt:", "pbkdf2:"))
    assert login(flask_app, "newbie").get("/api/me").status_code == 200


def test_registration_trims_the_username(open_signup):
    from users import User

    flask_app = open_signup
    register(flask_app.test_client(), username="  zed  ")
    with flask_app.app_context():
        assert User.query.filter_by(username="zed").count() == 1


@pytest.mark.parametrize("overrides", [
    {"username": ""}, {"username": "   "}, {"password": ""}, {"confirmPassword": ""},
    {"password": "short", "confirmPassword": "short"},       # under 8 characters
    {"confirmPassword": "different123"},                     # does not match
    {"username": 5}, {"username": "x" * 81},                 # not text / longer than the column
    {"password": "p" * 129, "confirmPassword": "p" * 129},   # longer than a password may be
    {"department": "Narnia"}, {"department": 7},             # not one of the departments / not text
], ids=["no-name", "blank-name", "no-password", "no-confirm", "too-short", "mismatch",
        "name-not-text", "name-too-long", "password-too-long", "unknown-department", "department-not-text"])
def test_registration_rejects_bad_input(open_signup, overrides):
    from users import User

    flask_app = open_signup
    assert register(flask_app.test_client(), **overrides).status_code == 400
    with flask_app.app_context():
        assert User.query.count() == 0


def test_registration_rejects_a_taken_username(world):
    world.app.config["ALLOW_REGISTRATION"] = True
    assert register(world.app.test_client(), username="alice").status_code == 400


def test_a_department_request_is_stored_with_the_login_and_no_person_is_made(world):
    from users import Person, User

    world.app.config["ALLOW_REGISTRATION"] = True
    assert register(world.app.test_client(), department="Finance").status_code == 200
    with world.app.app_context():
        user = User.query.filter_by(username="newbie").one()
        assert user.requested_department == "Finance" and user.person_id is None
        assert Person.query.count() == 3                         # nobody was added to the roster


def test_no_department_means_no_request(world):
    from users import User

    world.app.config["ALLOW_REGISTRATION"] = True
    register(world.app.test_client(), department="   ")
    register(world.app.test_client(), username="other")
    with world.app.app_context():
        assert User.query.filter(User.username.in_(["newbie", "other"])).filter(User.requested_department.isnot(None)).count() == 0


# ---- admin account management --------------------------------------------------------------------

def test_admin_can_create_accounts_with_or_without_admin_rights(world):
    from users import User

    admin = login(world.app, "admin")
    made = admin.post("/api/users", json={"username": "carol", "password": "password123"})
    assert made.status_code == 201
    admin.post("/api/users", json={"username": "dave", "password": "password123", "is_admin": True})
    with world.app.app_context():
        assert User.query.filter_by(username="carol").one().is_admin is False
        assert User.query.filter_by(username="dave").one().is_admin is True


@pytest.mark.parametrize("body,status", [
    ({"username": "", "password": "password123"}, 400),
    ({"username": "carol", "password": ""}, 400),
    ({"username": "carol", "password": "short"}, 400),
    ({"username": "alice", "password": "password123"}, 409),   # taken
], ids=["no-name", "no-password", "too-short", "taken"])
def test_admin_create_account_validation(world, body, status):
    assert login(world.app, "admin").post("/api/users", json=body).status_code == status


def test_admin_cannot_delete_their_own_account(world):
    assert login(world.app, "admin").delete(f"/api/users/{world.users['admin']}").status_code == 400


def test_deleting_an_unknown_account_is_404(world):
    assert login(world.app, "admin").delete("/api/users/99999").status_code == 404


def test_deleted_login_can_no_longer_log_in_but_its_person_stays(world):
    from users import Person

    assert login(world.app, "admin").delete(f"/api/users/{world.users['alice']}").status_code == 200
    response = world.app.test_client().post("/api/login", json={"username": "alice", "password": PASSWORD})
    assert response.status_code == 401
    with world.app.app_context():
        assert Person.query.filter_by(name="Ana").count() == 1
