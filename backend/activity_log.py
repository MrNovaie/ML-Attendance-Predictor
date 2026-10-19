"""The admin activity log: who changed what, when, on whom, from what to what. Tamper-evident and append-only.

How it protects itself (the design, in one place):

* One table (activity_log) in the same database. append_entry() adds a row to the CALLER's transaction and never
  commits, so a change and its log row are saved together or not at all.
* Each row stores `prev_hash` (the previous row's row_hash; the first row points at GENESIS_HASH) and its own
  `row_hash`: an HMAC-SHA256, keyed with ACTIVITY_LOG_KEY from the server's environment, over every other column.
  Edit any column, delete a middle row or insert one and the chain stops matching. Without the key, nobody can
  recompute the hashes after an edit.
* Two triggers make the database itself refuse UPDATE and DELETE on the table. That stops admins and bugs in this
  app. Someone with the database file can still drop the triggers, which is what the HMAC is for.
* Cutting rows off the END of the log leaves a perfectly valid chain. anchor() returns the newest (seq, row_hash);
  keep a copy somewhere the database's editor can't reach (the scheduled backup, item 6) and pass it to
  verify_chain(anchor=...).

Names and ids are stored as plain copies, with no foreign keys: a row still reads correctly after the admin's login
or the person is removed, and removing them can never delete or block a log row.

Not covered: root on the server can read the key and rewrite everything. That is the line the design draws.
"""
import hashlib
import hmac
import json
import logging
import os

from sqlalchemy import text

from extensions import db

logger = logging.getLogger(__name__)

GENESIS_HASH = "0" * 64
FORMAT_VERSION = "v1"          # first thing inside every hash, so the hashed layout can change one day without confusion

# Every column except row_hash goes into the hash, in this order. test_log.py checks this list against the table.
HASHED_FIELDS = (
    "seq", "admin_id", "admin_name", "logged_at", "action",
    "person_id", "person_name", "login_id", "login_name",
    "old_value", "new_value", "prev_hash",
)


class ActivityLog(db.Model):
    __tablename__ = "activity_log"

    # Position in the log: 1, 2, 3 ... The primary key is what makes two writers unable to both claim the same
    # position, so the chain can never fork.
    seq = db.Column(db.Integer, primary_key=True, autoincrement=False)
    # Who. Plain copies on purpose, never foreign keys (see the module docstring).
    admin_id = db.Column(db.Integer, nullable=True)
    admin_name = db.Column(db.String(80), nullable=False)
    # When, as text, in the server's clock with its offset: "2026-10-14T11:00:00+08:00". Text keeps it byte-for-byte
    # the same on the way back out of the database, which the hash needs.
    logged_at = db.Column(db.String(32), nullable=False)
    # What: a short action name, e.g. "person_removed".
    action = db.Column(db.String(40), nullable=False)
    # On whom: a person, a login, both, or neither. Ids and names are copies taken at the time.
    person_id = db.Column(db.Integer, nullable=True)
    person_name = db.Column(db.String(80), nullable=True)
    login_id = db.Column(db.Integer, nullable=True)
    login_name = db.Column(db.String(80), nullable=True)
    # From what to what. NULL means "none" (nothing before a creation, nothing after a removal).
    old_value = db.Column(db.Text, nullable=True)
    new_value = db.Column(db.Text, nullable=True)
    prev_hash = db.Column(db.String(64), nullable=False)
    row_hash = db.Column(db.String(64), nullable=False)


_warned_dev_key = False


def _key() -> bytes:
    """The HMAC key, from the environment. Same rule as SECRET_KEY: strict in production, an insecure default in dev."""
    global _warned_dev_key
    key = os.environ.get("ACTIVITY_LOG_KEY")
    if key:
        return key.encode("utf-8")
    if os.environ.get("ENV") == "production":
        raise RuntimeError("ACTIVITY_LOG_KEY must be set when ENV=production")
    if not _warned_dev_key:
        logger.warning("Using insecure dev ACTIVITY_LOG_KEY. Set ACTIVITY_LOG_KEY in production.")
        _warned_dev_key = True
    return b"dev-only-activity-log-key"


