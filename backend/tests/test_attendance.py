"""Admin attendance entry (PUT/GET /api/attendance), un-recording, and what it does to the prediction models."""
from datetime import date

import pytest

from conftest import login, make_record, recorded

TUESDAY = "2026-10-13"   # the clock is Wednesday 14 Oct, 11:00 local


def put(client, day=TUESDAY, records=None):
    return client.put("/api/attendance", json={"date": day, "records": records})


@pytest.fixture
def admin(world):
    return login(world.app, "admin")


def test_saves_new_records_and_reads_them_back(world, admin):
    ana, ben = world.people["Ana"], world.people["Ben"]
    response = put(admin, records=[{"person_id": ana, "status": 1}, {"person_id": ben, "status": 2}])
    assert response.get_json() == {"status": "success", "date": TUESDAY, "saved": 2, "removed": 0}
    assert recorded(world.app, TUESDAY) == {"Ana": 1, "Ben": 2}
    listed = admin.get(f"/api/attendance?date={TUESDAY}").get_json()
    assert sorted((r["person_id"], r["status"]) for r in listed["records"]) == sorted([(ana, 1), (ben, 2)])


def test_changes_an_existing_record_instead_of_adding_a_second(world, admin):
    ana = world.people["Ana"]
    put(admin, records=[{"person_id": ana, "status": 1}])
    result = put(admin, records=[{"person_id": ana, "status": 0}]).get_json()
    assert result["saved"] == 1
    assert recorded(world.app, TUESDAY) == {"Ana": 0}


def test_day_of_week_is_taken_from_the_date(world, admin):
    from users import AttendanceRecord

    put(admin, records=[{"person_id": world.people["Ana"], "status": 1}])
    with world.app.app_context():
        assert AttendanceRecord.query.one().day_of_week == date.fromisoformat(TUESDAY).isoweekday() == 2


def test_can_record_today_in_the_office_timezone(world, admin):
    assert put(admin, day="2026-10-14", records=[{"person_id": world.people["Ana"], "status": 1}]).status_code == 200


@pytest.mark.parametrize("day,why", [
    ("2026-10-15", "tomorrow"),
    ("2030-01-07", "far future"),
    ("2026-10-10", "Saturday"),
    ("2026-10-11", "Sunday"),
    ("13/10/2026", "wrong format"),
    ("", "empty"),
    (None, "missing"),
])
def test_rejects_bad_dates(world, admin, day, why):
    response = put(admin, day=day, records=[{"person_id": world.people["Ana"], "status": 1}])
    assert response.status_code == 400, why
    assert recorded(world.app, TUESDAY) == {}


def test_a_date_that_is_tomorrow_in_utc_but_today_in_the_office_is_allowed(world, set_clock):
    # Wed 14 Oct 20:00 UTC is already Thu 15 Oct 04:00 in UTC+8, so the 15th is "today".
    set_clock("2026-10-14T20:00:00")
    admin = login(world.app, "admin")
    assert put(admin, day="2026-10-15", records=[{"person_id": world.people["Ana"], "status": 1}]).status_code == 200
    assert put(admin, day="2026-10-16", records=[{"person_id": world.people["Ana"], "status": 1}]).status_code == 400


@pytest.mark.parametrize("records", [None, [], "Ana", {"person_id": 1, "status": 1}], ids=["none", "empty", "text", "object"])
def test_records_must_be_a_non_empty_list(admin, records):
    assert put(admin, records=records).status_code == 400


@pytest.mark.parametrize("entry", [
    {"status": 1},                       # no person_id
    {"person_id": "abc", "status": 1},   # not a number
    {"person_id": None, "status": 1},
], ids=["missing", "text", "null"])
def test_every_record_needs_a_person_id(admin, entry):
    assert put(admin, records=[entry]).status_code == 400


def test_rejects_duplicate_and_unknown_people(world, admin):
    ana = world.people["Ana"]
    assert put(admin, records=[{"person_id": ana, "status": 1}, {"person_id": ana, "status": 0}]).status_code == 400
    assert put(admin, records=[{"person_id": 99999, "status": 1}]).status_code == 400
    assert recorded(world.app, TUESDAY) == {}


def test_a_record_without_a_status_key_never_deletes(world, admin):
    ana = world.people["Ana"]
    put(admin, records=[{"person_id": ana, "status": 1}])
    assert put(admin, records=[{"person_id": ana}]).status_code == 400
    assert recorded(world.app, TUESDAY) == {"Ana": 1}


@pytest.mark.parametrize("status", [4, -1, 99, "1", "In", 1.0, 1.5, True, False, [1], {}],
                         ids=repr)
def test_rejects_statuses_that_are_not_in_the_status_table(world, admin, status):
    assert put(admin, records=[{"person_id": world.people["Ana"], "status": status}]).status_code == 400
    assert recorded(world.app, TUESDAY) == {}

@pytest.mark.parametrize("status", [0, 1, 2, 3])
def test_every_status_in_the_table_can_be_saved_and_read(world, admin, status):
    assert put(admin, records=[{"person_id": world.people["Ana"], "status": status}]).status_code == 200
    assert recorded(world.app, TUESDAY) == {"Ana": status}
    
