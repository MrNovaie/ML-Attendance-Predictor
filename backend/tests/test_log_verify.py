"""GET /api/activity-log/verify: the chain check as the log page uses it. (The check itself, verify_chain(), is tested
in test_log_core.py; here we only test what the route reports.)"""
import sqlite3

import activity_log
from conftest import busy_log, login  # noqa: F401  (busy_log is a fixture)

VERIFY = "/api/activity-log/verify"


def tamper(app, *statements):
    """What someone with the database file could do: drop the append-only trigger, then change rows directly."""
    path = app.config["SQLALCHEMY_DATABASE_URI"].replace("sqlite:///", "")
    db_file = sqlite3.connect(path)
    for statement in ("DROP TRIGGER IF EXISTS activity_log_no_update", "DROP TRIGGER IF EXISTS activity_log_no_delete", *statements):
        db_file.execute(statement)
    db_file.commit()
    db_file.close()


def verify(app):
    response = login(app, "admin").get(VERIFY)
    assert response.status_code == 200
    return response.get_json()


# ---- verify -------------------------------------------------------------------------------------------

def test_an_empty_log_is_intact(world):
    assert verify(world.app) == {"ok": True, "first_bad_seq": None, "rows": 0, "tail_checked": False}


def test_a_busy_log_is_intact(busy_log):
    body = verify(busy_log.app)
    assert body["ok"] is True and body["first_bad_seq"] is None and body["rows"] == 4


def test_an_edited_row_is_reported_by_its_seq(busy_log):
    tamper(busy_log.app, "UPDATE activity_log SET admin_name = 'boss' WHERE seq = 3")      # someone else's name on it
    body = verify(busy_log.app)
    assert body["ok"] is False and body["first_bad_seq"] == 3 and body["rows"] == 4


def test_a_bad_first_row_is_still_reported_as_bad(busy_log):
    """Seq 1 must not be mistaken for 'nothing wrong' (a falsy-value trap)."""
    tamper(busy_log.app, "UPDATE activity_log SET action = 'person_removed' WHERE seq = 1")
    body = verify(busy_log.app)
    assert body["ok"] is False and body["first_bad_seq"] == 1


def test_a_deleted_middle_row_is_reported_at_the_row_after_the_gap(busy_log):
    tamper(busy_log.app, "DELETE FROM activity_log WHERE seq = 2")
    body = verify(busy_log.app)
    assert body["ok"] is False and body["first_bad_seq"] == 3 and body["rows"] == 3


def test_ok_is_a_real_boolean_not_a_number(busy_log):
    assert verify(busy_log.app)["ok"] is True
    tamper(busy_log.app, "UPDATE activity_log SET action = 'x' WHERE seq = 4")
    assert verify(busy_log.app)["ok"] is False


def test_verify_says_it_cannot_see_a_cut_off_tail_yet(busy_log):
    assert verify(busy_log.app)["tail_checked"] is False


def test_verifying_writes_nothing(busy_log):
    with busy_log.app.app_context():
        before = activity_log.anchor()
    verify(busy_log.app)
    with busy_log.app.app_context():
        assert activity_log.anchor() == before


