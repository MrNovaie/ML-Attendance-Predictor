"""Settings from the environment: the CORS origin, the cookie flag, the production checks, the .env file and how
`python app.py` starts the server."""
import logging
import os

import pytest

PRODUCTION = {"ENV": "production", "SECRET_KEY": "s" * 32, "ACTIVITY_LOG_KEY": "k" * 32,
              "CORS_ORIGIN": "https://attendance.example.com"}
NAMES = ["ENV", "SECRET_KEY", "ACTIVITY_LOG_KEY", "CORS_ORIGIN", "COOKIE_SECURE", "DATABASE_URL", "HOST", "PORT"]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    """Every test starts with none of these set, and a database file of its own."""
    for name in NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'cfg.db').as_posix()}")


def set_env(monkeypatch, **values):
    for name, value in values.items():
        monkeypatch.setenv(name, value)


# ---- CORS_ORIGIN -----------------------------------------------------------------------------------

def test_the_default_origin_is_the_local_dev_server(appmod):
    assert appmod._cors_origins() == ["http://localhost:5173"]


def test_one_origin(appmod, monkeypatch):
    set_env(monkeypatch, CORS_ORIGIN="https://attendance.example.com")
    assert appmod._cors_origins() == ["https://attendance.example.com"]


def test_several_origins_with_spaces_and_a_stray_comma(appmod, monkeypatch):
    set_env(monkeypatch, CORS_ORIGIN=" http://10.0.0.5:5173 , https://attendance.example.com,")
    assert appmod._cors_origins() == ["http://10.0.0.5:5173", "https://attendance.example.com"]


@pytest.mark.parametrize("bad", ["*", "attendance.example.com", "https://attendance.example.com/",
                                 "https://attendance.example.com/app", "ftp://example.com", "https://*.example.com",
                                 "https://ok.example.com,*"])
def test_a_badly_written_origin_stops_the_server_with_a_clear_message(appmod, monkeypatch, bad):
    set_env(monkeypatch, CORS_ORIGIN=bad)
    with pytest.raises(RuntimeError, match="CORS_ORIGIN"):
        appmod.create_app()


def preflight(client, origin):
    return client.options("/api/login", headers={"Origin": origin, "Access-Control-Request-Method": "POST"})


def test_the_configured_origin_may_call_the_api_with_cookies_and_another_may_not(appmod, monkeypatch):
    set_env(monkeypatch, CORS_ORIGIN="https://attendance.example.com")
    client = appmod.create_app().test_client()
    allowed = preflight(client, "https://attendance.example.com")
    assert allowed.headers.get("Access-Control-Allow-Origin") == "https://attendance.example.com"
    assert allowed.headers.get("Access-Control-Allow-Credentials") == "true"
    assert preflight(client, "http://localhost:5173").headers.get("Access-Control-Allow-Origin") is None
    assert preflight(client, "https://evil.example.net").headers.get("Access-Control-Allow-Origin") is None


def test_without_the_setting_only_localhost_5173_is_allowed(appmod):
    client = appmod.create_app().test_client()
    assert preflight(client, "http://localhost:5173").headers.get("Access-Control-Allow-Origin") == "http://localhost:5173"
    assert preflight(client, "https://attendance.example.com").headers.get("Access-Control-Allow-Origin") is None


# ---- production refuses to start without its settings -----------------------------------------------

def test_production_with_everything_set_starts(appmod, monkeypatch):
    set_env(monkeypatch, **PRODUCTION)
    app = appmod.create_app()
    assert app.config["SESSION_COOKIE_SECURE"] is True


@pytest.mark.parametrize("missing", ["SECRET_KEY", "ACTIVITY_LOG_KEY", "CORS_ORIGIN"])
def test_production_refuses_to_start_without_each_required_setting(appmod, monkeypatch, missing):
    set_env(monkeypatch, **{k: v for k, v in PRODUCTION.items() if k != missing})
    with pytest.raises(RuntimeError, match=missing):
        appmod.create_app()


def test_development_starts_with_none_of_them_and_warns_about_the_dev_key(appmod, caplog):
    import activity_log

    activity_log._warned_dev_key = False
    with caplog.at_level(logging.WARNING):
        app = appmod.create_app()
    assert "insecure dev SECRET_KEY" in caplog.text and "insecure dev ACTIVITY_LOG_KEY" in caplog.text
    assert app.config["SESSION_COOKIE_SECURE"] is False


# ---- COOKIE_SECURE ----------------------------------------------------------------------------------

