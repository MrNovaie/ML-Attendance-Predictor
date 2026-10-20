"""The sentences on the log page: describe() turns a log row into one readable line."""
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

import activity_log
from activity_log import describe


def row(action, who="admin_jo", **kw):
    base = dict(admin_name=who, action=action, person_name=None, login_name=None, old_value=None, new_value=None)
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.mark.parametrize("entry,expected", [
    (row("attendance_set", person_name="Ben", old_value="2026-10-14 Out", new_value="2026-10-14 In"),
     "admin_jo set Ben to In on 2026-10-14 (was Out)"),
    (row("attendance_set", person_name="Ben", old_value=None, new_value="2026-10-14 Partial Day"),
     "admin_jo set Ben to Partial Day on 2026-10-14 (no record before)"),
    (row("attendance_removed", person_name="Ben", old_value="2026-10-14 Partial Day"),
     "admin_jo removed Ben's record for 2026-10-14 (was Partial Day)"),
    (row("person_added", person_name="Zed", new_value="Finance"), "admin_jo added Zed to Finance"),
    (row("person_renamed", person_name="Benjamin", old_value="Ben", new_value="Benjamin"), "admin_jo renamed Ben to Benjamin"),
    (row("department_changed", person_name="Cai", old_value="Manpower", new_value="Finance"),
     "admin_jo moved Cai from Manpower to Finance"),
    (row("person_removed", person_name="Cai"), "admin_jo removed Cai"),
    (row("person_removed", person_name="Cai", login_name="carol"), "admin_jo removed Cai and unlinked login carol"),
    (row("person_restored", person_name="Cai"), "admin_jo restored Cai"),
    (row("login_created", login_name="dave", new_value="admin"), "admin_jo created the admin login dave"),
    (row("login_created", login_name="zed", person_name="Zed", new_value="user"), "admin_jo created the user login zed for Zed"),
    (row("login_deleted", login_name="bob", old_value="user"), "admin_jo deleted the user login bob"),
    (row("login_deleted", login_name="alice", person_name="Ana", old_value="user"),
     "admin_jo deleted the user login alice (it was linked to Ana)"),
    (row("login_linked", login_name="carol", person_name="Zed", old_value=None, new_value="Zed"), "admin_jo linked login carol to Zed"),
    (row("login_linked", login_name="carol", person_name="Zed", old_value="Cai", new_value="Zed"),
     "admin_jo linked login carol to Zed (it was linked to Cai)"),
    (row("login_unlinked", login_name="carol", person_name="Cai", old_value="Cai"), "admin_jo unlinked login carol from Cai"),
    (row("password_reset", login_name="bob"), "admin_jo reset the password of login bob"),
    (row("password_reset", login_name="alice", person_name="Ana"), "admin_jo reset the password of login alice (Ana)"),
    (row("department_request_confirmed", login_name="alice", person_name="Ana", old_value="Finance", new_value="Manpower"),
     "admin_jo approved the request from login alice to move Ana from Finance to Manpower"),
    (row("department_request_dismissed", login_name="alice", person_name="Ana", old_value="Manpower"),
     "admin_jo dismissed the request from login alice to move Ana to Manpower"),
    (row("backup_restored", who="system", old_value=None, new_value="3 people, 40 attendance records"),
     "system restored a backup: 3 people, 40 attendance records"),
    (row("backup_restored", who="system", old_value="replaced existing data", new_value="3 people, 40 attendance records"),
     "system restored a backup: 3 people, 40 attendance records (replaced existing data)"),
])
def test_the_sentence(entry, expected):
    assert describe(entry) == expected


def test_an_action_the_code_does_not_know_still_reads_sensibly():
    assert describe(row("something_new", person_name="Ana", login_name="alice")) == "admin_jo did something_new on Ana (login alice)"
    assert describe(row("something_new")) == "admin_jo did something_new"


# What the app ALWAYS fills in for each action (the "yes" cells of the table in the README). Everything else is optional.
ALWAYS_PRESENT = {
    "attendance_set": dict(person_name="Ana", new_value="2026-10-14 In"),
    "attendance_removed": dict(person_name="Ana", old_value="2026-10-14 In"),
    "person_added": dict(person_name="Ana", new_value="Finance"),
    "person_renamed": dict(person_name="Ann", old_value="Ana", new_value="Ann"),
    "department_changed": dict(person_name="Ana", old_value="Finance", new_value="Manpower"),
    "person_removed": dict(person_name="Ana"),
    "person_restored": dict(person_name="Ana"),
    "login_created": dict(login_name="alice", new_value="user"),
    "login_deleted": dict(login_name="alice", old_value="user"),
    "login_linked": dict(person_name="Ana", login_name="alice", new_value="Ana"),
    "login_unlinked": dict(person_name="Ana", login_name="alice", old_value="Ana"),
    "password_reset": dict(login_name="alice"),
    "department_request_confirmed": dict(person_name="Ana", login_name="alice", old_value="Finance", new_value="Manpower"),
    "department_request_dismissed": dict(person_name="Ana", login_name="alice", old_value="Manpower"),
    "backup_restored": dict(),
}


def test_the_table_above_covers_every_action():
    assert set(ALWAYS_PRESENT) == activity_log.KNOWN_ACTIONS


@pytest.mark.parametrize("action", sorted(ALWAYS_PRESENT))
def test_no_sentence_ever_prints_the_word_None(action):
    """Optional fields left empty (no person, no old value, ...) must read naturally, never as the word 'None'."""
    sentence = describe(row(action, **ALWAYS_PRESENT[action]))
    assert "None" not in sentence
    assert sentence.startswith("admin_jo ")


def test_every_action_the_app_can_write_has_wording():
    """Read app.py for every action name passed to log_admin / log_system. A new action without a sentence fails here."""
    source = (Path(__file__).resolve().parent.parent / "app.py").read_text(encoding="utf-8")
    found = set()
    for first, second in re.findall(r'log_(?:admin|system)\(\s*"(\w+)"(?:\s+if\s+[^"]*?else\s+"(\w+)")?', source):
        found.add(first)
        if second:
            found.add(second)
    assert found, "found no log calls: has the helper been renamed?"
    assert found <= activity_log.KNOWN_ACTIONS, f"no wording for: {sorted(found - activity_log.KNOWN_ACTIONS)}"
    assert activity_log.KNOWN_ACTIONS <= found, f"wording for actions nothing writes: {sorted(activity_log.KNOWN_ACTIONS - found)}"