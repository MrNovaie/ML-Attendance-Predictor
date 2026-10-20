"""The activity log (activity_log.py): the hash, the chain, the append-only rules and the check you write.

Tests under "verify_chain" fail with NotImplementedError until you write verify_chain() in activity_log.py.
"""
import hashlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

import activity_log
from activity_log import (GENESIS_HASH, HASHED_FIELDS, ActivityLog, anchor, append_entry, canonical_bytes,
                          ensure_activity_log, row_hash_for, verify_chain)
from extensions import db

KEY = "test-key"
OFFICE = timezone(timedelta(hours=8))
WHEN = datetime(2026, 10, 14, 11, 0, 0, tzinfo=OFFICE)
ADMIN = SimpleNamespace(id=7, username="admin")


@pytest.fixture(autouse=True)
def key(monkeypatch):
    monkeypatch.setenv("ACTIVITY_LOG_KEY", KEY)
    monkeypatch.delenv("ENV", raising=False)


@pytest.fixture
def log_app(flask_app):
    with flask_app.app_context():
        ensure_activity_log()
    return flask_app


def fill(app, count=5):
    """Five ordinary entries, committed."""
    with app.app_context():
        for i in range(1, count + 1):
            append_entry(ADMIN, "attendance_set", WHEN + timedelta(minutes=i),
                         person=SimpleNamespace(id=i, name=f"Person {i}"), old=f"2026-10-13 Out", new=f"2026-10-13 In")
        db.session.commit()


def tamper(sql, **params):
    """What someone holding the database file could do: drop the two triggers, then edit freely."""
    db.session.execute(text("DROP TRIGGER activity_log_no_update"))
    db.session.execute(text("DROP TRIGGER activity_log_no_delete"))
    db.session.execute(text(sql), params)
    db.session.commit()


def forge_from(seq):
    """An attacker WITHOUT the key rebuilds every hash from `seq` onward using plain SHA-256."""
    previous = db.session.get(ActivityLog, seq - 1)
    for row in db.session.query(ActivityLog).filter(ActivityLog.seq >= seq).order_by(ActivityLog.seq):
        row.prev_hash = GENESIS_HASH if previous is None else previous.row_hash
        row.row_hash = hashlib.sha256(canonical_bytes(row)).hexdigest()
        previous = row
    db.session.commit()


# ---- the hash --------------------------------------------------------------------------------------

def test_known_answer_pins_the_exact_bytes_and_hash():
    """The expected hash below was computed outside this code with:  openssl dgst -sha256 -hmac test-key
    If this test fails, the hashed layout changed and every old row would stop verifying."""
    row = SimpleNamespace(seq=1, admin_id=7, admin_name="admin", logged_at="2026-10-14T11:00:00+08:00",
                          action="person_added", person_id=3, person_name="Ana", login_id=None, login_name=None,
                          old_value=None, new_value="Finance", prev_hash=GENESIS_HASH)
    assert canonical_bytes(row) == (
        b'["v1",1,7,"admin","2026-10-14T11:00:00+08:00","person_added",3,"Ana",null,null,null,"Finance",'
        b'"0000000000000000000000000000000000000000000000000000000000000000"]'
    )
    assert row_hash_for(row) == "8049dc15ba5c4d428946eed8ade25792b67fdb80b7265dcc9cfa078c3612187a"


def test_every_column_except_the_hash_itself_is_hashed():
    columns = {column.name for column in ActivityLog.__table__.columns}
    assert columns - {"row_hash"} == set(HASHED_FIELDS)     # a new column that is not hashed would be a hole


BASE = dict(seq=1, admin_id=7, admin_name="admin", logged_at="2026-10-14T11:00:00+08:00", action="person_added",
            person_id=3, person_name="Ana", login_id=4, login_name="alice", old_value="a", new_value="b",
            prev_hash=GENESIS_HASH)
CHANGED = dict(seq=2, admin_id=8, admin_name="root", logged_at="2026-10-14T11:00:01+08:00", action="person_removed",
               person_id=9, person_name="Ben", login_id=5, login_name="bob", old_value="x", new_value="y",
               prev_hash="1" * 64)


@pytest.mark.parametrize("field", sorted(BASE))
def test_changing_any_single_column_changes_the_hash(field):
    assert row_hash_for(SimpleNamespace(**{**BASE, field: CHANGED[field]})) != row_hash_for(SimpleNamespace(**BASE))


@pytest.mark.parametrize("field", ["person_name", "login_name", "old_value", "new_value"])
def test_none_is_not_the_same_as_the_text_none_or_empty(field):
    hashes = {row_hash_for(SimpleNamespace(**{**BASE, field: value})) for value in (None, "None", "")}
    assert len(hashes) == 3


@pytest.mark.parametrize("field", ["person_id", "login_id"])
def test_no_id_is_not_the_same_as_id_zero(field):
    assert row_hash_for(SimpleNamespace(**{**BASE, field: None})) != row_hash_for(SimpleNamespace(**{**BASE, field: 0}))


