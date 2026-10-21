"""Backup export and restore: permissions, file format, lossless round trip, and bad files."""
import io
from datetime import date

import pytest
from werkzeug.security import generate_password_hash

from conftest import login
from users import STATUSES

ALLOWED = set(STATUSES)


# ---- helpers -------------------------------------------------------------------------------------

def seed(flask_app, people=None, records=None):
    """people: [(name, department)], records: [(name, 'YYYY-MM-DD', status)]."""
    from extensions import db
    from users import AttendanceRecord, Person

    people = people if people is not None else [
        ("Ana", "Finance"),
        ("Ben", "Finance"),
        ("Cai", "Management"),   # a department that is not on the current list must survive
        ("Dee", "Holding"),      # no records at all: must survive as a roster-only row
    ]
    records = records if records is not None else [
        ("Ana", "2025-12-31", 1),   # a different year than the rest: dates are full ISO dates
        ("Ana", "2026-10-12", 1),
        ("Ana", "2026-10-13", 2),
        ("Ben", "2026-10-12", 0),
        ("Cai", "2026-10-13", 1),
    ]
    with flask_app.app_context():
        ids = {}
        for name, department in people:
            person = Person(name=name, department=department)
            db.session.add(person)
            db.session.flush()
            ids[name] = person.id
        for name, day, status in records:
            parsed = date.fromisoformat(day)
            db.session.add(AttendanceRecord(person_id=ids[name], date=parsed,
                                            day_of_week=parsed.isoweekday(), status=status))
        db.session.commit()
    return ids


def counts(flask_app):
    from users import AttendanceRecord, Person
    with flask_app.app_context():
        return Person.query.count(), AttendanceRecord.query.count()


def export_text(flask_app):
    import backup
    with flask_app.app_context():
        return backup.export_csv_text()


def restore(flask_app, text, replace=False):
    import backup
    with flask_app.app_context():
        return backup.restore_csv(io.StringIO(text), allowed_statuses=ALLOWED, replace=replace)


GOOD_HEADER = "person,department,date,status\r\n"     # the older format, still accepted when loading
FORMER_HEADER = "person,department,date,status,former\r\n"


# ---- the download endpoint -----------------------------------------------------------------------

def test_download_needs_a_login(flask_app):
    assert login(flask_app).get("/api/export/attendance.csv").status_code == 401


def test_download_is_admin_only(flask_app, add_users):
    add_users(("admin", True), ("alice", False))
    assert login(flask_app, "alice").get("/api/export/attendance.csv").status_code == 403
    assert login(flask_app, "admin").get("/api/export/attendance.csv").status_code == 200


def test_download_headers_and_file_name(flask_app, add_users):
    add_users(("admin", True))
    response = login(flask_app, "admin").get("/api/export/attendance.csv")
    assert response.mimetype == "text/csv"
    # 03:00 UTC on 14 Oct is 11:00 on 14 Oct in UTC+8
    assert response.headers["Content-Disposition"] == 'attachment; filename="attendance-backup-2026-10-14.csv"'
    assert response.headers["Cache-Control"] == "no-store"


def test_download_contents(flask_app, add_users):
    add_users(("admin", True), ("alice", False))
    seed(flask_app)
    text = login(flask_app, "admin").get("/api/export/attendance.csv").get_data(as_text=True)
    assert text.startswith("﻿")   # so Excel reads names as UTF-8
    lines = text.lstrip("﻿").splitlines()
    assert lines[0] == "person,department,date,status,former"
    assert lines[1:] == [
        "Ana,Finance,2025-12-31,1,0",
        "Ana,Finance,2026-10-12,1,0",
        "Ana,Finance,2026-10-13,2,0",
        "Ben,Finance,2026-10-12,0,0",
        "Cai,Management,2026-10-13,1,0",
        "Dee,Holding,,,0",          # a person with no records is still in the backup
    ]


def test_download_never_contains_logins_or_password_hashes(flask_app, add_users):
    add_users(("admin", True), ("alice", False))
    seed(flask_app)
    text = login(flask_app, "admin").get("/api/export/attendance.csv").get_data(as_text=True)
    assert "alice" not in text and "admin" not in text and "pbkdf2" not in text and "scrypt" not in text


def test_download_of_an_empty_database_is_just_the_header(flask_app, add_users):
    add_users(("admin", True))
    text = login(flask_app, "admin").get("/api/export/attendance.csv").get_data(as_text=True)
    assert text.lstrip("﻿").strip() == "person,department,date,status,former"


def test_names_with_commas_quotes_and_accents_survive(make_app):
    first, second = make_app("first"), make_app("second")
    seed(first, people=[('Zoë "Zed" O\'Neil, Jr.', "Finance")], records=[('Zoë "Zed" O\'Neil, Jr.', "2026-10-12", 1)])
    restore(second, export_text(first))
    assert export_text(second) == export_text(first)


