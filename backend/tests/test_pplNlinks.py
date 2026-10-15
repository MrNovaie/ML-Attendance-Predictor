"""Adding people, departments, and the admin-made link between a login and a person."""
import pytest

from conftest import login


@pytest.fixture
def admin(world):
    return login(world.app, "admin")


def patch(client, person_id, **body):
    return client.patch(f"/api/people/{person_id}", json=body)


def linked_user_of(world, person_name):
    from users import User

    with world.app.app_context():
        user = User.query.filter_by(person_id=world.people[person_name]).first()
        return user.username if user else None


# ---- adding people -------------------------------------------------------------------------------

def test_add_a_person_with_a_department(admin):
    response = admin.post("/api/table_persons", json={"name": "Dee", "department": "Intelligence"})
    assert response.status_code == 201
    assert response.get_json()["department"] == "Intelligence"


@pytest.mark.parametrize("body", [{"name": "Dee"}, {"name": "Dee", "department": ""}, {"name": "Dee", "department": "  "},
                                  {"name": "Dee", "department": None}], ids=["missing", "empty", "blank", "null"])
def test_no_department_means_holding(admin, body):
    assert admin.post("/api/table_persons", json=body).get_json()["department"] == "Holding"


def test_name_is_trimmed(admin):
    assert admin.post("/api/table_persons", json={"name": "  Dee  "}).get_json()["name"] == "Dee"


@pytest.mark.parametrize("body", [{}, {"name": ""}, {"name": "   "}, {"name": None}], ids=["none", "empty", "blank", "null"])
def test_a_name_is_required(admin, body):
    assert admin.post("/api/table_persons", json=body).status_code == 400


def test_duplicate_names_are_refused(world, admin):
    assert admin.post("/api/table_persons", json={"name": "Ana"}).status_code == 409
    assert admin.post("/api/table_persons", json={"name": " Ana "}).status_code == 409


@pytest.mark.parametrize("department", ["Nonsense", "finance", 5, ["Finance"]], ids=repr)
def test_unknown_departments_are_refused(admin, department):
    assert admin.post("/api/table_persons", json={"name": "Dee", "department": department}).status_code == 400


def test_departments_endpoint_lists_the_choices_and_the_default(admin):
    body = admin.get("/api/departments").get_json()
    assert body["default"] == "Holding" and body["departments"][0] == "Holding"
    assert {"Intelligence", "Finance", "Human Resources", "Manpower", "Headquarters"} <= set(body["departments"])


# ---- changing a department -----------------------------------------------------------------------

def test_change_a_department(world, admin):
    response = patch(admin, world.people["Ben"], department="Manpower")
    assert response.get_json()["department"] == "Manpower"
    people = {p["name"]: p["department"] for p in admin.get("/api/people").get_json()}
    assert people["Ben"] == "Manpower"


def test_blank_department_becomes_holding(world, admin):
    assert patch(admin, world.people["Ben"], department="").get_json()["department"] == "Holding"


@pytest.mark.parametrize("department", ["Nonsense", 5], ids=repr)
def test_bad_department_changes_nothing(world, admin, department):
    assert patch(admin, world.people["Ben"], department=department).status_code == 400
    assert {p["name"]: p["department"] for p in admin.get("/api/people").get_json()}["Ben"] == "Finance"


def test_changing_the_department_keeps_the_link(world, admin):
    patch(admin, world.people["Ana"], department="Manpower")
    assert linked_user_of(world, "Ana") == "alice"


def test_patch_with_nothing_to_change_or_an_unknown_person(world, admin):
    assert patch(admin, world.people["Ana"]).status_code == 400
    assert patch(admin, 99999, department="Finance").status_code == 404


# ---- links ---------------------------------------------------------------------------------------

def test_link_a_login_to_a_person(world, admin):
    response = patch(admin, world.people["Ben"], user_id=world.users["bob"])
    assert response.get_json()["user_id"] == world.users["bob"]
    assert linked_user_of(world, "Ben") == "bob"
    assert login(world.app, "bob").get("/api/me/attendance").get_json()["person"]["name"] == "Ben"


def test_unlink_with_null(world, admin):
    assert patch(admin, world.people["Ana"], user_id=None).get_json()["user_id"] is None
    assert linked_user_of(world, "Ana") is None
    assert login(world.app, "alice").get("/api/me/attendance").get_json()["linked"] is False


def test_one_login_per_person_the_newer_link_replaces_the_older(world, admin):
    patch(admin, world.people["Ana"], user_id=world.users["bob"])      # alice was linked to Ana
    assert linked_user_of(world, "Ana") == "bob"
    assert login(world.app, "alice").get("/api/me/attendance").get_json()["linked"] is False


def test_one_person_per_login_linking_moves_the_login(world, admin):
    patch(admin, world.people["Ben"], user_id=world.users["alice"])    # alice was linked to Ana
    assert linked_user_of(world, "Ben") == "alice"
    assert linked_user_of(world, "Ana") is None


def test_relinking_the_same_login_is_harmless(world, admin):
    assert patch(admin, world.people["Ana"], user_id=world.users["alice"]).status_code == 200
    assert linked_user_of(world, "Ana") == "alice"


@pytest.mark.parametrize("user_id", ["2", True, False, 1.5, [2]], ids=repr)
def test_user_id_must_be_an_integer_or_null(world, admin, user_id):
    assert patch(admin, world.people["Ben"], user_id=user_id).status_code == 400
    assert linked_user_of(world, "Ben") is None


def test_linking_an_unknown_login_changes_nothing(world, admin):
    assert patch(admin, world.people["Ana"], user_id=99999).status_code == 404
    assert linked_user_of(world, "Ana") == "alice"                     # the old link survives


def test_the_database_itself_refuses_two_logins_on_one_person(world):
    from extensions import db
    from sqlalchemy.exc import IntegrityError
    from users import User

    with world.app.app_context():
        db.session.get(User, world.users["bob"]).person_id = world.people["Ana"]   # alice already has Ana
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()