def canonical_bytes(row) -> bytes:
    """Exactly the bytes that get hashed for `row` (anything with the HASHED_FIELDS as attributes).

    A JSON array, so field boundaries are unambiguous (("ab", "c") and ("a", "bc") differ), None is `null` and
    never the same as the text "None" or "", and the same values always give the same bytes.
    """
    values = [FORMAT_VERSION] + [getattr(row, name) for name in HASHED_FIELDS]
    return json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def row_hash_for(row) -> str:
    """The row_hash this row SHOULD have, computed from its other columns with the server's key."""
    return hmac.new(_key(), canonical_bytes(row), hashlib.sha256).hexdigest()


def _newest():
    """The entry with the highest seq, or None when the log is empty."""
    return db.session.query(ActivityLog).order_by(ActivityLog.seq.desc()).first()


def append_entry(admin, action: str, when, *, person=None, login=None, old=None, new=None) -> ActivityLog:
    """Add one entry to the log, inside the caller's transaction. The caller commits (or rolls back) with its change.

    admin   anything with .id and .username (the logged-in admin), or None for the system itself
    when    a timezone-aware datetime: pass the app's now_local() (UTC+8)
    person  anything with .id and .name, if a person was affected
    login   anything with .id and .username, if a login was affected
    old/new the value before and after (stored as text); None when there isn't one

    If two requests try to take the same position, the second fails with IntegrityError at the flush below and its
    whole transaction (the change included) rolls back. A failed write is safe. A forked chain is not.
    """
    if when.tzinfo is None:
        raise ValueError("when must be timezone-aware (use now_local())")
    last = _newest()
    row = ActivityLog(
        seq=1 if last is None else last.seq + 1,
        admin_id=None if admin is None else admin.id,
        admin_name="system" if admin is None else admin.username,
        logged_at=when.isoformat(timespec="seconds"),
        action=action,
        person_id=None if person is None else person.id,
        person_name=None if person is None else person.name,
        login_id=None if login is None else login.id,
        login_name=None if login is None else login.username,
        old_value=None if old is None else str(old),
        new_value=None if new is None else str(new),
        prev_hash=GENESIS_HASH if last is None else last.row_hash,
    )
    row.row_hash = row_hash_for(row)
    db.session.add(row)
    db.session.flush()
    return row


def anchor():
    """(seq, row_hash) of the newest entry, or None for an empty log. Copy this somewhere the database's editor
    can't reach (the scheduled backup), then hand it back to verify_chain(anchor=...) to catch a cut-off tail."""
    last = _newest()
    return None if last is None else (last.seq, last.row_hash)


def verify_chain(anchor=None):
    expected_seq, expected_prev_hash = 1, GENESIS_HASH  # the seq and the hash the next row should have

    for row in db.session.query(ActivityLog).order_by(ActivityLog.seq):
        if row.seq != expected_seq or not hmac.compare_digest(row.row_hash, row_hash_for(row)) or not hmac.compare_digest(row.prev_hash, expected_prev_hash):
            return row.seq
        else:
            expected_seq += 1
            expected_prev_hash = row.row_hash
    if anchor is not None:
        anchored_seq, anchored_hash = anchor           
        row = db.session.get(ActivityLog, anchored_seq)
        if row is None or row.row_hash != anchored_hash:
            return anchored_seq
    return None


def ensure_activity_log() -> None:
    """Create the table if it is missing and install the two triggers that refuse UPDATE and DELETE.

    db.create_all() makes the table but knows nothing about triggers, so run this at startup (it is safe to repeat),
    next to the other ensure_* helpers.
    """
    ActivityLog.__table__.create(bind=db.engine, checkfirst=True)
    with db.engine.begin() as conn:
        for event in ("UPDATE", "DELETE"):
            conn.execute(text(
                f"CREATE TRIGGER IF NOT EXISTS activity_log_no_{event.lower()} BEFORE {event} ON activity_log "
                "BEGIN SELECT RAISE(ABORT, 'activity_log is append-only'); END"
            ))