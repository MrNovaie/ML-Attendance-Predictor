"""GET /api/activity-log.csv: the download, its filters, and the formula protection."""
import csv
import io

import pytest

import activity_log
from conftest import busy_log, fetch_log, log_seqs, login  # noqa: F401  (busy_log is a fixture)

CSV_URL = "/api/activity-log.csv"


def read_csv(response):
    text = response.get_data(as_text=True)
    assert text.startswith("﻿")
    return list(csv.reader(io.StringIO(text[1:])))


def download(app, query="", who="admin"):
    response = login(app, who).get(CSV_URL + query)
    assert response.status_code == 200, response.get_data(as_text=True)
    return response


# ---- csv: the file ---------------------------------------------------------------------------------------

def test_the_download_headers(busy_log):
    response = download(busy_log.app)
    assert response.mimetype == "text/csv"
    assert response.headers["Content-Disposition"] == 'attachment; filename="activity-log-2026-10-14.csv"'
    assert response.headers["Cache-Control"] == "no-store"


def test_the_columns_and_the_rows_oldest_first(busy_log):
    rows = read_csv(download(busy_log.app))
    assert rows[0] == ["seq", "logged_at", "admin", "action", "text", "person", "login", "old", "new", "row_hash"]
    assert [r[0] for r in rows[1:]] == ["1", "2", "3", "4"]
    renamed = rows[2]
    assert renamed[1:9] == ["2026-10-13T23:30:00+08:00", "boss", "person_renamed", "boss renamed Ben to Benjamin",
                            "Benjamin", "", "Ben", "Benjamin"]
    assert len(renamed[9]) == 64


def test_the_hash_column_is_the_stored_hash(busy_log):
    rows = read_csv(download(busy_log.app))
    with busy_log.app.app_context():
        stored = {r.seq: r.row_hash for r in activity_log.ActivityLog.query}
    assert {int(r[0]): r[9] for r in rows[1:]} == stored


def test_an_empty_log_is_just_the_header(world):
    assert len(read_csv(download(world.app))) == 1


def test_downloading_writes_nothing(busy_log):
    with busy_log.app.app_context():
        before = activity_log.anchor()
    download(busy_log.app, "?admin=boss")
    with busy_log.app.app_context():
        assert activity_log.anchor() == before


# ---- csv: filters --------------------------------------------------------------------------------------

@pytest.mark.parametrize("query,expected", [
    ("?admin=boss", ["2", "4"]),
    ("?person=zed", ["4"]),
    ("?action=person_renamed", ["2"]),
    ("?from=2026-10-14", ["3", "4"]),
    ("?to=2026-10-13", ["1", "2"]),
    ("?admin=admin&from=2026-10-13", ["3"]),
    ("?admin=nobody", []),
])
def test_the_csv_uses_the_same_filters_as_the_page(busy_log, query, expected):
    csv_rows = read_csv(download(busy_log.app, query))
    assert [r[0] for r in csv_rows[1:]] == expected
    assert sorted(log_seqs(fetch_log(busy_log.app, query))) == sorted(int(n) for n in expected)


def test_a_bad_date_is_a_400(busy_log):
    assert login(busy_log.app, "admin").get(CSV_URL + "?from=soon").status_code == 400


def test_the_csv_is_not_paged(world):
    from datetime import datetime, timedelta, timezone
    from extensions import db
    now = datetime(2026, 10, 14, 11, 0, tzinfo=timezone(timedelta(hours=8)))
    with world.app.app_context():
        for _ in range(520):
            activity_log.append_entry(None, "backup_restored", now)
        db.session.commit()
    assert len(read_csv(download(world.app))) == 521


# ---- csv: formulas and awkward text ---------------------------------------------------------------------

@pytest.mark.parametrize("name", ["=HYPERLINK(\"http://evil\",\"x\")", "+1+1", "-2+3", "@SUM(A1)"])
def test_a_name_that_looks_like_a_formula_is_neutralised(world, name):
    assert login(world.app, "admin").post("/api/table_persons", json={"name": name, "department": "Finance"}).status_code == 201
    rows = read_csv(download(world.app))
    assert rows[1][5] == "'" + name                 # the person column starts with a quote, so it is plain text


def test_commas_quotes_and_accents_survive(world):
    name = 'Josée "JJ", Smith'
    assert login(world.app, "admin").post("/api/table_persons", json={"name": name, "department": "Finance"}).status_code == 201
    rows = read_csv(download(world.app))
    assert rows[1][5] == name and name in rows[1][4]


@pytest.mark.parametrize("value,expected", [
    (None, ""), ("", ""), ("plain", "plain"), ("a=b", "a=b"), (7, 7),
    ("=1+1", "'=1+1"), ("+1", "'+1"), ("-1", "'-1"), ("@x", "'@x"), ("\tx", "'\tx"), ("\rx", "'\rx"),
])
def test_csv_safe(value, expected):
    assert activity_log.csv_safe(value) == expected