def test_field_boundaries_cannot_be_shifted():
    one = SimpleNamespace(**{**BASE, "person_name": "ab", "login_name": "c"})
    two = SimpleNamespace(**{**BASE, "person_name": "a", "login_name": "bc"})
    assert row_hash_for(one) != row_hash_for(two)


def test_a_different_key_gives_a_different_hash(monkeypatch):
    row = SimpleNamespace(**BASE)
    before = row_hash_for(row)
    monkeypatch.setenv("ACTIVITY_LOG_KEY", "another-key")
    assert row_hash_for(row) != before


def test_production_refuses_to_run_without_a_key(monkeypatch):
    monkeypatch.delenv("ACTIVITY_LOG_KEY")
    monkeypatch.setenv("ENV", "production")
    with pytest.raises(RuntimeError, match="ACTIVITY_LOG_KEY"):
        row_hash_for(SimpleNamespace(**BASE))


def test_dev_without_a_key_still_works(monkeypatch):
    monkeypatch.delenv("ACTIVITY_LOG_KEY")
    assert len(row_hash_for(SimpleNamespace(**BASE))) == 64


# ---- appending -------------------------------------------------------------------------------------

def test_entries_are_numbered_and_chained(log_app):
    fill(log_app, 3)
    with log_app.app_context():
        rows = db.session.query(ActivityLog).order_by(ActivityLog.seq).all()
        assert [r.seq for r in rows] == [1, 2, 3]
        assert rows[0].prev_hash == GENESIS_HASH
        assert rows[1].prev_hash == rows[0].row_hash
        assert rows[2].prev_hash == rows[1].row_hash
        assert all(r.row_hash == row_hash_for(r) for r in rows)


def test_an_entry_records_who_when_what_on_whom_and_from_to(log_app):
    with log_app.app_context():
        append_entry(ADMIN, "login_linked", WHEN, person=SimpleNamespace(id=3, name="Ana"),
                     login=SimpleNamespace(id=4, username="alice"), old=None, new="alice")
        db.session.commit()
        row = db.session.get(ActivityLog, 1)
        assert (row.admin_id, row.admin_name) == (7, "admin")
        assert row.logged_at == "2026-10-14T11:00:00+08:00"
        assert row.action == "login_linked"
        assert (row.person_id, row.person_name, row.login_id, row.login_name) == (3, "Ana", 4, "alice")
        assert (row.old_value, row.new_value) == (None, "alice")


def test_the_system_itself_can_be_the_actor(log_app):
    with log_app.app_context():
        row = append_entry(None, "backup_restored", WHEN)
        assert (row.admin_id, row.admin_name) == (None, "system")


def test_the_time_is_kept_with_its_offset_and_a_naive_time_is_refused(log_app):
    with log_app.app_context():
        assert append_entry(ADMIN, "x", datetime(2026, 10, 14, 3, 0, 0, tzinfo=timezone.utc)).logged_at == \
            "2026-10-14T03:00:00+00:00"
        with pytest.raises(ValueError, match="timezone-aware"):
            append_entry(ADMIN, "x", datetime(2026, 10, 14, 11, 0, 0))
        # whole seconds only, so the stored text never depends on sub-second noise
        assert append_entry(ADMIN, "x", WHEN.replace(microsecond=500000)).logged_at == "2026-10-14T11:00:00+08:00"


def test_the_log_is_part_of_the_callers_transaction(log_app):
    """The change and its log row are saved together or not at all."""
    from users import Person
    with log_app.app_context():
        db.session.add(Person(name="Zed"))
        append_entry(ADMIN, "person_added", WHEN)
        db.session.rollback()
        assert db.session.query(ActivityLog).count() == 0
        assert Person.query.count() == 0
        db.session.add(Person(name="Zed"))
        append_entry(ADMIN, "person_added", WHEN)
        db.session.commit()
        assert db.session.query(ActivityLog).count() == 1
        assert Person.query.count() == 1


def test_two_writers_cannot_both_take_the_same_position(log_app):
    fill(log_app, 1)
    with log_app.app_context():
        db.session.add(ActivityLog(seq=1, admin_name="admin", logged_at="x", action="x", prev_hash=GENESIS_HASH,
                                   row_hash="y"))
        with pytest.raises(IntegrityError):
            db.session.flush()
        db.session.rollback()


def test_a_writer_working_from_a_stale_read_fails_instead_of_forking(log_app, monkeypatch):
    """Two workers read the same 'newest' row. The slower one must fail at once, with its change rolled back,
    and must not add a second entry at the same position."""
    fill(log_app, 1)
    with log_app.app_context():
        monkeypatch.setattr(activity_log, "_newest", lambda: None)      # this worker has not seen row 1 yet
        with pytest.raises(IntegrityError):
            append_entry(ADMIN, "late_writer", WHEN)
        db.session.rollback()
        assert [row.action for row in db.session.query(ActivityLog)] == ["attendance_set"]


