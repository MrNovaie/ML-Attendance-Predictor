"""Passwords (change, reset, forced change), bulk login creation, and sign-up department requests."""
import re

import pytest

from conftest import PASSWORD, login, make_people
from test_perms import ADMIN_ONLY, ANY_LOGIN, LINKED_OR_ADMIN, OWN_ATTENDANCE, call

NEW_PASSWORD = "a-brand-new-pass"


def can_log_in(flask_app, username, password):
    response = flask_app.test_client().post("/api/login", json={"username": username, "password": password})
    return response.status_code == 200


def user_row(flask_app, username):
    from users import User

    with flask_app.app_context():
        user = User.query.filter_by(username=username).one()
        return {"id": user.id, "must_change": user.must_change_password, "person_id": user.person_id,
                "is_admin": user.is_admin, "hash": user.password, "requested": user.requested_department}


def set_user(flask_app, username, **fields):
    from extensions import db
    from users import User

    with flask_app.app_context():
        user = User.query.filter_by(username=username).one()
        for key, value in fields.items():
            setattr(user, key, value)
        db.session.commit()


def change_password(client, current=PASSWORD, new=NEW_PASSWORD):
    return client.post("/api/me/password", json={"current_password": current, "new_password": new})


# ---- changing your own password -------------------------------------------------------------------

def test_a_user_can_change_their_own_password(world):
    response = change_password(login(world.app, "alice"))
    assert response.status_code == 200
    assert can_log_in(world.app, "alice", NEW_PASSWORD)
    assert not can_log_in(world.app, "alice", PASSWORD)
    assert user_row(world.app, "alice")["hash"].startswith(("scrypt:", "pbkdf2:"))


@pytest.mark.parametrize("current", ["not-my-password", "", None, 12345])
def test_the_current_password_must_be_right(world, current):
    before = user_row(world.app, "alice")["hash"]
    assert change_password(login(world.app, "alice"), current=current).status_code == 400
    assert user_row(world.app, "alice")["hash"] == before and can_log_in(world.app, "alice", PASSWORD)


@pytest.mark.parametrize("new", ["short", "p" * 129, 12345, None, "", PASSWORD],
                         ids=["too-short", "too-long", "not-text", "missing", "empty", "same-as-current"])
def test_the_new_password_must_be_acceptable(world, new):
    assert change_password(login(world.app, "alice"), new=new).status_code == 400
    assert can_log_in(world.app, "alice", PASSWORD)


def test_changing_the_password_clears_the_forced_change_flag(world):
    set_user(world.app, "alice", must_change_password=True)
    assert change_password(login(world.app, "alice")).status_code == 200
    assert user_row(world.app, "alice")["must_change"] is False


def test_changing_the_password_logs_out_the_other_browsers_but_not_this_one(world):
    here, elsewhere = login(world.app, "alice"), login(world.app, "alice")
    assert change_password(here).status_code == 200
    assert here.get("/api/me").status_code == 200
    assert elsewhere.get("/api/me").status_code == 401


def test_an_old_style_cookie_with_just_the_user_id_is_not_accepted(world):
    client = world.app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = str(world.users["alice"])
    assert client.get("/api/me").status_code == 401


# ---- a login that must change its password -------------------------------------------------------

def test_a_flagged_login_can_log_in_see_who_it_is_and_log_out(world):
    set_user(world.app, "alice", must_change_password=True)
    client = login(world.app, "alice")
    me = client.get("/api/me").get_json()
    assert me["must_change_password"] is True and me["username"] == "alice"
    assert client.post("/api/logout").status_code == 200


ROUTES = [row for row in ADMIN_ONLY + ANY_LOGIN + LINKED_OR_ADMIN + [OWN_ATTENDANCE]
          if row[1] not in ("/api/me", "/api/logout", "/api/me/password")]


@pytest.mark.parametrize("method,route,url,body", ROUTES, ids=lambda v: v if isinstance(v, str) else "")
def test_a_flagged_login_can_do_nothing_else_even_an_admin(world, method, route, url, body):
    set_user(world.app, "admin", must_change_password=True)
    response = call(login(world.app, "admin"), method, url, body, world)
    assert response.status_code == 403
    assert response.get_json()["password_change_required"] is True