def test_one_bad_record_means_nothing_is_saved(world, admin):
    ana, ben = world.people["Ana"], world.people["Ben"]
    response = put(admin, records=[{"person_id": ana, "status": 1}, {"person_id": ben, "status": 7}])
    assert response.status_code == 400
    assert recorded(world.app, TUESDAY) == {}


# ---- un-recording ---------------------------------------------------------------------------------

def test_null_removes_a_record_and_leaves_the_others(world, admin):
    ana, ben = world.people["Ana"], world.people["Ben"]
    put(admin, records=[{"person_id": ana, "status": 1}, {"person_id": ben, "status": 0}])
    result = put(admin, records=[{"person_id": ana, "status": None}]).get_json()
    assert (result["saved"], result["removed"]) == (0, 1)
    assert recorded(world.app, TUESDAY) == {"Ben": 0}


def test_removing_something_that_was_never_recorded_is_harmless(world, admin):
    result = put(admin, records=[{"person_id": world.people["Ana"], "status": None}])
    assert result.status_code == 200 and result.get_json()["removed"] == 0


def test_save_and_remove_in_one_request(world, admin):
    ana, ben, cai = (world.people[n] for n in ("Ana", "Ben", "Cai"))
    put(admin, records=[{"person_id": ana, "status": 1}, {"person_id": ben, "status": 1}])
    result = put(admin, records=[{"person_id": ana, "status": None}, {"person_id": ben, "status": 2},
                                 {"person_id": cai, "status": 0}]).get_json()
    assert (result["saved"], result["removed"]) == (2, 1)
    assert recorded(world.app, TUESDAY) == {"Ben": 2, "Cai": 0}


def test_removing_a_record_does_not_touch_other_days(world, admin):
    ana = world.people["Ana"]
    put(admin, day="2026-10-12", records=[{"person_id": ana, "status": 1}])
    put(admin, day=TUESDAY, records=[{"person_id": ana, "status": 1}])
    put(admin, day=TUESDAY, records=[{"person_id": ana, "status": None}])
    assert recorded(world.app, "2026-10-12") == {"Ana": 1}


def test_get_attendance_for_a_day_with_nothing_recorded(admin):
    assert admin.get(f"/api/attendance?date={TUESDAY}").get_json()["records"] == []


def test_get_attendance_rejects_a_bad_date(admin):
    assert admin.get("/api/attendance?date=yesterday").status_code == 400


# ---- the model cache -----------------------------------------------------------------------------

def weekly_history(world, name, status_by_weekday, weeks=3):
    """Records for `name` on the Mon-Fri of `weeks` weeks (backdated, so the cache outlasts them)."""
    from datetime import timedelta
    monday = date(2026, 9, 7)
    for week in range(weeks):
        for offset, status in enumerate(status_by_weekday):
            make_record(world.app, world.people[name], monday + timedelta(days=7 * week + offset), status)


def predicted_names(client, day="2030-01-07"):
    body = client.get(f"/api/predict?date={day}").get_json()
    return {p["person"]: p for p in body["predictions"]}


def test_removing_a_record_clears_the_model_cache_and_the_next_prediction_rebuilds_it(world, admin):
    import ml

    weekly_history(world, "Ana", [1, 1, 1, 1, 0])          # 15 records: plenty for a model
    assert "Ana" in predicted_names(admin)
    assert ml.MODELS_PATH.exists()

    admin.put("/api/attendance", json={"date": "2026-09-07",
                                       "records": [{"person_id": world.people["Ana"], "status": None}]})
    assert not ml.MODELS_PATH.exists()                     # thrown away at once...
    assert "Ana" in predicted_names(admin)                 # ...and rebuilt on demand (14 records left)
    assert ml.MODELS_PATH.exists()


def test_a_person_with_too_little_history_left_loses_their_prediction(world, admin):
    """Without invalidate_models() the stale cache would keep predicting for Ana (this is the bug it prevents)."""
    weekly_history(world, "Ana", [1, 1, 1, 1, 0], weeks=1)  # exactly 5 records: the minimum for a model
    assert "Ana" in predicted_names(admin)

    day = date(2026, 9, 7).isoformat()
    admin.put("/api/attendance", json={"date": day, "records": [{"person_id": world.people["Ana"], "status": None}]})
    assert "Ana" not in predicted_names(admin)             # 4 records left: no model any more


def test_saving_without_removing_does_not_clear_the_cache(world, admin):
    import ml

    weekly_history(world, "Ana", [1, 1, 1, 1, 0])
    predicted_names(admin)
    assert ml.MODELS_PATH.exists()
    put(admin, records=[{"person_id": world.people["Ben"], "status": 1}])
    assert ml.MODELS_PATH.exists()


def test_removing_when_no_cache_exists_is_fine(world, admin):
    import ml

    put(admin, records=[{"person_id": world.people["Ana"], "status": 1}])
    assert not ml.MODELS_PATH.exists()
    assert put(admin, records=[{"person_id": world.people["Ana"], "status": None}]).status_code == 200