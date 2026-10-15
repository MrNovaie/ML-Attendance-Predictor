"""The database rules, and the small startup migrations that bring an older database up to date."""
import logging
from datetime import date

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from conftest import make_people, make_record


@pytest.fixture
def old_db(appmod, tmp_path, monkeypatch, set_clock):
    """An app on an empty file with NO tables yet, so a test can build an older schema by hand."""
    import ml

    monkeypatch.setattr(ml, "MODELS_PATH", tmp_path / "models.joblib")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'old.db').as_posix()}")
    return appmod.create_app()


def run_sql(flask_app, *statements):
    from extensions import db

    with flask_app.app_context():
        for statement in statements:
            db.session.execute(text(statement))
        db.session.commit()


def query(flask_app, sql):
    from extensions import db

    with flask_app.app_context():
        return db.session.execute(text(sql)).fetchall()


def startup(flask_app, appmod):
    """What `python app.py` does before serving."""
    from extensions import db

    with flask_app.app_context():
        db.create_all()
        appmod.ensure_attendance_schema()
        appmod.ensure_person_department()
        appmod.ensure_user_person_link()


def columns(flask_app, table):
    from extensions import db

    with flask_app.app_context():
        return {c["name"]: c for c in inspect(db.engine).get_columns(table)}


# ---- the rules the database enforces itself ------------------------------------------------------

def add_row(flask_app, person_id, day, status, dow=None):
    from extensions import db
    from users import AttendanceRecord

    with flask_app.app_context():
        db.session.add(AttendanceRecord(person_id=person_id, date=day, status=status,
                                        day_of_week=dow if dow is not None else day.isoweekday()))
        db.session.commit()


@pytest.mark.parametrize("status", [3, -1, 9])
def test_database_refuses_a_status_outside_0_1_2(world, status):
    from extensions import db

    with pytest.raises(IntegrityError):
        add_row(world.app, world.people["Ana"], date(2026, 10, 13), status)


@pytest.mark.parametrize("dow", [0, 6, 7])
def test_database_refuses_a_weekday_number_outside_1_to_5(world, dow):
    with pytest.raises(IntegrityError):
        add_row(world.app, world.people["Ana"], date(2026, 10, 13), 1, dow=dow)


def test_database_refuses_two_records_for_one_person_and_day(world):
    add_row(world.app, world.people["Ana"], date(2026, 10, 13), 1)
    with pytest.raises(IntegrityError):
        add_row(world.app, world.people["Ana"], date(2026, 10, 13), 0)


def test_database_refuses_a_duplicate_person_name_and_a_missing_department(world):
    from extensions import db
    from users import Person

    with world.app.app_context():
        db.session.add(Person(name="Ana", department="Finance"))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()
    with pytest.raises(IntegrityError):
        run_sql(world.app, "INSERT INTO person (name, department) VALUES ('Zed', NULL)")


# ---- person.department ---------------------------------------------------------------------------

def test_missing_department_column_is_added_and_existing_people_land_in_holding(old_db, appmod):
    run_sql(old_db, "CREATE TABLE person (id INTEGER PRIMARY KEY, name VARCHAR(80) NOT NULL UNIQUE)",
            "INSERT INTO person (name) VALUES ('Ana'), ('Ben')")
    startup(old_db, appmod)
    assert query(old_db, "SELECT name, department FROM person ORDER BY name") == [("Ana", "Holding"), ("Ben", "Holding")]
    assert columns(old_db, "person")["department"]["nullable"] is False
    run_sql(old_db, "INSERT INTO person (name) VALUES ('Cai')")                       # the default applies to new rows too
    assert query(old_db, "SELECT department FROM person WHERE name = 'Cai'") == [("Holding",)]


def test_blank_and_null_departments_are_filled_and_real_ones_kept(old_db, appmod):
    run_sql(old_db, "CREATE TABLE person (id INTEGER PRIMARY KEY, name VARCHAR(80) NOT NULL UNIQUE, department VARCHAR(80))",
            "INSERT INTO person (name, department) VALUES ('Ana', NULL), ('Ben', ''), ('Cai', '   '), ('Dee', 'Finance'), "
            "('Eli', 'Management')")
    startup(old_db, appmod)
    assert dict(query(old_db, "SELECT name, department FROM person")) == {
        "Ana": "Holding", "Ben": "Holding", "Cai": "Holding", "Dee": "Finance", "Eli": "Management"}


