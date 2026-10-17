"""Predictions and evaluation, as the API serves them."""
from datetime import date, timedelta

import pytest

from conftest import login, make_record

MONDAY, FRIDAY, SATURDAY = "2030-01-07", "2030-01-11", "2030-01-12"     # far enough ahead to always be "future"


def history(world, name, by_weekday, weeks):
    """`weeks` weeks of Mon-Fri records for `name`, with status by_weekday[0..4]."""
    start = date(2026, 8, 31)
    for week in range(weeks):
        for offset, status in enumerate(by_weekday):
            make_record(world.app, world.people[name], start + timedelta(days=7 * week + offset), status)


def weekdays(count):
    """The first `count` weekdays from Monday 31 Aug 2026."""
    day, found = date(2026, 8, 31), []
    while len(found) < count:
        if day.weekday() < 5:
            found.append(day)
        day += timedelta(days=1)
    return found


def predict(client, day):
    return client.get(f"/api/predict?date={day}")


@pytest.fixture
def user(world):
    return login(world.app, "admin")


def test_no_data_means_no_predictions(user):
    body = predict(user, MONDAY).get_json()
    assert body["status"] == "success" and body["predictions"] == []


def test_predicts_each_weekdays_usual_status(world, user):
    history(world, "Ana", [1, 1, 1, 1, 0], weeks=3)               # In Monday to Thursday, Out on Fridays
    monday = predict(user, MONDAY).get_json()["predictions"][0]
    friday = predict(user, FRIDAY).get_json()["predictions"][0]
    assert (monday["person"], monday["verdict"], monday["sample_size"]) == ("Ana", "In", 3)
    assert friday["verdict"] == "Out"
    assert 0 < monday["p_winner"] <= 100


def test_reports_the_requested_date_and_a_message(world, user):
    history(world, "Ana", [1, 1, 1, 1, 0], weeks=3)
    body = predict(user, MONDAY).get_json()
    assert body["requested_date"] == MONDAY and "Jan 07, 2030" in body["message"]


def test_people_without_enough_history_are_left_out(world, user):
    history(world, "Ana", [1, 1, 1, 1, 0], weeks=3)               # 15 records
    for day in weekdays(4):                                         # Ben: only 4 records
        make_record(world.app, world.people["Ben"], day, 1)
    assert [p["person"] for p in predict(user, MONDAY).get_json()["predictions"]] == ["Ana"]


def test_weekends_get_an_explanation_and_no_predictions(world, user):
    history(world, "Ana", [1, 1, 1, 1, 0], weeks=3)
    body = predict(user, SATURDAY).get_json()
    assert body["predictions"] == [] and "weekend" in body["message"].lower()


@pytest.mark.parametrize("day", ["2000-01-03", "01/07/2030", "tomorrow"], ids=["past", "wrong-format", "text"])
def test_bad_or_past_dates_are_refused(user, day):
    assert predict(user, day).status_code == 400


def test_a_newer_record_is_picked_up_and_an_old_cache_is_not_trusted_blindly(world, user):
    """Ana is usually In on Mondays. Six 'Out' Mondays are then added, which should flip the prediction."""
    import os
    import time
    from datetime import datetime, timezone

    import ml

    out_mondays = [date(2026, 9, 21) + timedelta(days=7 * n) for n in range(6)]
    verdict = lambda: predict(user, MONDAY).get_json()["predictions"][0]["verdict"]

    history(world, "Ana", [1, 1, 1, 1, 0], weeks=3)
    assert verdict() == "In"

    # Records older than the cache change nothing: the cached models are served (the fast path)...
    for day in out_mondays:
        make_record(world.app, world.people["Ana"], day, 0)
    assert verdict() == "In"

    # ...but once the cache is older than the newest row, the models are retrained.
    long_ago = time.time() - 3600
    os.utime(ml.MODELS_PATH, (long_ago, long_ago))
    make_record(world.app, world.people["Ana"], date(2026, 10, 12) + timedelta(days=28), 0,
                updated_at=datetime.now(timezone.utc).replace(tzinfo=None))
    assert verdict() == "Out"


# ---- evaluation ----------------------------------------------------------------------------------

def test_evaluation_needs_ten_records(world, user):
    for day in weekdays(9):
        make_record(world.app, world.people["Ana"], day, 1)
    assert user.get("/api/evaluate").get_json()["evaluations"] == []
    make_record(world.app, world.people["Ana"], weekdays(10)[-1], 1)
    assert len(user.get("/api/evaluate").get_json()["evaluations"]) == 1


def test_evaluation_reports_accuracy_next_to_the_baseline(world, user):
    history(world, "Ana", [1, 1, 1, 1, 0], weeks=3)               # 15 records
    body = user.get("/api/evaluate").get_json()
    ana = body["evaluations"][0]
    assert ana["person"] == "Ana" and ana["total_samples"] == 15 and ana["n_samples"] == 3
    assert 0 <= ana["accuracy_pct"] <= 100 and 0 <= ana["baseline_pct"] <= 100
    assert body["total_samples"] == 15