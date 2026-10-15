"""Login, logout, registration and account management."""
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


# ---- registration --------------------------------------------------------------------------------

def register(client, **overrides):
    body = {"username": "newbie", "password": "password123", "confirmPassword": "password123"}
    body.update(overrides)
    return client.post("/api/register", json=body)


def test_registration_makes_an_unlinked_non_admin_login_with_a_hashed_password(flask_app):
    from users import User

    assert register(flask_app.test_client()).status_code == 200
    with flask_app.app_context():
        user = User.query.filter_by(username="newbie").one()
        assert user.is_admin is False and user.person_id is None
        assert user.password != "password123" and user.password.startswith(("scrypt:", "pbkdf2:"))
    assert login(flask_app, "newbie").get("/api/me").status_code == 200


def test_registration_trims_the_username(flask_app):
    from users import User

    register(flask_app.test_client(), username="  zed  ")
    with flask_app.app_context():
        assert User.query.filter_by(username="zed").count() == 1


@pytest.mark.parametrize("overrides", [
    {"username": ""}, {"username": "   "}, {"password": ""}, {"confirmPassword": ""},
    {"password": "short", "confirmPassword": "short"},       # under 8 characters
    {"confirmPassword": "different123"},                     # does not match
], ids=["no-name", "blank-name", "no-password", "no-confirm", "too-short", "mismatch"])
def test_registration_rejects_bad_input(flask_app, overrides):
    from users import User

    assert register(flask_app.test_client(), **overrides).status_code == 400
    with flask_app.app_context():
        assert User.query.count() == 0


def test_registration_rejects_a_taken_username(world):
    assert register(world.app.test_client(), username="alice").status_code == 400


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