# ---- restore: the round trip ---------------------------------------------------------------------

def test_round_trip_is_lossless(make_app):
    original, fresh = make_app("original"), make_app("fresh")
    seed(original)
    exported = export_text(original)

    result = restore(fresh, exported)

    assert result == {"people": 4, "records": 5}
    assert counts(fresh) == counts(original) == (4, 5)
    assert export_text(fresh) == exported    # identical file out of the restored database

def test_every_status_survives_a_backup_and_restore(make_app):
    fresh = make_app("fresh")
    days = ["2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15"]
    text = GOOD_HEADER + "".join(f"Ana,Finance,{day},{status}\r\n" for status, day in enumerate(days))
    assert restore(fresh, text) == {"people": 1, "records": 4}
    assert export_text(fresh).count("\r\n") == 5 # the header and one row for each status
    for status, day in enumerate(days):
        assert f"Ana,Finance,{day},{status},0" in export_text(fresh)
    

def test_restore_sets_day_of_week_from_the_date(make_app):
    from users import AttendanceRecord
    fresh = make_app("fresh")
    restore(fresh, GOOD_HEADER + "Ana,Finance,2026-10-14,1\r\n")   # a Wednesday
    with fresh.app_context():
        assert AttendanceRecord.query.one().day_of_week == 3


def test_restore_accepts_a_file_with_or_without_the_excel_bom(make_app):
    body = GOOD_HEADER + "Ana,Finance,2026-10-12,1\r\n"
    with_bom, without_bom = make_app("a"), make_app("b")
    assert restore(with_bom, "\ufeff" + body)["records"] == 1
    assert restore(without_bom, body.replace("\r\n", "\n"))["records"] == 1


def test_blank_department_becomes_holding(make_app):
    from users import Person
    fresh = make_app("fresh")
    restore(fresh, GOOD_HEADER + "Ana,,2026-10-12,1\r\n")
    with fresh.app_context():
        assert Person.query.one().department == "Holding"


def test_restore_clears_the_model_cache(make_app, tmp_path):
    import ml
    fresh = make_app("fresh")
    ml.MODELS_PATH.write_bytes(b"stale")
    assert ml.MODELS_PATH.exists()
    restore(fresh, GOOD_HEADER + "Ana,Finance,2026-10-12,1\r\n")
    assert not ml.MODELS_PATH.exists()


# ---- restore: what it refuses --------------------------------------------------------------------

def test_restore_refuses_a_database_that_already_has_data(make_app):
    import backup
    target = make_app("target")
    seed(target)
    before = export_text(target)
    with pytest.raises(backup.BackupError, match="already has people or attendance"):
        restore(target, GOOD_HEADER + "Zed,Finance,2026-10-12,1\r\n")
    assert export_text(target) == before


def test_replace_swaps_everything_and_unlinks_logins(make_app):
    from extensions import db
    from users import Person, User
    target = make_app("target")
    seed(target)
    with target.app_context():
        ana = Person.query.filter_by(name="Ana").one()
        db.session.add(User(username="alice", password=generate_password_hash("x" * 10), person_id=ana.id))
        db.session.commit()

    result = restore(target, GOOD_HEADER + "Zed,Finance,2026-10-12,1\r\n", replace=True)

    assert result == {"people": 1, "records": 1}
    with target.app_context():
        assert [p.name for p in Person.query.all()] == ["Zed"]
        assert User.query.filter_by(username="alice").one().person_id is None   # login kept, link dropped


BAD_FILES = {
    "missing column": ("person,department,date\r\nAna,Finance,2026-10-12\r\n", "Missing: status"),
    "empty person": (GOOD_HEADER + ",Finance,2026-10-12,1\r\n", "person is empty"),
    "bad date": (GOOD_HEADER + "Ana,Finance,12/10/2026,1\r\n", "not a date in YYYY-MM-DD"),
    "weekend": (GOOD_HEADER + "Ana,Finance,2026-10-17,1\r\n", "is a weekend"),
    "status out of range": (GOOD_HEADER + "Ana,Finance,2026-10-12,4\r\n", "must be one of 0, 1, 2, 3"),
    "status 1.0": (GOOD_HEADER + "Ana,Finance,2026-10-12,1.0\r\n", "must be one of"),
    "status word": (GOOD_HEADER + "Ana,Finance,2026-10-12,In\r\n", "must be one of"),
    "date without status": (GOOD_HEADER + "Ana,Finance,2026-10-12,\r\n", "both be filled in"),
    "status without date": (GOOD_HEADER + "Ana,Finance,,1\r\n", "both be filled in"),
    "duplicate record": (GOOD_HEADER + "Ana,Finance,2026-10-12,1\r\nAna,Finance,2026-10-12,0\r\n", "already has a record"),
    "two departments": (GOOD_HEADER + "Ana,Finance,2026-10-12,1\r\nAna,Manpower,2026-10-13,1\r\n", "two different departments"),
    "name too long": (GOOD_HEADER + ("x" * 81) + ",Finance,2026-10-12,1\r\n", "longer than 80"),
    "former is not 0 or 1": (FORMER_HEADER + "Ana,Finance,2026-10-12,1,2\r\n", "former must be 0 or 1"),
    "former is a word": (FORMER_HEADER + "Ana,Finance,2026-10-12,1,yes\r\n", "former must be 0 or 1"),
    "former on one row only": (FORMER_HEADER + "Ana,Finance,2026-10-12,1,1\r\nAna,Finance,2026-10-13,1,0\r\n",
                               "both former and not former"),
}


