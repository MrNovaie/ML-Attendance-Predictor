"""Removing a person (they become a "former" person), restoring them, and renaming people.

Removing never deletes anything: the person and their attendance stay, their login is unlinked but kept, and they
drop out of the board, predictions, head-counts and new logins.
"""
import pytest

from conftest import login, make_record
from users import STATUSES
from test_preds import MONDAY, history


@pytest.fixture
def admin(world):
    return login(world.app, "admin")


def patch(client, person_id, **body):
    return client.patch(f"/api/people/{person_id}", json=body)


def remove(client, world, name):
    return patch(client, world.people[name], former=True)


def names(client, include_former=False):
    url = "/api/people" + ("?include_former=1" if include_former else "")
    return [p["name"] for p in client.get(url).get_json()]


def row(world, name):
    from users import Person

    with world.app.app_context():
        person = Person.query.filter_by(name=name).one()
        return {"id": person.id, "former": person.former, "department": person.department}


def count(world, model):
    with world.app.app_context():
        return model.query.count()


def login_row(world, username):
    from users import User

    with world.app.app_context():
        user = User.query.filter_by(username=username).first()
        return None if user is None else {"person_id": user.person_id, "is_admin": user.is_admin}


# ---- removing ------------------------------------------------------------------------------------

def test_removing_a_person_marks_them_former_and_hides_them(world, admin):
    body = remove(admin, world, "Ben").get_json()
    assert body["former"] is True and body["name"] == "Ben"
    assert names(admin) == ["Ana", "Cai"]
    assert names(admin, include_former=True) == ["Ana", "Ben", "Cai"]
    assert {p["name"]: p["former"] for p in admin.get("/api/people?include_former=1").get_json()} == {
        "Ana": False, "Ben": True, "Cai": False}


def test_removing_deletes_nothing(world, admin):
    from users import AttendanceRecord, Person

    make_record(world.app, world.people["Ben"], "2026-10-12", 1)
    make_record(world.app, world.people["Ben"], "2026-10-13", 0)
    remove(admin, world, "Ben")
    assert count(world, Person) == 3 and count(world, AttendanceRecord) == 2
    assert row(world, "Ben")["department"] == "Finance"


def test_removing_unlinks_the_login_but_keeps_it(world, admin):
    body = remove(admin, world, "Ana").get_json()                 # alice is linked to Ana
    assert body["unlinked_username"] == "alice" and body["user_id"] is None
    assert login_row(world, "alice") == {"person_id": None, "is_admin": False}
    assert login_row(world, "bob") == {"person_id": None, "is_admin": False}      # nobody else was touched


def test_removing_someone_with_no_login_reports_no_unlinked_login(world, admin):
    assert remove(admin, world, "Ben").get_json()["unlinked_username"] is None


def test_the_unlinked_login_still_works_but_has_no_person_and_no_head_count(world, admin):
    remove(admin, world, "Ana")
    alice = login(world.app, "alice")                              # the password still works
    assert alice.get("/api/me").status_code == 200
    mine = alice.get("/api/me/attendance").get_json()
    assert mine["linked"] is False and mine["person"] is None and mine["can_edit"] is False
    assert alice.put("/api/me/attendance", json={"status": 1}).status_code == 403
    assert alice.get("/api/attendance/summary").status_code == 403
    assert alice.post("/api/me/password", json={"current_password": "password123",
                                                "new_password": "a-new-password"}).status_code == 200


def test_removing_twice_is_harmless(world, admin):
    remove(admin, world, "Ana")
    again = remove(admin, world, "Ana")
    assert again.status_code == 200 and again.get_json()["unlinked_username"] is None
    assert row(world, "Ana")["former"] is True


@pytest.mark.parametrize("value", ["yes", "true", 1, 0, None, [True]], ids=repr)
def test_former_must_be_true_or_false(world, admin, value):
    assert patch(admin, world.people["Ben"], former=value).status_code == 400
    assert row(world, "Ben")["former"] is False


def test_the_other_changes_in_the_same_request_are_applied_too(world, admin):
    body = patch(admin, world.people["Ana"], former=True, department="Manpower").get_json()
    assert body["department"] == "Manpower" and body["former"] is True


# ---- restoring -----------------------------------------------------------------------------------

def test_restoring_brings_a_person_back_with_their_history(world, admin):
    from users import AttendanceRecord

    make_record(world.app, world.people["Ana"], "2026-10-12", 1)
    remove(admin, world, "Ana")
    body = patch(admin, world.people["Ana"], former=False).get_json()
    assert body["former"] is False
    assert names(admin) == ["Ana", "Ben", "Cai"]
    assert count(world, AttendanceRecord) == 1
    assert row(world, "Ana")["department"] == "Finance"


def test_restoring_does_not_relink_the_old_login(world, admin):
    remove(admin, world, "Ana")
    body = patch(admin, world.people["Ana"], former=False).get_json()
    assert body["user_id"] is None and login_row(world, "alice")["person_id"] is None


def test_restoring_an_active_person_is_harmless(world, admin):
    assert patch(admin, world.people["Ben"], former=False).status_code == 200
    assert row(world, "Ben")["former"] is False and login_row(world, "alice")["person_id"] == world.people["Ana"]


