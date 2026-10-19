"""The sentences on the log page."""
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

import activity_log
from activity_log import describe
"""GET /api/activity-log: what the log page reads."""
import pytest

from conftest import login

URL = "/api/activity-log"


def fetch(app, query="", who="admin"):
    response = login(app, who).get(URL + query)
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()


def seqs(body):
    return [e["seq"] for e in body["entries"]]


@pytest.fixture
def busy(world, add_users, set_clock):
    """Four log rows by two admins over three days (all times are the office clock, UTC+8):
       1  admin   Mon 12th 10:00  set Ana to In
       2  boss    Tue 13th 23:30  renamed Ben to Benjamin      (late evening: still the 13th)
       3  admin   Wed 14th 11:00  moved Cai to Finance
       4  boss    Wed 14th 11:00  added Zed
    """
    add_users(("boss", True))
    ana, ben, cai = world.people["Ana"], world.people["Ben"], world.people["Cai"]
    admin, boss = login(world.app, "admin"), login(world.app, "boss")

    set_clock("2026-10-12T02:00:00")
    assert admin.put("/api/attendance", json={"date": "2026-10-12", "records": [{"person_id": ana, "status": 1}]}).status_code == 200
    set_clock("2026-10-13T15:30:00")
    assert boss.patch(f"/api/people/{ben}", json={"name": "Benjamin"}).status_code == 200
    set_clock("2026-10-14T03:00:00")
    assert admin.patch(f"/api/people/{cai}", json={"department": "Finance"}).status_code == 200
    assert boss.post("/api/table_persons", json={"name": "Zed", "department": "Finance"}).status_code == 201
    return world


# ---- what a row looks like -----------------------------------------------------------------------

def test_newest_first_with_the_sentence_and_the_raw_columns(busy):
    entries = fetch(busy.app)["entries"]
    assert [e["seq"] for e in entries] == [4, 3, 2, 1]
    assert entries[0]["text"] == "boss added Zed to Finance"
    assert entries[1]["text"] == "admin moved Cai from Manpower to Finance"
    assert entries[2]["text"] == "boss renamed Ben to Benjamin"
    assert entries[3]["text"] == "admin set Ana to In on 2026-10-12 (no record before)"
    renamed = entries[2]
    assert (renamed["admin"], renamed["action"], renamed["old"], renamed["new"], renamed["person"]) == \
           ("boss", "person_renamed", "Ben", "Benjamin", "Benjamin")
    assert renamed["logged_at"] == "2026-10-13T23:30:00+08:00"


def test_an_empty_log_is_an_empty_list(world):
    assert fetch(world.app) == {"entries": [], "more": False}


def test_reading_the_log_writes_nothing(busy):
    import activity_log
    with busy.app.app_context():
        before = activity_log.anchor()
    fetch(busy.app, "?admin=boss&person=ben&from=2026-10-01&to=2026-10-31&limit=2")
    with busy.app.app_context():
        assert activity_log.anchor() == before


# ---- filters ---------------------------------------------------------------------------------------

def test_filter_by_admin(busy):
    assert seqs(fetch(busy.app, "?admin=boss")) == [4, 2]
    assert seqs(fetch(busy.app, "?admin=admin")) == [3, 1]
    assert seqs(fetch(busy.app, "?admin=nobody")) == []


def test_the_admin_filter_still_works_after_that_admin_s_login_is_deleted(busy):
    from users import User
    with busy.app.app_context():
        boss_id = User.query.filter_by(username="boss").one().id
    assert login(busy.app, "admin").delete(f"/api/users/{boss_id}").status_code == 200
    body = fetch(busy.app, "?admin=boss")
    assert seqs(body) == [4, 2]                                   # the deletion itself (row 5) is by "admin"
    assert body["entries"][0]["text"] == "boss added Zed to Finance"
    assert fetch(busy.app)["entries"][0]["text"] == "admin deleted the admin login boss"


def test_filter_by_action(busy):
    assert seqs(fetch(busy.app, "?action=person_renamed")) == [2]
    assert seqs(fetch(busy.app, "?action=login_deleted")) == []


