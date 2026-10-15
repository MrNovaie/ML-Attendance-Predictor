"""Shared test setup: every test gets its own empty SQLite file, its own model cache and a fixed clock.

Run from the backend folder:  pip install pytest   then   pytest
"""
from datetime import datetime, timezone

import pytest
from werkzeug.security import generate_password_hash

PASSWORD = "password123"


def FAST_HASH(password):
    """Same format as the real hashes, but one round, so a test that logs in does not spend a second hashing."""
    return generate_password_hash(password, method="pbkdf2:sha256:1")


# Wed 14 Oct 2026, 11:00 in UTC+8 (the office timezone). Tests move the clock with `set_clock`.
DEFAULT_NOW = "2026-10-14T03:00:00"


@pytest.fixture
def appmod(monkeypatch):
    import app as module
    return module


@pytest.fixture
def set_clock(appmod, monkeypatch):
    """set_clock("2026-10-14T00:14:59") pins 'now' (given in UTC) for the whole test."""
    def _set(iso_utc: str) -> None:
        moment = datetime.fromisoformat(iso_utc).replace(tzinfo=timezone.utc)
        monkeypatch.setattr(appmod, "utc_now", lambda: moment)
    _set(DEFAULT_NOW)
    return _set


@pytest.fixture
def make_app(appmod, tmp_path, monkeypatch, set_clock):
    """make_app("name") -> a Flask app on a fresh database file. Call it twice for two databases."""
    import ml
    from extensions import db

    monkeypatch.setattr(ml, "MODELS_PATH", tmp_path / "models.joblib")

    def _make(name: str = "main"):
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / (name + '.db')).as_posix()}")
        flask_app = appmod.create_app()
        with flask_app.app_context():
            db.create_all()
            appmod.ensure_attendance_schema()
            appmod.ensure_person_department()
            appmod.ensure_user_person_link()
        return flask_app

    return _make


@pytest.fixture
def flask_app(make_app):
    return make_app("main")


@pytest.fixture
def add_users(flask_app):
    """Create the logins used in tests: admin, alice (non-admin) ... and return their ids."""
    from extensions import db
    from users import User

    def _add(*specs):
        ids = {}
        with flask_app.app_context():
            for username, is_admin in specs:
                user = User(username=username, password=FAST_HASH(PASSWORD), is_admin=is_admin)
                db.session.add(user)
                db.session.flush()
                ids[username] = user.id
            db.session.commit()
        return ids

    return _add


def login(flask_app, username=None):
    """A test client, logged in as `username` (or anonymous when None)."""
    client = flask_app.test_client()
    if username:
        response = client.post("/api/login", json={"username": username, "password": PASSWORD})
        assert response.status_code == 200, response.get_data(as_text=True)
    return client


# ---- shared test data ------------------------------------------------------------------------------

# Records are backdated so the model cache file counts as newer than every row (see ml.load_models).
BACKDATED = datetime(2026, 1, 1)


def make_people(flask_app, *specs):
    """make_people(app, ("Ana", "Finance"), ...) -> {name: id}"""
    from extensions import db
    from users import Person

    ids = {}
    with flask_app.app_context():
        for name, department in specs:
            person = Person(name=name, department=department)
            db.session.add(person)
            db.session.flush()
            ids[name] = person.id
        db.session.commit()
    return ids


def make_record(flask_app, person_id, day, status, updated_at=BACKDATED):
    from datetime import date as date_type
    from extensions import db
    from users import AttendanceRecord

    parsed = day if isinstance(day, date_type) else date_type.fromisoformat(day)
    with flask_app.app_context():
        db.session.add(AttendanceRecord(person_id=person_id, date=parsed, day_of_week=parsed.isoweekday(),
                                        status=status, created_at=updated_at, updated_at=updated_at))
        db.session.commit()


def link(flask_app, user_id, person_id):
    from extensions import db
    from users import User

    with flask_app.app_context():
        db.session.get(User, user_id).person_id = person_id
        db.session.commit()


def recorded(flask_app, day):
    """{person name: status} for one day, read straight from the database."""
    from datetime import date as date_type
    from users import AttendanceRecord, Person

    day = day if isinstance(day, date_type) else date_type.fromisoformat(day)
    with flask_app.app_context():
        rows = (AttendanceRecord.query.filter_by(date=day)
                .join(Person, Person.id == AttendanceRecord.person_id)
                .with_entities(Person.name, AttendanceRecord.status).all())
    return {name: status for name, status in rows}


@pytest.fixture
def world(flask_app, add_users):
    """Three logins (admin, alice linked to Ana, bob not linked) and three people. Clock: Wed 11:00 local."""
    from types import SimpleNamespace

    users = add_users(("admin", True), ("alice", False), ("bob", False))
    people = make_people(flask_app, ("Ana", "Finance"), ("Ben", "Finance"), ("Cai", "Manpower"))
    link(flask_app, users["alice"], people["Ana"])
    return SimpleNamespace(app=flask_app, users=users, people=people)