@pytest.mark.parametrize("env, setting, expected", [
    ({}, None, False),                                  # development: off
    ({"ENV": "production"}, None, True),                # production: on
    ({}, "1", True), ({}, "true", True), ({}, "YES", True),
    ({"ENV": "production"}, "0", False), ({"ENV": "production"}, "false", False), ({"ENV": "production"}, "no", False),
    ({"ENV": "production"}, "1", True),
])
def test_is_cookie_secure(appmod, monkeypatch, env, setting, expected):
    set_env(monkeypatch, **env)
    if setting is not None:
        monkeypatch.setenv("COOKIE_SECURE", setting)
    assert appmod._is_cookie_secure() is expected


def test_turning_the_secure_cookie_off_in_production_is_allowed_but_warned_about(appmod, monkeypatch, caplog):
    set_env(monkeypatch, ENV="production", COOKIE_SECURE="0")
    with caplog.at_level(logging.WARNING):
        assert appmod._is_cookie_secure() is False
    assert "not protected by HTTPS" in caplog.text


def test_a_meaningless_cookie_setting_stops_the_server(appmod, monkeypatch):
    set_env(monkeypatch, COOKIE_SECURE="maybe")
    with pytest.raises(RuntimeError, match="COOKIE_SECURE"):
        appmod.create_app()


# ---- DATABASE_URL, and how `python app.py` starts ------------------------------------------------------

def test_a_blank_database_url_means_the_default_file(appmod, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "   ")
    assert appmod._database_url().endswith("/attendance.db") and appmod._database_url().startswith("sqlite:///")


def test_a_database_url_is_used_as_given(appmod, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///elsewhere.db")
    assert appmod._database_url() == "sqlite:///elsewhere.db"


def test_the_server_defaults_to_this_computer_only_with_debug_on(appmod):
    assert appmod.run_settings() == {"host": "127.0.0.1", "port": 5000, "debug": True}


def test_host_and_port_can_be_set(appmod, monkeypatch):
    set_env(monkeypatch, HOST="0.0.0.0", PORT="8080")
    assert appmod.run_settings() == {"host": "0.0.0.0", "port": 8080, "debug": True}


def test_debug_is_never_on_in_production(appmod, monkeypatch):
    set_env(monkeypatch, ENV="production")
    assert appmod.run_settings()["debug"] is False


def test_blank_host_and_port_fall_back_to_the_defaults(appmod, monkeypatch):
    set_env(monkeypatch, HOST="", PORT="")
    assert appmod.run_settings() == {"host": "127.0.0.1", "port": 5000, "debug": True}


# ---- the .env file ----------------------------------------------------------------------------------

def test_env_file_values_are_loaded_but_a_real_environment_variable_wins(appmod, monkeypatch, tmp_path):
    monkeypatch.delenv("ATTENDANCE_NO_DOTENV")
    monkeypatch.setattr(os, "environ", dict(os.environ))          # so what the loader sets cannot leak into other tests
    env_file = tmp_path / ".env"
    env_file.write_text("CORS_ORIGIN=https://from-the-file.example.com\nSECRET_KEY=from-the-file\n", encoding="utf-8")
    os.environ["SECRET_KEY"] = "from-the-real-environment"
    assert appmod.load_env_file(env_file) is True
    assert os.environ["CORS_ORIGIN"] == "https://from-the-file.example.com"
    assert os.environ["SECRET_KEY"] == "from-the-real-environment"


def test_the_env_file_can_be_switched_off(appmod, monkeypatch, tmp_path):
    monkeypatch.setattr(os, "environ", dict(os.environ))
    env_file = tmp_path / ".env"
    env_file.write_text("CORS_ORIGIN=https://from-the-file.example.com\n", encoding="utf-8")
    os.environ["ATTENDANCE_NO_DOTENV"] = "1"
    assert appmod.load_env_file(env_file) is False
    assert "CORS_ORIGIN" not in os.environ


def test_a_missing_env_file_is_fine(appmod, monkeypatch, tmp_path):
    monkeypatch.delenv("ATTENDANCE_NO_DOTENV")
    assert appmod.load_env_file(tmp_path / "no-such.env") is False


def test_the_env_file_is_found_next_to_app_py_not_in_the_current_folder(appmod, monkeypatch, tmp_path):
    backend, elsewhere = tmp_path / "backend", tmp_path / "elsewhere"
    backend.mkdir(), elsewhere.mkdir()
    (backend / ".env").write_text("CORS_ORIGIN=https://next-to-app.example.com\n", encoding="utf-8")
    (elsewhere / ".env").write_text("CORS_ORIGIN=https://wrong-folder.example.com\n", encoding="utf-8")
    monkeypatch.delenv("ATTENDANCE_NO_DOTENV")
    monkeypatch.setattr(os, "environ", dict(os.environ))
    monkeypatch.setattr(appmod, "BASE_DIR", backend)
    monkeypatch.chdir(elsewhere)                                    # e.g. `flask --app backend/app` run from the repo root
    assert appmod.load_env_file() is True
    assert os.environ["CORS_ORIGIN"] == "https://next-to-app.example.com"