def test_a_flagged_admin_changes_nothing_before_changing_the_password(world):
    from users import AttendanceRecord, Person

    set_user(world.app, "admin", must_change_password=True)
    client = login(world.app, "admin")
    client.put("/api/attendance", json={"date": "2026-10-13", "records": [{"person_id": world.people["Ana"], "status": 1}]})
    client.post("/api/table_persons", json={"name": "Zed"})
    with world.app.app_context():
        assert AttendanceRecord.query.count() == 0 and Person.query.count() == 3


def test_after_changing_the_password_everything_works_again(world):
    set_user(world.app, "alice", must_change_password=True)
    client = login(world.app, "alice")
    assert client.get("/api/today").status_code == 403
    assert change_password(client).status_code == 200
    assert client.get("/api/today").status_code == 200
    assert client.get("/api/me").get_json()["must_change_password"] is False


# ---- an admin sets a temporary password ------------------------------------------------------------

def test_admin_sets_a_typed_temporary_password(world):
    admin = login(world.app, "admin")
    response = admin.post(f"/api/users/{world.users['bob']}/password", json={"password": "temporary-pass"})
    body = response.get_json()
    assert response.status_code == 200 and body["temporary_password"] == "temporary-pass" and body["generated"] is False
    assert can_log_in(world.app, "bob", "temporary-pass") and not can_log_in(world.app, "bob", PASSWORD)
    assert user_row(world.app, "bob")["must_change"] is True


@pytest.mark.parametrize("body", [{}, {"password": ""}, {"password": "   "}, {"password": None}])
def test_a_blank_password_means_generate_one(world, body):
    admin = login(world.app, "admin")
    first = admin.post(f"/api/users/{world.users['bob']}/password", json=body).get_json()
    second = admin.post(f"/api/users/{world.users['bob']}/password", json=body).get_json()
    assert first["generated"] is True
    assert re.fullmatch(r"[a-km-zA-HJ-NP-Z2-9]{12}", first["temporary_password"])      # no 0 O 1 l I
    assert first["temporary_password"] != second["temporary_password"]
    assert can_log_in(world.app, "bob", second["temporary_password"])


@pytest.mark.parametrize("password", ["short", "p" * 129, 12345])
def test_a_bad_typed_password_changes_nothing(world, password):
    before = user_row(world.app, "bob")
    response = login(world.app, "admin").post(f"/api/users/{world.users['bob']}/password", json={"password": password})
    assert response.status_code == 400
    assert user_row(world.app, "bob") == before


def test_resetting_an_unknown_login_is_404_and_your_own_is_refused(world):
    admin = login(world.app, "admin")
    assert admin.post("/api/users/99999/password", json={}).status_code == 404
    before = user_row(world.app, "admin")
    assert admin.post(f"/api/users/{world.users['admin']}/password", json={}).status_code == 400
    assert user_row(world.app, "admin") == before


def test_the_reset_ends_the_users_open_sessions(world):
    bob = login(world.app, "bob")
    assert bob.get("/api/me").status_code == 200
    login(world.app, "admin").post(f"/api/users/{world.users['bob']}/password", json={})
    assert bob.get("/api/me").status_code == 401


def test_one_admin_can_reset_another_admin(world, add_users):
    add_users(("second", True))
    admin = login(world.app, "admin")
    second_id = user_row(world.app, "second")["id"]
    assert admin.post(f"/api/users/{second_id}/password", json={"password": "temporary-pass"}).status_code == 200
    assert user_row(world.app, "second")["must_change"] is True


# ---- accounts made by an admin one at a time ---------------------------------------------------------

def test_a_new_login_must_change_its_password_unless_the_admin_says_not(world):
    admin = login(world.app, "admin")
    admin.post("/api/users", json={"username": "carol", "password": "password123"})
    admin.post("/api/users", json={"username": "dave", "password": "password123", "must_change_password": False})
    assert user_row(world.app, "carol")["must_change"] is True
    assert user_row(world.app, "dave")["must_change"] is False


