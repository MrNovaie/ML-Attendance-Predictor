"""Shared test setup: every test gets its own empty SQLite file, its own model cache and a fixed clock.

Run from the backend folder:  pip install pytest   then   pytest
"""
from datetime import datetime, timezone

import pytest
from werkzeug.security import generate_password_hash

PASSWORD = "password123"
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
                user = User(username=username, password=generate_password_hash(PASSWORD), is_admin=is_admin)
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