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
        appmod.ensure_attendance_status_check()
        appmod.ensure_person_department()
        appmod.ensure_person_former()
        appmod.ensure_user_person_link()
        appmod.ensure_user_account_columns()


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


@pytest.mark.parametrize("status", [4, -1, 9])
def test_database_refuses_a_status_outside_the_status_table(world, status):
    with pytest.raises(IntegrityError):
        add_row(world.app, world.people["Ana"], date(2026, 10, 13), status)

@pytest.mark.parametrize("status", [0, 1, 2, 3])
def test_database_accepts_every_status_in_the_table(world, status):
    add_row(world.app, world.people["Ana"], date(2026, 10, 13), status)
    assert query(world.app, "SELECT status FROM attendance_record") == [(status,)]

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


# ---- person.former -------------------------------------------------------------------------------

def test_the_former_column_is_added_and_everyone_already_there_stays_active(old_db, appmod):
    run_sql(old_db, "CREATE TABLE person (id INTEGER PRIMARY KEY, name VARCHAR(80) NOT NULL UNIQUE, department VARCHAR(80) NOT NULL)",
            "INSERT INTO person (name, department) VALUES ('Ana', 'Finance'), ('Ben', 'Holding')")
    startup(old_db, appmod)
    assert query(old_db, "SELECT name, department, former FROM person ORDER BY name") == [
        ("Ana", "Finance", 0), ("Ben", "Holding", 0)]
    assert columns(old_db, "person")["former"]["nullable"] is False
    run_sql(old_db, "INSERT INTO person (name, department) VALUES ('Cai', 'Holding')")      # the default applies to new rows too
    assert query(old_db, "SELECT former FROM person WHERE name = 'Cai'") == [(0,)]


def test_the_former_column_is_not_added_twice_and_former_people_keep_their_flag(old_db, appmod):
    startup(old_db, appmod)                                                                 # a fresh, current database
    run_sql(old_db, "INSERT INTO person (name, department, former) VALUES ('Ana', 'Finance', 1)")
    startup(old_db, appmod)
    assert query(old_db, "SELECT name, former FROM person") == [("Ana", 1)]


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


# ---- user.must_change_password and user.requested_department ---------------------------------------

def test_the_account_columns_are_added_and_nobody_is_forced_to_change_a_password(old_db, appmod):
    run_sql(old_db,
            "CREATE TABLE user (id INTEGER PRIMARY KEY, username VARCHAR(80) NOT NULL UNIQUE, "
            "password VARCHAR(255) NOT NULL, is_admin BOOLEAN NOT NULL, person_id INTEGER)",
            "INSERT INTO user (username, password, is_admin) VALUES ('old_admin', 'hash-1', 1), ('old_user', 'hash-2', 0)")
    startup(old_db, appmod)
    rows = query(old_db, "SELECT username, password, is_admin, must_change_password, requested_department FROM user ORDER BY username")
    assert rows == [("old_admin", "hash-1", 1, 0, None), ("old_user", "hash-2", 0, 0, None)]
    assert {"must_change_password", "requested_department"} <= set(columns(old_db, "user"))


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
    assert "Rebuilding the table" not in caplog.text
    assert query(world.app, "SELECT COUNT(*) FROM attendance_record") == [(1,)]
    assert query(world.app, "SELECT COUNT(*) FROM person") == [(3,)]


def test_running_startup_twice_changes_nothing(old_db, appmod):
    run_sql(old_db, "CREATE TABLE person (id INTEGER PRIMARY KEY, name VARCHAR(80) NOT NULL UNIQUE)",
            "INSERT INTO person (name) VALUES ('Ana')")
    startup(old_db, appmod)
    before = query(old_db, "SELECT * FROM person")
    startup(old_db, appmod)
    assert query(old_db, "SELECT * FROM person") == before



# ---- attendance_record: the status CHECK before AM only / PM only existed ------------------------------

OLD_ATTENDANCE_TABLE = (
    "CREATE TABLE attendance_record (id INTEGER NOT NULL, person_id INTEGER NOT NULL, date DATE NOT NULL, "
    "day_of_week SMALLINT NOT NULL, status SMALLINT NOT NULL, created_at DATETIME DEFAULT (CURRENT_TIMESTAMP), "
    "updated_at DATETIME NOT NULL, PRIMARY KEY (id), CONSTRAINT uq_person_date UNIQUE (person_id, date), "
    "CHECK (day_of_week IN (1, 2, 3, 4, 5)), CHECK (status IN (0, 1, 2)), FOREIGN KEY(person_id) REFERENCES person (id))"
)


