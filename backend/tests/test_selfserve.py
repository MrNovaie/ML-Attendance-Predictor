"""A user setting their own status for today, until the cutoff (08:15 UTC+8), and the office clock behind it.

Times below are given in UTC. The office is UTC+8, so 00:14:59 UTC is 08:14:59 local.
"""
from datetime import date, datetime, timedelta, timezone

import pytest

from conftest import link, login, recorded

WED_0800 = "2026-10-14T00:00:00"      # Wed 08:00 local: open
WED_0814 = "2026-10-14T00:14:59"      # one second before the cutoff
WED_0815 = "2026-10-14T00:15:00"      # the cutoff itself: closed
WED_0900 = "2026-10-14T01:00:00"
THU_0700 = "2026-10-14T23:00:00"      # Thu 07:00 local, though it is still Wednesday in UTC
SAT_0800 = "2026-10-17T00:00:00"      # Saturday


def mine(client):
    return client.get("/api/me/attendance").get_json()


def set_mine(client, status):
    return client.put("/api/me/attendance", json={"status": status})


# ---- the window ----------------------------------------------------------------------------------

@pytest.mark.parametrize("when,is_open,seconds_left", [
    (WED_0800, True, 15 * 60),
    (WED_0814, True, 1),
    (WED_0815, False, 0),
    (WED_0900, False, 0),
    ("2026-10-13T16:00:00", True, 8 * 3600 + 15 * 60),   # Wed 00:00 local: the new day has begun
    (THU_0700, True, 75 * 60),
    (SAT_0800, False, 0),
    ("2026-10-18T00:00:00", False, 0),                    # Sunday
], ids=["0800", "0814:59", "0815", "0900", "midnight", "thursday-0700", "saturday", "sunday"])
def test_window_state(world, set_clock, when, is_open, seconds_left):
    set_clock(when)
    state = mine(login(world.app, "alice"))
    assert (state["window_open"], state["seconds_left"]) == (is_open, seconds_left)
    assert state["can_edit"] is is_open


def test_self_service_window_function_directly(appmod):
    tz = timezone(timedelta(hours=8))
    assert appmod.self_service_window(datetime(2026, 10, 14, 8, 14, 59, tzinfo=tz)) == (True, 1)
    assert appmod.self_service_window(datetime(2026, 10, 14, 8, 15, tzinfo=tz)) == (False, 0)
    assert appmod.self_service_window(datetime(2026, 10, 14, 8, 14, 59, 500000, tzinfo=tz)) == (True, 1)  # rounds up, never 0 while open
    assert appmod.self_service_window(datetime(2026, 10, 17, 7, 0, tzinfo=tz)) == (False, 0)


def test_cutoff_is_reported(world, set_clock):
    set_clock(WED_0800)
    state = mine(login(world.app, "alice"))
    assert state["cutoff"] == "08:15" and state["timezone"] == "UTC+8"


# ---- writing -------------------------------------------------------------------------------------

def test_a_linked_user_can_set_their_own_status_before_the_cutoff(world, set_clock):
    set_clock(WED_0814)
    client = login(world.app, "alice")
    response = set_mine(client, 1)
    assert response.status_code == 200 and response.get_json()["attendance"] == 1
    assert recorded(world.app, date(2026, 10, 14)) == {"Ana": 1}
    assert mine(client)["status"] == 1


def test_they_can_change_their_mind_until_the_cutoff(world, set_clock):
    set_clock(WED_0800)
    client = login(world.app, "alice")
    for status in (1, 2, 0):
        assert set_mine(client, status).status_code == 200
    assert recorded(world.app, date(2026, 10, 14)) == {"Ana": 0}


def test_at_the_cutoff_the_write_is_refused_and_nothing_changes(world, set_clock):
    client = login(world.app, "alice")
    set_clock(WED_0814)
    set_mine(client, 1)
    set_clock(WED_0815)
    response = set_mine(client, 0)
    assert response.status_code == 403 and "08:15" in response.get_json()["error"]
    assert recorded(world.app, date(2026, 10, 14)) == {"Ana": 1}


def test_the_window_reopens_by_itself_the_next_morning(world, set_clock):
    client = login(world.app, "alice")
    set_clock(WED_0900)
    assert set_mine(client, 1).status_code == 403
    set_clock(THU_0700)
    assert set_mine(client, 1).status_code == 200
    assert recorded(world.app, date(2026, 10, 15)) == {"Ana": 1}     # Thursday's date, not Wednesday's
    assert recorded(world.app, date(2026, 10, 14)) == {}