def test_rows_outlive_the_admin_and_the_person_they_name(log_app, add_users):
    from users import Person, User
    ids = add_users(("carol", True))
    with log_app.app_context():
        carol = db.session.get(User, ids["carol"])
        person = Person(name="Zed")
        db.session.add(person)
        db.session.flush()
        append_entry(carol, "person_added", WHEN, person=person, new="Holding")
        db.session.commit()
        db.session.delete(db.session.get(User, ids["carol"]))
        db.session.delete(db.session.get(Person, person.id))
        db.session.commit()                                   # neither delete is blocked by the log
        row = db.session.get(ActivityLog, 1)
        assert (row.admin_id, row.admin_name, row.person_name) == (ids["carol"], "carol", "Zed")


def test_anchor_is_the_newest_seq_and_hash(log_app):
    with log_app.app_context():
        assert anchor() is None
    fill(log_app, 3)
    with log_app.app_context():
        newest = db.session.get(ActivityLog, 3)
        assert anchor() == (3, newest.row_hash)


# ---- append-only -----------------------------------------------------------------------------------

def test_the_database_refuses_to_edit_a_row(log_app):
    fill(log_app, 2)
    with log_app.app_context():
        with pytest.raises(DBAPIError, match="append-only"):
            db.session.execute(text("UPDATE activity_log SET new_value = 'changed' WHERE seq = 1"))
        db.session.rollback()


def test_the_database_refuses_to_delete_a_row(log_app):
    fill(log_app, 2)
    with log_app.app_context():
        with pytest.raises(DBAPIError, match="append-only"):
            db.session.execute(text("DELETE FROM activity_log WHERE seq = 2"))
        db.session.rollback()
        assert db.session.query(ActivityLog).count() == 2


def test_ensure_activity_log_can_run_twice(log_app):
    with log_app.app_context():
        ensure_activity_log()
        ensure_activity_log()
    fill(log_app, 1)


# ---- verify_chain: your turn -----------------------------------------------------------------------

def test_an_untouched_log_verifies(log_app):
    fill(log_app, 5)
    with log_app.app_context():
        assert verify_chain() is None


def test_an_empty_log_verifies(log_app):
    with log_app.app_context():
        assert verify_chain() is None


def test_a_log_still_verifies_after_its_admin_and_person_are_deleted(log_app, add_users):
    from users import Person, User
    ids = add_users(("carol", True))
    with log_app.app_context():
        carol = db.session.get(User, ids["carol"])
        person = Person(name="Zed")
        db.session.add(person)
        db.session.flush()
        append_entry(carol, "person_added", WHEN, person=person, new="Holding")
        db.session.commit()
        db.session.delete(db.session.get(User, ids["carol"]))
        db.session.delete(db.session.get(Person, person.id))
        db.session.commit()
        assert verify_chain() is None


def test_verify_catches_an_edited_row(log_app):
    fill(log_app, 5)
    with log_app.app_context():
        tamper("UPDATE activity_log SET new_value = 'Out' WHERE seq = 3")
        assert verify_chain() == 3


def test_verify_catches_someone_swapping_the_admin_name(log_app):
    """Blaming a different admin must break the chain."""
    fill(log_app, 5)
    with log_app.app_context():
        tamper("UPDATE activity_log SET admin_name = 'someone-else' WHERE seq = 2")
        assert verify_chain() == 2


def test_verify_catches_a_deleted_middle_row(log_app):
    fill(log_app, 5)
    with log_app.app_context():
        tamper("DELETE FROM activity_log WHERE seq = 3")
        assert verify_chain() == 4


def test_verify_catches_a_deleted_first_row(log_app):
    fill(log_app, 5)
    with log_app.app_context():
        tamper("DELETE FROM activity_log WHERE seq = 1")
        assert verify_chain() == 2


def test_verify_catches_a_chain_rebuilt_without_the_key(log_app):
    """Edit row 3 and recompute every hash after it with plain SHA-256: the chain links up, the HMAC does not."""
    fill(log_app, 5)
    with log_app.app_context():
        tamper("UPDATE activity_log SET new_value = 'Out' WHERE seq = 3")
        forge_from(3)
        assert verify_chain() == 3


def test_verify_with_the_wrong_key_fails_at_the_first_row(log_app, monkeypatch):
    fill(log_app, 5)
    monkeypatch.setenv("ACTIVITY_LOG_KEY", "not-the-key")
    with log_app.app_context():
        assert verify_chain() == 1


def test_cutting_off_the_tail_is_invisible_to_the_chain_but_not_to_the_anchor(log_app):
    fill(log_app, 5)
    with log_app.app_context():
        saved = anchor()                                    # what the scheduled backup would have kept: (5, hash)
        tamper("DELETE FROM activity_log WHERE seq IN (4, 5)")
        assert verify_chain() is None                       # the shorter chain is still valid on its own
        assert verify_chain(anchor=saved) == 5              # the anchor knows row 5 should exist


def test_an_anchor_that_matches_passes(log_app):
    fill(log_app, 5)
    with log_app.app_context():
        assert verify_chain(anchor=anchor()) is None


def test_an_anchor_with_the_wrong_hash_fails_at_its_position(log_app):
    fill(log_app, 5)
    with log_app.app_context():
        assert verify_chain(anchor=(5, "f" * 64)) == 5