@pytest.mark.parametrize("body", [{"username": 5, "password": "password123"}, {"username": "carol", "password": 12345678},
                                  {"username": "carol", "password": "p" * 129}])
def test_creating_an_account_with_odd_types_is_a_400_not_a_crash(world, body):
    assert login(world.app, "admin").post("/api/users", json=body).status_code == 400


def test_the_user_list_shows_who_has_a_temporary_password_and_who_asked_for_a_department(world):
    set_user(world.app, "bob", must_change_password=True, requested_department="Finance")
    rows = {u["username"]: u for u in login(world.app, "admin").get("/api/users").get_json()}
    assert rows["bob"]["must_change_password"] is True and rows["bob"]["requested_department"] == "Finance"
    assert rows["alice"]["must_change_password"] is False and rows["alice"]["requested_department"] is None


# ---- bulk creation ------------------------------------------------------------------------------------

def bulk(world, *names, who="admin"):
    ids = [world.people[name] for name in names]
    return login(world.app, who).post("/api/users/bulk", json={"person_ids": ids})


def count_users(flask_app):
    from users import User

    with flask_app.app_context():
        return User.query.count()


def test_bulk_makes_one_linked_login_per_person_with_its_own_temporary_password(world):
    response = bulk(world, "Cai", "Ben")                       # Ana already has a login (alice)
    assert response.status_code == 201
    created = response.get_json()["created"]
    assert [(c["person"], c["username"]) for c in created] == [("Ben", "ben"), ("Cai", "cai")]     # by name
    assert len({c["temporary_password"] for c in created}) == 2
    for entry in created:
        assert re.fullmatch(r"[a-km-zA-HJ-NP-Z2-9]{12}", entry["temporary_password"])
        row = user_row(world.app, entry["username"])
        assert row["person_id"] == entry["person_id"] and row["is_admin"] is False and row["must_change"] is True
        assert row["hash"].startswith(("scrypt:", "pbkdf2:")) and entry["temporary_password"] not in row["hash"]
        assert can_log_in(world.app, entry["username"], entry["temporary_password"])


def test_a_bulk_login_has_to_change_its_password_before_anything_else(world):
    entry = bulk(world, "Ben").get_json()["created"][0]
    client = world.app.test_client()
    client.post("/api/login", json={"username": entry["username"], "password": entry["temporary_password"]})
    assert client.get("/api/me/attendance").status_code == 403
    assert change_password(client, current=entry["temporary_password"]).status_code == 200
    assert client.get("/api/me/attendance").status_code == 200


def test_bulk_is_all_or_nothing_when_someone_already_has_a_login(world):
    before = count_users(world.app)
    response = bulk(world, "Ana", "Ben")                       # Ana is alice's
    assert response.status_code == 409 and "Ana" in response.get_json()["error"]
    assert count_users(world.app) == before                    # Ben did not get one either


def test_bulk_with_an_unknown_person_creates_nothing(world):
    before = count_users(world.app)
    response = login(world.app, "admin").post("/api/users/bulk", json={"person_ids": [world.people["Ben"], 99999]})
    assert response.status_code == 404 and count_users(world.app) == before


@pytest.mark.parametrize("body", [{}, {"person_ids": []}, {"person_ids": "1"}, {"person_ids": [True]},
                                  {"person_ids": [1.5]}, {"person_ids": [None]}, {"person_ids": list(range(1, 202))}],
                         ids=["missing", "empty", "not-a-list", "boolean", "float", "null", "over-200"])
def test_bulk_rejects_bad_requests(world, body):
    before = count_users(world.app)
    assert login(world.app, "admin").post("/api/users/bulk", json=body).status_code == 400
    assert count_users(world.app) == before


def test_listing_a_person_twice_still_makes_one_login(world):
    ben = world.people["Ben"]
    response = login(world.app, "admin").post("/api/users/bulk", json={"person_ids": [ben, ben]})
    assert response.status_code == 201 and len(response.get_json()["created"]) == 1


