"""Attendance backup: every person and attendance record in one CSV, and a way to load it back.

File format (one header row, then one row per record):

    person,department,date,status
    Ana,Finance,2026-10-12,1
    Ben,Finance,2026-10-12,0
    Cai,Holding,,

* date is ISO (YYYY-MM-DD), so history can span any number of years.
* status is the numeric code stored in the database (0 = Out, 1 = In, 2 = Partial Day).
* A person with no records at all gets a single row with an empty date and status, so the roster
  survives a round trip too.

Not included, on purpose: logins and their person links. Password hashes should not sit in a
spreadsheet. After a restore, an admin links the logins to people again.

Restoring validates the whole file first and writes in one transaction, so a bad file changes nothing.
"""

import csv
import io
from collections import defaultdict
from datetime import date

from extensions import db
from ml import invalidate_models
from users import DEFAULT_DEPARTMENT, AttendanceRecord, Person, User

COLUMNS = ["person", "department", "date", "status"]
# Excel needs a byte-order mark to open a UTF-8 CSV with accented names intact.
BOM = "﻿"
MAX_ERRORS_SHOWN = 10
_NAME_LENGTH = Person.name.type.length
_DEPARTMENT_LENGTH = Person.department.type.length


class BackupError(ValueError):
    """The backup file can't be loaded. The message says what is wrong and on which line."""


def _department_of(person) -> str:
    return (person.department or "").strip() or DEFAULT_DEPARTMENT


def export_rows():
    """Yield [person, department, date, status] for the whole database, sorted by person then date."""
    records_by_person = defaultdict(list)
    for record in AttendanceRecord.query.order_by(AttendanceRecord.date):
        records_by_person[record.person_id].append(record)
    for person in Person.query.order_by(Person.name):
        department = _department_of(person)
        records = records_by_person.get(person.id)
        if not records:
            yield [person.name, department, "", ""]
            continue
        for record in records:
            yield [person.name, department, record.date.isoformat(), record.status]


def export_csv_text() -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(COLUMNS)
    writer.writerows(export_rows())
    return BOM + out.getvalue()


def _parse(stream, allowed_statuses):
    """Read the whole file into (departments_by_person, records). Raises BackupError listing every
    problem found (up to a limit), so the user can fix the file in one go."""
    reader = csv.DictReader(stream)
    # A leading BOM (written for Excel) is not part of the first column name.
    header = [(name or "").strip().lstrip(BOM).lower() for name in (reader.fieldnames or [])]
    missing = [column for column in COLUMNS if column not in header]
    if missing:
        raise BackupError(
            f"The file must have the columns {', '.join(COLUMNS)}. Missing: {', '.join(missing)}."
        )
    reader.fieldnames = header

    errors = []
    departments = {}  # person name -> department
    records = {}      # (person name, date) -> status

    def problem(line, message):
        errors.append(f"line {line}: {message}")

    for row in reader:
        line = reader.line_num
        name = (row.get("person") or "").strip()
        department = (row.get("department") or "").strip() or DEFAULT_DEPARTMENT
        day_text = (row.get("date") or "").strip()
        status_text = (row.get("status") or "").strip()

        if not name:
            problem(line, "person is empty.")
            continue
        if len(name) > _NAME_LENGTH:
            problem(line, f"person is longer than {_NAME_LENGTH} characters.")
            continue
        if len(department) > _DEPARTMENT_LENGTH:
            problem(line, f"department is longer than {_DEPARTMENT_LENGTH} characters.")
            continue
        if departments.setdefault(name, department) != department:
            problem(line, f"{name} appears with two different departments "
                          f"({departments[name]} and {department}).")
            continue

        if not day_text and not status_text:
            continue  # roster-only row: the person exists, nothing recorded
        if not day_text or not status_text:
            problem(line, "date and status must both be filled in, or both left empty.")
            continue
        try:
            day = date.fromisoformat(day_text)
        except ValueError:
            problem(line, f"'{day_text}' is not a date in YYYY-MM-DD form.")
            continue
        if day.weekday() > 4:
            problem(line, f"{day_text} is a weekend. Attendance is only kept for weekdays.")
            continue
        # Compare the text itself, so "1.0", "+1" and "True" are not mistaken for 1.
        allowed_text = {str(status) for status in allowed_statuses}
        if status_text not in allowed_text:
            problem(line, f"status '{status_text}' must be one of {', '.join(sorted(allowed_text))}.")
            continue
        key = (name, day)
        if key in records:
            problem(line, f"{name} already has a record for {day_text}.")
            continue
        records[key] = int(status_text)

    if errors:
        shown = errors[:MAX_ERRORS_SHOWN]
        extra = len(errors) - len(shown)
        if extra:
            shown.append(f"... and {extra} more.")
        raise BackupError("The backup file has problems:\n" + "\n".join(shown))
    return departments, records


def restore_csv(stream, *, allowed_statuses, replace=False) -> dict:
    """Load a backup into the database and return {'people': n, 'records': n}.

    Refuses to touch a database that already has people or attendance unless replace=True, which
    deletes all of it first (and unlinks every login from its person). Nothing is written if the
    file has any problem.
    """
    departments, records = _parse(stream, allowed_statuses)

    has_data = Person.query.first() is not None or AttendanceRecord.query.first() is not None
    if has_data and not replace:
        raise BackupError(
            "The database already has people or attendance. Restoring would mix the two. "
            "Use --replace to delete what is there first (make a backup of it before you do)."
        )

    try:
        if has_data:
            User.query.update({User.person_id: None})
            AttendanceRecord.query.delete()
            Person.query.delete()
            db.session.flush()
        people = {}
        for name in sorted(departments):
            person = Person(name=name, department=departments[name])
            db.session.add(person)
            people[name] = person
        db.session.flush()  # assigns the ids
        for (name, day), status in sorted(records.items()):
            db.session.add(AttendanceRecord(
                person_id=people[name].id,
                date=day,
                day_of_week=day.isoweekday(),
                status=status,
            ))
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    # Records were deleted and re-added in bulk: make the models retrain from the restored rows.
    invalidate_models()
    return {"people": len(people), "records": len(records)}