# ---- user.person_id ------------------------------------------------------------------------------

def test_missing_link_column_is_added_and_existing_logins_survive_unlinked(old_db, appmod):
    run_sql(old_db,
            "CREATE TABLE user (id INTEGER PRIMARY KEY, username VARCHAR(80) NOT NULL UNIQUE, "
            "password VARCHAR(255) NOT NULL, is_admin BOOLEAN NOT NULL)",
            "INSERT INTO user (username, password, is_admin) VALUES ('old_admin', 'hash-1', 1), ('old_user', 'hash-2', 0)")
    startup(old_db, appmod)
    rows = query(old_db, "SELECT username, password, is_admin, person_id FROM user ORDER BY username")
    assert rows == [("old_admin", "hash-1", 1, None), ("old_user", "hash-2", 0, None)]


def test_the_added_link_column_still_enforces_one_login_per_person(old_db, appmod):
    run_sql(old_db,
            "CREATE TABLE user (id INTEGER PRIMARY KEY, username VARCHAR(80) NOT NULL UNIQUE, "
            "password VARCHAR(255) NOT NULL, is_admin BOOLEAN NOT NULL)",
            "INSERT INTO user (username, password, is_admin) VALUES ('a', 'x', 0), ('b', 'y', 0)")
    startup(old_db, appmod)
    run_sql(old_db, "INSERT INTO person (name, department) VALUES ('Ana', 'Finance')",
            "UPDATE user SET person_id = 1 WHERE username = 'a'")
    with pytest.raises(IntegrityError):
        run_sql(old_db, "UPDATE user SET person_id = 1 WHERE username = 'b'")


# ---- attendance_record ---------------------------------------------------------------------------

def test_a_stale_attendance_table_is_rebuilt_empty_and_with_the_constraints(old_db, appmod, caplog):
    run_sql(old_db,
            "CREATE TABLE attendance_record (id INTEGER PRIMARY KEY, name TEXT, date TEXT, status INTEGER)",
            "INSERT INTO attendance_record (name, date, status) VALUES ('Ana', '2026-10-13', 1)",
            "CREATE TABLE person (id INTEGER PRIMARY KEY, name VARCHAR(80) NOT NULL UNIQUE)")
    with caplog.at_level(logging.WARNING):
        startup(old_db, appmod)
    assert "stale schema" in caplog.text
    assert {"person_id", "day_of_week", "updated_at"} <= set(columns(old_db, "attendance_record"))
    assert query(old_db, "SELECT COUNT(*) FROM attendance_record") == [(0,)]
    run_sql(old_db, "INSERT INTO person (name, department) VALUES ('Ana', 'Finance')")
    with pytest.raises(IntegrityError):
        run_sql(old_db, "INSERT INTO attendance_record (person_id, date, day_of_week, status, updated_at) "
                        "VALUES (1, '2026-10-13', 2, 7, CURRENT_TIMESTAMP)")


def test_a_current_database_is_left_alone(world, appmod, caplog):
    make_record(world.app, world.people["Ana"], "2026-10-13", 1)
    with caplog.at_level(logging.WARNING):
        startup(world.app, appmod)
    assert "stale schema" not in caplog.text and "Adding the column" not in caplog.text
    assert query(world.app, "SELECT COUNT(*) FROM attendance_record") == [(1,)]
    assert query(world.app, "SELECT COUNT(*) FROM person") == [(3,)]


def test_running_startup_twice_changes_nothing(old_db, appmod):
    run_sql(old_db, "CREATE TABLE person (id INTEGER PRIMARY KEY, name VARCHAR(80) NOT NULL UNIQUE)",
            "INSERT INTO person (name) VALUES ('Ana')")
    startup(old_db, appmod)
    before = query(old_db, "SELECT * FROM person")
    startup(old_db, appmod)
    assert query(old_db, "SELECT * FROM person") == before