@pytest.fixture
def old_status_db(old_db, appmod):
    """A database made before status 3 existed: current tables, but an attendance_record that only allows 0, 1, 2,
    holding one record of each status."""
    startup(old_db, appmod)
    run_sql(old_db,
            "DROP TABLE attendance_record",
            OLD_ATTENDANCE_TABLE,
            "INSERT INTO person (name, department) VALUES ('Ana', 'Finance')",
            "INSERT INTO attendance_record (id, person_id, date, day_of_week, status, updated_at) "
            "VALUES (10, 1, '2026-10-12', 1, 0, '2026-10-12 08:00:00'), (11, 1, '2026-10-13', 2, 1, '2026-10-13 08:00:00'), "
            "(12, 1, '2026-10-14', 3, 2, '2026-10-14 08:00:00')")
    return old_db


def test_an_old_status_check_is_rebuilt_and_every_row_is_kept(old_status_db, appmod, caplog):
    before = query(old_status_db, "SELECT * FROM attendance_record ORDER BY id")
    with caplog.at_level(logging.WARNING):
        startup(old_status_db, appmod)
    assert "Rebuilding the table" in caplog.text
    assert query(old_status_db, "SELECT * FROM attendance_record ORDER BY id") == before     # ids, dates, times, statuses
    assert query(old_status_db, "SELECT name FROM sqlite_master WHERE name LIKE 'attendance_record%'") == [("attendance_record",)]


def test_after_the_rebuild_status_3_is_allowed_and_the_other_rules_still_hold(old_status_db, appmod):
    startup(old_status_db, appmod)
    run_sql(old_status_db, "INSERT INTO attendance_record (person_id, date, day_of_week, status, updated_at) "
                           "VALUES (1, '2026-10-15', 4, 3, CURRENT_TIMESTAMP)")
    with pytest.raises(IntegrityError):         # still only the statuses in the table
        run_sql(old_status_db, "INSERT INTO attendance_record (person_id, date, day_of_week, status, updated_at) "
                               "VALUES (1, '2026-10-16', 5, 4, CURRENT_TIMESTAMP)")
    with pytest.raises(IntegrityError):         # still one record per person per day
        run_sql(old_status_db, "INSERT INTO attendance_record (person_id, date, day_of_week, status, updated_at) "
                               "VALUES (1, '2026-10-15', 4, 1, CURRENT_TIMESTAMP)")
    with pytest.raises(IntegrityError):         # still weekdays only
        run_sql(old_status_db, "INSERT INTO attendance_record (person_id, date, day_of_week, status, updated_at) "
                               "VALUES (1, '2026-10-17', 6, 1, CURRENT_TIMESTAMP)")


def test_a_rebuilt_table_still_fills_in_created_at_and_works_with_the_models(old_status_db, appmod):
    startup(old_status_db, appmod)
    run_sql(old_status_db, "INSERT INTO attendance_record (person_id, date, day_of_week, status, updated_at) "
                           "VALUES (1, '2026-10-15', 4, 3, CURRENT_TIMESTAMP)")
    assert query(old_status_db, "SELECT created_at IS NOT NULL FROM attendance_record WHERE date = '2026-10-15'") == [(1,)]
    from extensions import db
    from users import AttendanceRecord

    with old_status_db.app_context():
        assert sorted(r.status for r in AttendanceRecord.query.all()) == [0, 1, 2, 3]


def test_running_the_rebuild_twice_changes_nothing_the_second_time(old_status_db, appmod, caplog):
    startup(old_status_db, appmod)
    after_first = query(old_status_db, "SELECT * FROM attendance_record ORDER BY id")
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        startup(old_status_db, appmod)
    assert "Rebuilding the table" not in caplog.text
    assert query(old_status_db, "SELECT * FROM attendance_record ORDER BY id") == after_first


def test_a_rebuild_that_fails_leaves_the_old_table_and_its_rows_untouched(old_status_db, appmod, monkeypatch):
    before = query(old_status_db, "SELECT * FROM attendance_record ORDER BY id")
    monkeypatch.setattr(appmod, "attendance_table_sql", lambda: "THIS IS NOT SQL")
    with pytest.raises(Exception):
        startup(old_status_db, appmod)
    assert query(old_status_db, "SELECT * FROM attendance_record ORDER BY id") == before
    assert query(old_status_db, "SELECT name FROM sqlite_master WHERE name LIKE 'attendance_record%'") == [("attendance_record",)]
    with pytest.raises(IntegrityError):          # and it still has its old rules
        run_sql(old_status_db, "INSERT INTO attendance_record (person_id, date, day_of_week, status, updated_at) "
                               "VALUES (1, '2026-10-15', 4, 3, CURRENT_TIMESTAMP)")


def test_python_app_py_runs_every_ensure_step(appmod):
    """`python app.py` is the only place the startup steps run, so a step that is written but not called there
    never happens. Every ensure_* function in app.py must appear in the __main__ block (and in startup() above)."""
    import inspect as pyinspect
    import re

    source = pyinspect.getsource(appmod)
    main_block = source.split('if __name__ == "__main__":')[1]
    written = set(re.findall(r"^def (ensure_\w+)\(", source, flags=re.MULTILINE))
    called = set(re.findall(r"\b(ensure_\w+)\(", main_block))
    assert written and written <= called, f"never called at startup: {sorted(written - called)}"