@pytest.mark.parametrize("label", BAD_FILES)
def test_a_bad_file_is_rejected_and_changes_nothing(make_app, label):
    import backup
    text, expected = BAD_FILES[label]
    target = make_app("target")
    with pytest.raises(backup.BackupError, match=expected):
        restore(target, text)
    assert counts(target) == (0, 0)


def test_one_bad_line_among_good_ones_writes_nothing(make_app):
    import backup
    target = make_app("target")
    text = GOOD_HEADER + "Ana,Finance,2026-10-12,1\r\nBen,Finance,2026-10-13,1\r\nCai,Finance,2026-10-14,9\r\n"
    with pytest.raises(backup.BackupError, match="line 4"):
        restore(target, text)
    assert counts(target) == (0, 0)


def test_all_problems_are_listed_with_their_line_numbers(make_app):
    import backup
    target = make_app("target")
    text = GOOD_HEADER + "Ana,Finance,2026-10-17,1\r\nBen,Finance,2026-10-12,7\r\n"
    with pytest.raises(backup.BackupError) as info:
        restore(target, text)
    message = str(info.value)
    assert "line 2" in message and "line 3" in message


# ---- the command line ----------------------------------------------------------------------------

def test_cli_export_then_import_round_trips(make_app, tmp_path):
    source, target = make_app("source"), make_app("target")
    seed(source)
    path = tmp_path / "backup.csv"

    assert source.test_cli_runner().invoke(args=["export-backup", str(path)]).exit_code == 0
    result = target.test_cli_runner().invoke(args=["import-backup", str(path)])

    assert result.exit_code == 0, result.output
    assert "Restored 4 people and 5 attendance records." in result.output
    assert export_text(target) == export_text(source)


def test_cli_import_refuses_a_non_empty_database_without_replace(make_app, tmp_path):
    target = make_app("target")
    seed(target)
    path = tmp_path / "backup.csv"
    path.write_text(GOOD_HEADER + "Zed,Finance,2026-10-12,1\r\n", encoding="utf-8")

    result = target.test_cli_runner().invoke(args=["import-backup", str(path)])

    assert result.exit_code != 0
    assert "already has people or attendance" in result.output
    assert counts(target) == (4, 5)


def test_cli_replace_asks_first_and_yes_skips_the_question(make_app, tmp_path):
    target = make_app("target")
    seed(target)
    path = tmp_path / "backup.csv"
    path.write_text(GOOD_HEADER + "Zed,Finance,2026-10-12,1\r\n", encoding="utf-8")
    runner = target.test_cli_runner()

    declined = runner.invoke(args=["import-backup", str(path), "--replace"], input="n\n")
    assert declined.exit_code != 0 and counts(target) == (4, 5)

    confirmed = runner.invoke(args=["import-backup", str(path), "--replace", "--yes"])
    assert confirmed.exit_code == 0, confirmed.output
    assert counts(target) == (1, 1)


def test_a_file_from_before_the_former_column_restores_everyone_as_active(make_app):
    from users import Person
    target = make_app("target")
    restore(target, GOOD_HEADER + "Ana,Finance,2026-10-12,1\r\nBen,Finance,,\r\n")
    with target.app_context():
        assert {p.name: p.former for p in Person.query} == {"Ana": False, "Ben": False}


def test_a_blank_former_cell_means_active_and_one_means_former(make_app):
    from users import Person
    target = make_app("target")
    restore(target, FORMER_HEADER + "Ana,Finance,2026-10-12,1,\r\nBen,Finance,2026-10-12,0,1\r\nCai,Holding,,,1\r\n")
    with target.app_context():
        assert {p.name: p.former for p in Person.query} == {"Ana": False, "Ben": True, "Cai": True}