def test_filter_by_person_is_a_case_insensitive_contains(busy):
    assert seqs(fetch(busy.app, "?person=benj")) == [2]
    assert seqs(fetch(busy.app, "?person=ZED")) == [4]
    assert seqs(fetch(busy.app, "?person=n")) == [2, 1]           # BenjaMIN and Ana


@pytest.mark.parametrize("typed", ["%", "_", "Z_d", "100%"])
def test_percent_and_underscore_in_a_search_mean_themselves(busy, typed):
    assert seqs(fetch(busy.app, f"?person={typed}")) == []


def test_filters_combine(busy):
    assert seqs(fetch(busy.app, "?admin=boss&person=zed")) == [4]
    assert seqs(fetch(busy.app, "?admin=admin&person=zed")) == []


def test_blank_filters_are_ignored(busy):
    assert seqs(fetch(busy.app, "?admin=&person=&action=&from=&to=")) == [4, 3, 2, 1]


# ---- dates -------------------------------------------------------------------------------------------

@pytest.mark.parametrize("query,expected", [
    ("?from=2026-10-14", [4, 3]),
    ("?from=2026-10-13", [4, 3, 2]),                  # inclusive
    ("?to=2026-10-13", [2, 1]),                       # 23:30 on the 13th is inside "to the 13th"
    ("?to=2026-10-12", [1]),
    ("?from=2026-10-13&to=2026-10-13", [2]),          # one whole day
    ("?from=2026-10-12&to=2026-10-12", [1]),
    ("?from=2026-10-15", []),
    ("?to=2026-10-11", []),
    ("?from=2026-10-14&to=2026-10-13", []),           # backwards range: nothing, not an error
], ids=lambda v: v if isinstance(v, str) else "")
def test_date_filters(busy, query, expected):
    assert seqs(fetch(busy.app, query)) == expected


@pytest.mark.parametrize("query", [
    "?from=yesterday", "?to=14/10/2026", "?from=2026-13-45", "?to=2026-10-1x", "?from=2026-10-14T11:00",
])
def test_a_bad_date_is_a_400(busy, query):
    response = login(busy.app, "admin").get(URL + query)
    assert response.status_code == 400
    assert response.get_json()["code"] == 400


# ---- paging ------------------------------------------------------------------------------------------

def test_paging_walks_the_log_with_no_gaps_and_no_repeats(busy):
    first = fetch(busy.app, "?limit=2")
    assert seqs(first) == [4, 3] and first["more"] is True
    second = fetch(busy.app, "?limit=2&before=3")
    assert seqs(second) == [2, 1] and second["more"] is False


def test_more_is_false_when_the_page_is_exactly_full(busy):
    body = fetch(busy.app, "?limit=4")
    assert seqs(body) == [4, 3, 2, 1] and body["more"] is False


def test_paging_works_together_with_a_filter(busy):
    first = fetch(busy.app, "?admin=boss&limit=1")
    assert seqs(first) == [4] and first["more"] is True
    assert seqs(fetch(busy.app, "?admin=boss&limit=1&before=4")) == [2]


@pytest.mark.parametrize("limit,count", [("0", 1), ("-5", 1), ("abc", 4), ("", 4)])
def test_a_silly_limit_does_not_break_the_page(busy, limit, count):
    assert len(fetch(busy.app, f"?limit={limit}")["entries"]) == count


def test_the_page_size_is_capped_at_500(world, set_clock):
    import activity_log
    from datetime import datetime, timedelta, timezone
    from extensions import db
    now = datetime(2026, 10, 14, 11, 0, tzinfo=timezone(timedelta(hours=8)))
    with world.app.app_context():
        for _ in range(510):
            activity_log.append_entry(None, "backup_restored", now)
        db.session.commit()
    body = fetch(world.app, "?limit=100000")
    assert len(body["entries"]) == 500 and body["more"] is True
    assert len(fetch(world.app)["entries"]) == 100                  # the default


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