def test_a_restored_person_can_be_linked_again(world, admin):
    remove(admin, world, "Ana")
    patch(admin, world.people["Ana"], former=False)
    assert patch(admin, world.people["Ana"], user_id=world.users["alice"]).get_json()["user_id"] == world.users["alice"]
    assert login(world.app, "alice").get("/api/attendance/summary").status_code == 200


# ---- a former person cannot get a login ----------------------------------------------------------

def test_a_login_cannot_be_linked_to_a_former_person(world, admin):
    remove(admin, world, "Ben")
    assert patch(admin, world.people["Ben"], user_id=world.users["bob"]).status_code == 409
    assert login_row(world, "bob")["person_id"] is None


def test_removing_and_linking_in_one_request_changes_nothing(world, admin):
    assert patch(admin, world.people["Ben"], former=True, user_id=world.users["bob"]).status_code == 409
    assert row(world, "Ben")["former"] is False and login_row(world, "bob")["person_id"] is None


def test_restoring_and_linking_in_one_request_does_both(world, admin):
    remove(admin, world, "Ben")
    body = patch(admin, world.people["Ben"], former=False, user_id=world.users["bob"]).get_json()
    assert body["former"] is False and body["user_id"] == world.users["bob"]


def test_unlinking_a_former_person_is_allowed(world, admin):
    remove(admin, world, "Ben")
    assert patch(admin, world.people["Ben"], user_id=None).status_code == 200


def test_bulk_logins_refuse_a_former_person_and_create_nothing(world, admin):
    from users import User

    remove(admin, world, "Ben")
    response = admin.post("/api/users/bulk", json={"person_ids": [world.people["Cai"], world.people["Ben"]]})
    assert response.status_code == 409 and "Ben" in response.get_json()["error"]
    assert count(world, User) == 3


# ---- recording and reading attendance ------------------------------------------------------------

def test_a_former_person_cannot_be_recorded_and_the_rest_of_the_request_is_not_saved(world, admin):
    remove(admin, world, "Ben")
    response = admin.put("/api/attendance", json={"date": "2026-10-13", "records": [
        {"person_id": world.people["Ana"], "status": 1}, {"person_id": world.people["Ben"], "status": 1}]})
    assert response.status_code == 400 and "Ben" in response.get_json()["error"]
    from conftest import recorded
    assert recorded(world.app, "2026-10-13") == {}


def test_un_recording_a_former_person_is_refused_too(world, admin):
    make_record(world.app, world.people["Ben"], "2026-10-13", 1)
    remove(admin, world, "Ben")
    response = admin.put("/api/attendance", json={"date": "2026-10-13", "records": [
        {"person_id": world.people["Ben"], "status": None}]})
    assert response.status_code == 400
    from conftest import recorded
    assert recorded(world.app, "2026-10-13") == {"Ben": 1}


def test_reading_a_day_leaves_out_former_people(world, admin):
    make_record(world.app, world.people["Ana"], "2026-10-13", 1)
    make_record(world.app, world.people["Ben"], "2026-10-13", 0)
    remove(admin, world, "Ben")
    records = admin.get("/api/attendance?date=2026-10-13").get_json()["records"]
    assert records == [{"person_id": world.people["Ana"], "status": 1}]


def test_the_head_count_leaves_out_former_people(world, admin):
    make_record(world.app, world.people["Ana"], "2026-10-14", 1)
    make_record(world.app, world.people["Ben"], "2026-10-14", 1)
    make_record(world.app, world.people["Cai"], "2026-10-14", 0)
    remove(admin, world, "Ben")
    result = login(world.app, "alice").get("/api/attendance/summary").get_json()
    assert result["by_status"] == {"0": 1, "1": 1, "2": 0, "3": 0}
    assert (result["not_recorded"], result["total"]) == (0, 2)
    finance = login(world.app, "alice").get("/api/attendance/summary?department=Finance").get_json()
    assert (finance["total"], finance["by_status"]["1"]) == (1, 1)


# ---- predictions and evaluation ------------------------------------------------------------------

def predicted(client):
    return [p["person"] for p in client.get(f"/api/predict?date={MONDAY}").get_json()["predictions"]]


def test_predictions_drop_a_removed_person_even_when_the_models_were_cached(world, admin):
    history(world, "Ana", [1, 1, 1, 1, 0], weeks=3)
    history(world, "Ben", [1, 1, 1, 1, 0], weeks=3)
    assert predicted(admin) == ["Ana", "Ben"]                      # this builds the model cache
    remove(admin, world, "Ben")
    assert predicted(admin) == ["Ana"]
    patch(admin, world.people["Ben"], former=False)
    assert predicted(admin) == ["Ana", "Ben"]


def test_evaluation_drops_a_removed_person(world, admin):
    history(world, "Ana", [1, 1, 1, 1, 0], weeks=3)
    history(world, "Ben", [1, 1, 1, 1, 0], weeks=3)
    remove(admin, world, "Ben")
    assert [e["person"] for e in admin.get("/api/evaluate").get_json()["evaluations"]] == ["Ana"]


