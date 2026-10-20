"""GET /api/activity-log: what the log page reads (rows, filters, dates, paging)."""
import pytest

from conftest import LOG_URL, fetch_log, log_seqs, login


# ---- what a row looks like -----------------------------------------------------------------------

def test_newest_first_with_the_sentence_and_the_raw_columns(busy_log):
    entries = fetch_log(busy_log.app)["entries"]
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
    assert fetch_log(world.app) == {"entries": [], "more": False}


def test_reading_the_log_writes_nothing(busy_log):
    import activity_log
    with busy_log.app.app_context():
        before = activity_log.anchor()
    fetch_log(busy_log.app, "?admin=boss&person=ben&from=2026-10-01&to=2026-10-31&limit=2")
    with busy_log.app.app_context():
        assert activity_log.anchor() == before


# ---- filters ---------------------------------------------------------------------------------------

def test_filter_by_admin(busy_log):
    assert log_seqs(fetch_log(busy_log.app, "?admin=boss")) == [4, 2]
    assert log_seqs(fetch_log(busy_log.app, "?admin=admin")) == [3, 1]
    assert log_seqs(fetch_log(busy_log.app, "?admin=nobody")) == []


def test_the_admin_filter_still_works_after_that_admin_s_login_is_deleted(busy_log):
    from users import User
    with busy_log.app.app_context():
        boss_id = User.query.filter_by(username="boss").one().id
    assert login(busy_log.app, "admin").delete(f"/api/users/{boss_id}").status_code == 200
    body = fetch_log(busy_log.app, "?admin=boss")
    assert log_seqs(body) == [4, 2]                                   # the deletion itself (row 5) is by "admin"
    assert body["entries"][0]["text"] == "boss added Zed to Finance"
    assert fetch_log(busy_log.app)["entries"][0]["text"] == "admin deleted the admin login boss"


def test_filter_by_action(busy_log):
    assert log_seqs(fetch_log(busy_log.app, "?action=person_renamed")) == [2]
    assert log_seqs(fetch_log(busy_log.app, "?action=login_deleted")) == []


def test_filter_by_person_is_a_case_insensitive_contains(busy_log):
    assert log_seqs(fetch_log(busy_log.app, "?person=benj")) == [2]
    assert log_seqs(fetch_log(busy_log.app, "?person=ZED")) == [4]
    assert log_seqs(fetch_log(busy_log.app, "?person=n")) == [2, 1]           # BenjaMIN and Ana


@pytest.mark.parametrize("typed", ["%", "_", "Z_d", "100%"])
def test_percent_and_underscore_in_a_search_mean_themselves(busy_log, typed):
    assert log_seqs(fetch_log(busy_log.app, f"?person={typed}")) == []


def test_filters_combine(busy_log):
    assert log_seqs(fetch_log(busy_log.app, "?admin=boss&person=zed")) == [4]
    assert log_seqs(fetch_log(busy_log.app, "?admin=admin&person=zed")) == []


def test_blank_filters_are_ignored(busy_log):
    assert log_seqs(fetch_log(busy_log.app, "?admin=&person=&action=&from=&to=")) == [4, 3, 2, 1]


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
def test_date_filters(busy_log, query, expected):
    assert log_seqs(fetch_log(busy_log.app, query)) == expected


@pytest.mark.parametrize("query", [
    "?from=yesterday", "?to=14/10/2026", "?from=2026-13-45", "?to=2026-10-1x", "?from=2026-10-14T11:00",
])
def test_a_bad_date_is_a_400(busy_log, query):
    response = login(busy_log.app, "admin").get(LOG_URL + query)
    assert response.status_code == 400
    assert response.get_json()["code"] == 400


# ---- paging ------------------------------------------------------------------------------------------

def test_paging_walks_the_log_with_no_gaps_and_no_repeats(busy_log):
    first = fetch_log(busy_log.app, "?limit=2")
    assert log_seqs(first) == [4, 3] and first["more"] is True
    second = fetch_log(busy_log.app, "?limit=2&before=3")
    assert log_seqs(second) == [2, 1] and second["more"] is False


def test_more_is_false_when_the_page_is_exactly_full(busy_log):
    body = fetch_log(busy_log.app, "?limit=4")
    assert log_seqs(body) == [4, 3, 2, 1] and body["more"] is False


def test_paging_works_together_with_a_filter(busy_log):
    first = fetch_log(busy_log.app, "?admin=boss&limit=1")
    assert log_seqs(first) == [4] and first["more"] is True
    assert log_seqs(fetch_log(busy_log.app, "?admin=boss&limit=1&before=4")) == [2]


@pytest.mark.parametrize("limit,count", [("0", 1), ("-5", 1), ("abc", 4), ("", 4)])
def test_a_silly_limit_does_not_break_the_page(busy_log, limit, count):
    assert len(fetch_log(busy_log.app, f"?limit={limit}")["entries"]) == count


def test_the_page_size_is_capped_at_500(world, set_clock):
    import activity_log
    from datetime import datetime, timedelta, timezone
    from extensions import db
    now = datetime(2026, 10, 14, 11, 0, tzinfo=timezone(timedelta(hours=8)))
    with world.app.app_context():
        for _ in range(510):
            activity_log.append_entry(None, "backup_restored", now)
        db.session.commit()
    body = fetch_log(world.app, "?limit=100000")
    assert len(body["entries"]) == 500 and body["more"] is True
    assert len(fetch_log(world.app)["entries"]) == 100                  # the default