def test_usernames_are_made_from_names_and_never_collide(world):
    people = make_people(world.app, ("Alice", "Finance"), ("Ann-Marie", "Finance"), ("Ann Marie", "Finance"),
                         ("José Luis", "Finance"), ("***", "Finance"), ("ben", "Manpower"))
    ids = list(people.values()) + [world.people["Ben"]]
    created = login(world.app, "admin").post("/api/users/bulk", json={"person_ids": ids}).get_json()["created"]
    usernames = {c["person"]: c["username"] for c in created}
    assert usernames["Alice"] == "alice2"                      # "alice" is taken by the existing login
    assert {usernames["Ann-Marie"], usernames["Ann Marie"]} == {"ann.marie", "ann.marie2"}
    assert usernames["José Luis"] == "jose.luis"
    assert usernames["***"] == "user"
    assert {usernames["ben"], usernames["Ben"]} == {"ben", "ben2"}
    assert len(set(usernames.values())) == len(usernames)


def test_username_helper_keeps_a_record_of_what_it_hands_out():
    from app import username_from_name

    taken = {"ana"}
    assert username_from_name("Ana", taken) == "ana2"
    assert username_from_name("ANA", taken) == "ana3"
    assert taken == {"ana", "ana2", "ana3"}
    assert len(username_from_name("x" * 200, set())) <= 70


# ---- sign-up department requests ----------------------------------------------------------------------

def answer(world, username, action):
    return login(world.app, "admin").post(f"/api/users/{user_row(world.app, username)['id']}/department-request",
                                          json={"action": action})


def test_confirming_a_request_moves_the_linked_person_and_clears_it(world):
    from users import Person

    set_user(world.app, "alice", requested_department="Manpower")      # alice is linked to Ana (Finance)
    response = answer(world, "alice", "confirm")
    assert response.status_code == 200 and response.get_json()["department"] == "Manpower"
    with world.app.app_context():
        assert Person.query.filter_by(name="Ana").one().department == "Manpower"
    assert user_row(world.app, "alice")["requested"] is None


def test_confirming_needs_a_person_to_move_and_keeps_the_request(world):
    set_user(world.app, "bob", requested_department="Manpower")        # bob is not linked
    response = answer(world, "bob", "confirm")
    assert response.status_code == 409 and "Link" in response.get_json()["error"]
    assert user_row(world.app, "bob")["requested"] == "Manpower"


def test_dismissing_a_request_clears_it_and_moves_nobody(world):
    from users import Person

    set_user(world.app, "alice", requested_department="Manpower")
    assert answer(world, "alice", "dismiss").status_code == 200
    assert user_row(world.app, "alice")["requested"] is None
    with world.app.app_context():
        assert Person.query.filter_by(name="Ana").one().department == "Finance"


def test_department_request_edge_cases(world):
    assert answer(world, "alice", "confirm").status_code == 409        # nothing was requested
    set_user(world.app, "alice", requested_department="Manpower")
    assert answer(world, "alice", "approve").status_code == 400        # not an action
    assert login(world.app, "admin").post("/api/users/99999/department-request", json={"action": "confirm"}).status_code == 404
    assert user_row(world.app, "alice")["requested"] == "Manpower"


# ---- command line --------------------------------------------------------------------------------------

def test_set_password_from_the_command_line_also_lifts_the_forced_change(world):
    set_user(world.app, "admin", must_change_password=True)
    result = world.app.test_cli_runner().invoke(args=["set-password", "admin"], input=f"{NEW_PASSWORD}\n{NEW_PASSWORD}\n")
    assert result.exit_code == 0, result.output
    assert can_log_in(world.app, "admin", NEW_PASSWORD) and not can_log_in(world.app, "admin", PASSWORD)
    assert user_row(world.app, "admin")["must_change"] is False


def test_set_password_refuses_an_unknown_login_or_a_short_password(world):
    runner = world.app.test_cli_runner()
    assert runner.invoke(args=["set-password", "nobody"], input="long-enough-1\nlong-enough-1\n").exit_code != 0
    assert runner.invoke(args=["set-password", "admin"], input="short\nshort\n").exit_code != 0
    assert can_log_in(world.app, "admin", PASSWORD)