def test_with_only_former_people_left_there_is_nothing_to_predict_and_no_error(world, admin):
    history(world, "Ana", [1, 1, 1, 1, 0], weeks=3)
    assert predicted(admin) == ["Ana"]
    remove(admin, world, "Ana")
    response = admin.get(f"/api/predict?date={MONDAY}")
    assert response.status_code == 200 and response.get_json()["predictions"] == []
    assert admin.get("/api/evaluate").get_json()["evaluations"] == []


# ---- adding a person whose name is taken by a former person --------------------------------------

def test_adding_a_former_persons_name_points_to_restoring_them(world, admin):
    remove(admin, world, "Ben")
    response = admin.post("/api/table_persons", json={"name": "Ben"})
    assert response.status_code == 409 and "former" in response.get_json()["error"].lower()


def test_an_overlong_name_is_refused_when_adding(admin):
    assert admin.post("/api/table_persons", json={"name": "x" * 81}).status_code == 400
    assert admin.post("/api/table_persons", json={"name": "x" * 80}).status_code == 201


# ---- renaming ------------------------------------------------------------------------------------

def test_rename_a_person(world, admin):
    body = patch(admin, world.people["Ben"], name="  Benjamin  ").get_json()
    assert body["name"] == "Benjamin"
    assert names(admin) == ["Ana", "Benjamin", "Cai"]


def test_renaming_keeps_attendance_link_and_department(world, admin):
    from conftest import recorded

    make_record(world.app, world.people["Ana"], "2026-10-13", 2)
    patch(admin, world.people["Ana"], name="Anna")
    assert recorded(world.app, "2026-10-13") == {"Anna": 2}
    assert login_row(world, "alice")["person_id"] == world.people["Ana"]
    assert login(world.app, "alice").get("/api/me/attendance").get_json()["person"]["name"] == "Anna"
    assert row(world, "Anna")["department"] == "Finance"


@pytest.mark.parametrize("name", ["", "   ", None, 5, ["Ben"], "x" * 81], ids=repr)
def test_a_bad_name_changes_nothing(world, admin, name):
    assert patch(admin, world.people["Ben"], name=name).status_code == 400
    assert names(admin) == ["Ana", "Ben", "Cai"]


def test_a_name_that_is_taken_is_refused(world, admin):
    assert patch(admin, world.people["Ben"], name="Ana").status_code == 409
    assert patch(admin, world.people["Ben"], name=" Ana ").status_code == 409
    assert names(admin) == ["Ana", "Ben", "Cai"]


def test_a_name_held_by_a_former_person_is_refused_too(world, admin):
    remove(admin, world, "Cai")
    response = patch(admin, world.people["Ben"], name="Cai")
    assert response.status_code == 409 and "former" in response.get_json()["error"].lower()


def test_renaming_to_your_own_name_is_harmless(world, admin):
    assert patch(admin, world.people["Ben"], name="Ben").status_code == 200
    assert patch(admin, world.people["Ben"], name=" Ben ").status_code == 200
    assert names(admin) == ["Ana", "Ben", "Cai"]


def test_a_failed_part_of_the_request_applies_none_of_it(world, admin):
    assert patch(admin, world.people["Ben"], name="Benjamin", department="Nonsense").status_code == 400
    assert patch(admin, world.people["Ben"], name="Benjamin", former="yes").status_code == 400
    assert patch(admin, world.people["Ben"], name="Ana", department="Manpower").status_code == 409
    assert names(admin) == ["Ana", "Ben", "Cai"] and row(world, "Ben")["department"] == "Finance"


def test_a_former_person_can_be_renamed(world, admin):
    remove(admin, world, "Ben")
    assert patch(admin, world.people["Ben"], name="Benjamin").status_code == 200
    assert names(admin, include_former=True) == ["Ana", "Benjamin", "Cai"]


def test_predictions_use_the_new_name_after_a_rename(world, admin):
    history(world, "Ana", [1, 1, 1, 1, 0], weeks=3)
    assert predicted(admin) == ["Ana"]                             # builds the model cache under the old name
    patch(admin, world.people["Ana"], name="Anna")
    assert predicted(admin) == ["Anna"]


# ---- backups keep the flag -----------------------------------------------------------------------

def test_a_backup_round_trip_keeps_former_people_and_their_history(world, admin, make_app):
    import backup
    from users import Person

    make_record(world.app, world.people["Ben"], "2026-10-12", 1)
    remove(admin, world, "Ben")
    remove(admin, world, "Cai")                                    # no records: a roster-only row
    text = admin.get("/api/export/attendance.csv").get_data(as_text=True).lstrip("﻿")
    assert "Ben,Finance,2026-10-12,1,1" in text and "Cai,Manpower,,,1" in text and "Ana,Finance,,,0" in text

    other = make_app("restored")
    import io
    with other.app_context():
        backup.restore_csv(io.StringIO(text), allowed_statuses=set(STATUSES))
        flags = {p.name: p.former for p in Person.query}
    assert flags == {"Ana": False, "Ben": True, "Cai": True}
