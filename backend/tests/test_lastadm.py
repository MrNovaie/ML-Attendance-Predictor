"""Race condition check: the last admin can never be deleted, even when two admins try to delete each other at the same moment."""
import sqlite3

from conftest import login


def count(app, model, **where):
    with app.app_context():
        return model.query.filter_by(**where).count()


def delete_behind_the_requests_back(app, user_id):
    """Another request deleting this login on its own connection, and committing, before ours gets to run its DELETE."""
    path = app.config["SQLALCHEMY_DATABASE_URI"].replace("sqlite:///", "")
    other = sqlite3.connect(path)
    other.execute('DELETE FROM "user" WHERE id = ?', (user_id,))
    other.commit()
    other.close()


def test_an_admin_can_delete_another_admin_while_one_remains(world, add_users):
    from users import User

    second = add_users(("boss", True))["boss"]
    response = login(world.app, "admin").delete(f"/api/users/{second}")
    assert response.status_code == 200
    assert count(world.app, User, is_admin=True) == 1


def test_deleting_an_ordinary_login_is_fine_with_a_single_admin(world):
    from users import User

    assert login(world.app, "admin").delete(f"/api/users/{world.users['bob']}").status_code == 200
    assert count(world.app, User, is_admin=True) == 1


def test_two_admins_deleting_each_other_cannot_leave_zero(world, add_users, appmod, monkeypatch):
    """Admin deletes boss. While that request is running, boss's own request deletes admin and commits first.
    Our count then finds nobody, so we must roll back and keep boss."""
    from users import User

    boss = add_users(("boss", True))["boss"]
    client = login(world.app, "admin")

    real_person_of = appmod.person_of

    def person_of_then_the_other_delete_lands(user):
        result = real_person_of(user)
        delete_behind_the_requests_back(world.app, world.users["admin"])
        return result

    monkeypatch.setattr(appmod, "person_of", person_of_then_the_other_delete_lands)
    response = client.delete(f"/api/users/{boss}")

    assert response.status_code == 409
    assert response.get_json()["code"] == 409
    with world.app.app_context():
        assert User.query.filter_by(username="boss").count() == 1           # kept
        assert User.query.filter_by(is_admin=True).count() == 1             # the one admin left


def test_a_refused_delete_writes_no_log_row(world, add_users, appmod, monkeypatch):
    import activity_log

    boss = add_users(("boss", True))["boss"]
    client = login(world.app, "admin")
    with world.app.app_context():
        before = activity_log.anchor()

    real_person_of = appmod.person_of
    monkeypatch.setattr(appmod, "person_of", lambda user: (delete_behind_the_requests_back(world.app, world.users["admin"]),
                                                          real_person_of(user))[1])
    assert client.delete(f"/api/users/{boss}").status_code == 409
    with world.app.app_context():
        assert activity_log.anchor() == before
        assert activity_log.verify_chain() is None


def test_the_delete_runs_before_the_admin_count(world, add_users):
    """The whole point of the fix: the DELETE has to run first so this request holds SQLite's write lock, and the count
    that decides 'is anyone left?' is taken while holding it. A count taken first can be out of date."""
    from sqlalchemy import event
    from extensions import db

    boss = add_users(("boss", True))["boss"]
    client = login(world.app, "admin")
    seen = []

    with world.app.app_context():
        engine = db.engine

    def spy(conn, cursor, statement, *rest):
        seen.append(statement)

    event.listen(engine, "before_cursor_execute", spy)
    try:
        assert client.delete(f"/api/users/{boss}").status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", spy)

    delete_at = next(i for i, s in enumerate(seen) if s.lstrip().upper().startswith("DELETE FROM USER"))
    count_at = next(i for i, s in enumerate(seen) if "count(" in s.lower() and "is_admin" in s.lower())
    assert delete_at < count_at