"""The head-count tally for today: counts only, never names, in the office timezone."""
import pytest

from conftest import login, make_people, make_record

TODAY = "2026-10-14"       # the clock is Wednesday 14 Oct, 11:00 local


def summary(client, department=None):
    url = "/api/attendance/summary" + (f"?department={department}" if department is not None else "")
    return client.get(url).get_json()


def test_empty_database_is_all_zeros(flask_app, add_users):
    add_users(("admin", True))
    assert summary(login(flask_app, "admin")) == {
        "date": TODAY, "department": None, "by_status": {"0": 0, "1": 0, "2": 0, "3": 0}, "not_recorded": 0, "total": 0,
    }


def test_counts_each_status_and_what_is_left(world):
    ana, ben, cai = (world.people[n] for n in ("Ana", "Ben", "Cai"))
    make_record(world.app, ana, TODAY, 1)
    make_record(world.app, ben, TODAY, 2)
    make_record(world.app, cai, TODAY, 3)
    result = summary(login(world.app, "alice"))
    assert result["by_status"] == {"0": 0, "1": 1, "2": 1, "3": 1}
    assert (result["not_recorded"], result["total"]) == (0, 3)


def test_only_today_is_counted(world):
    make_record(world.app, world.people["Ana"], "2026-10-13", 1)      # yesterday
    make_record(world.app, world.people["Ben"], TODAY, 0)
    result = summary(login(world.app, "alice"))
    assert result["by_status"] == {"0": 1, "1": 0, "2": 0, "3": 0}
    assert result["not_recorded"] == 2


def test_the_office_date_decides_what_today_is(world, set_clock):
    set_clock("2026-10-14T20:00:00")                                   # already Thursday 15th in UTC+8
    make_record(world.app, world.people["Ana"], "2026-10-15", 1)
    make_record(world.app, world.people["Ben"], "2026-10-14", 1)
    result = summary(login(world.app, "alice"))
    assert result["date"] == "2026-10-15" and result["by_status"]["1"] == 1


def test_department_filter_limits_both_the_counts_and_the_total(world):
    make_record(world.app, world.people["Ana"], TODAY, 1)             # Finance
    make_record(world.app, world.people["Cai"], TODAY, 0)             # Manpower
    finance = summary(login(world.app, "alice"), "Finance")
    assert finance["department"] == "Finance"
    assert finance["by_status"] == {"0": 0, "1": 1, "2": 0, "3": 0}
    assert (finance["not_recorded"], finance["total"]) == (1, 2)


def test_a_department_nobody_is_in_gives_zeros_not_an_error(world):
    result = summary(login(world.app, "alice"), "Intelligence")
    assert result["total"] == 0 and result["not_recorded"] == 0


def test_an_absurdly_long_department_is_refused(world):
    assert login(world.app, "alice").get("/api/attendance/summary?department=" + "x" * 81).status_code == 400


def test_blank_department_means_everyone(world):
    assert summary(login(world.app, "alice"), "")["total"] == 3


def test_the_summary_contains_no_names(world):
    make_record(world.app, world.people["Ana"], TODAY, 1)
    text = login(world.app, "alice").get("/api/attendance/summary").get_data(as_text=True)
    assert not any(name in text for name in ("Ana", "Ben", "Cai"))


def test_totals_add_up(world):
    for name, status in (("Ana", 1), ("Ben", 0), ("Cai", 2)):
        make_record(world.app, world.people[name], TODAY, status)
    result = summary(login(world.app, "alice"))
    assert sum(result["by_status"].values()) + result["not_recorded"] == result["total"] == 3