def test_the_date_is_the_office_date_not_the_utc_date(world, set_clock):
    set_clock(THU_0700)                                              # Wednesday in UTC, Thursday in the office
    client = login(world.app, "alice")
    assert mine(client)["date"] == "2026-10-15"
    assert client.get("/api/today").get_json()["date"] == "2026-10-15"


def test_weekend_is_refused(world, set_clock):
    set_clock(SAT_0800)
    client = login(world.app, "alice")
    assert set_mine(client, 1).status_code == 400
    assert mine(client)["weekend"] is True
    assert client.get("/api/today").get_json()["weekend"] is True


def test_a_weekend_in_the_office_that_is_still_friday_in_utc(world, set_clock):
    set_clock("2026-10-16T17:00:00")                                 # Fri 17:00 UTC = Sat 01:00 local
    assert login(world.app, "alice").get("/api/today").get_json()["weekend"] is True


@pytest.mark.parametrize("status", [4, -1, "1", None, 1.0, True, False, [1]], ids=repr)
def test_invalid_status_is_refused_even_when_everything_else_is_fine(world, set_clock, status):
    set_clock(WED_0800)
    assert set_mine(login(world.app, "alice"), status).status_code == 400
    assert recorded(world.app, date(2026, 10, 14)) == {}


def test_missing_body_is_refused(world, set_clock):
    set_clock(WED_0800)
    assert login(world.app, "alice").put("/api/me/attendance").status_code == 400


# ---- who it applies to ---------------------------------------------------------------------------

def test_an_unlinked_user_cannot_set_anything(world, set_clock):
    set_clock(WED_0800)
    client = login(world.app, "bob")
    state = mine(client)
    assert state["linked"] is False and state["person"] is None and state["can_edit"] is False
    response = set_mine(client, 1)
    assert response.status_code == 403 and "linked" in response.get_json()["error"]
    assert recorded(world.app, date(2026, 10, 14)) == {}


def test_the_person_comes_from_the_link_never_from_the_request(world, set_clock):
    set_clock(WED_0800)
    client = login(world.app, "alice")
    client.put("/api/me/attendance", json={"status": 1, "person_id": world.people["Ben"], "person": "Ben"})
    assert recorded(world.app, date(2026, 10, 14)) == {"Ana": 1}


def test_two_users_do_not_overwrite_each_other(world, set_clock):
    set_clock(WED_0800)
    link(world.app, world.users["bob"], world.people["Ben"])
    set_mine(login(world.app, "alice"), 1)
    set_mine(login(world.app, "bob"), 0)
    assert recorded(world.app, date(2026, 10, 14)) == {"Ana": 1, "Ben": 0}


def test_the_status_report_names_the_linked_person(world, set_clock):
    set_clock(WED_0800)
    state = mine(login(world.app, "alice"))
    assert state["linked"] is True
    assert state["person"] == {"id": world.people["Ana"], "name": "Ana", "department": "Finance"}
    assert state["status"] is None


def test_an_admins_own_login_is_treated_like_any_other(world, set_clock):
    set_clock(WED_0900)                                              # after the cutoff, and admin is not linked
    assert set_mine(login(world.app, "admin"), 1).status_code == 403


# ---- admins and users together -------------------------------------------------------------------

def test_a_user_can_overwrite_an_entry_an_admin_made_for_them_before_the_cutoff(world, set_clock):
    set_clock(WED_0800)
    admin = login(world.app, "admin")
    admin.put("/api/attendance", json={"date": "2026-10-14", "records": [{"person_id": world.people["Ana"], "status": 0}]})
    assert set_mine(login(world.app, "alice"), 1).status_code == 200
    assert recorded(world.app, date(2026, 10, 14)) == {"Ana": 1}


def test_after_the_cutoff_only_an_admin_can_change_it(world, set_clock):
    set_clock(WED_0800)
    set_mine(login(world.app, "alice"), 1)
    set_clock(WED_0900)
    assert set_mine(login(world.app, "alice"), 0).status_code == 403
    admin = login(world.app, "admin")
    admin.put("/api/attendance", json={"date": "2026-10-14", "records": [{"person_id": world.people["Ana"], "status": 0}]})
    assert recorded(world.app, date(2026, 10, 14)) == {"Ana": 0}


def test_a_user_cannot_use_the_admin_endpoint_to_edit_themselves(world, set_clock):
    set_clock(WED_0800)
    response = login(world.app, "alice").put("/api/attendance", json={
        "date": "2026-10-14", "records": [{"person_id": world.people["Ana"], "status": 1}]})
    assert response.status_code == 403
    assert recorded(world.app, date(2026, 10, 14)) == {}