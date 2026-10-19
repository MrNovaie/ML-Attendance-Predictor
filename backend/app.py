import hmac
import logging
import os
import click
import math
import re
import secrets
import unicodedata
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from flask import Flask, Response, jsonify, request
from flask_cors import CORS
from flask_login import login_user, login_required, logout_user, current_user
from functools import wraps
from extensions import db, login_manager
import activity_log   # also registers the activity_log table, so db.create_all() creates it
import backup
from import_csv import import_csv
from ml import input_to_date, get_name_list, load_data, predict_all_for_date, evaluate_all, invalidate_models

from pathlib import Path
from sqlalchemy.exc import IntegrityError
# Importing users registers User on db's metadata so db.create_all() picks
# it up. Don't drop this import even though `User` is the only name we use.
from users import User, Person, AttendanceRecord, DEFAULT_DEPARTMENT, DEPARTMENTS
from werkzeug.security import check_password_hash, generate_password_hash


logger = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parent

# The office runs on UTC+8. "Today" and the self-service cutoff are judged in this timezone and not
# in the server's, so they stay right when the server clock is UTC.
TIMEZONE_OFFSET_HOURS = 8
TIMEZONE_LABEL = f"UTC{TIMEZONE_OFFSET_HOURS:+d}"
LOCAL_TZ = timezone(timedelta(hours=TIMEZONE_OFFSET_HOURS))
# Logged-in users may change their OWN attendance for today until this local time (HH:MM, 24h).
SELF_SERVICE_CUTOFF = "08:15"
_CUTOFF_HOUR, _CUTOFF_MINUTE = (int(part) for part in SELF_SERVICE_CUTOFF.split(":"))


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def now_local() -> datetime:
    return utc_now().astimezone(LOCAL_TZ)


def local_today() -> date:
    return now_local().date()


def self_service_window(now: datetime) -> tuple[bool, int]:
    """(is_open, seconds_left) for users editing their own attendance at local time `now`.

    Open on weekdays until the cutoff. It resets by itself each day because it only ever
    looks at the current local date and time.
    """
    if now.weekday() > 4:
        return False, 0
    cutoff = now.replace(hour=_CUTOFF_HOUR, minute=_CUTOFF_MINUTE, second=0, microsecond=0)
    if now >= cutoff:
        return False, 0
    return True, math.ceil((cutoff - now).total_seconds())


PASSWORD_MIN, PASSWORD_MAX = 8, 128
# Temporary passwords skip look-alike characters (0/O, 1/l/I), because an admin reads them out or prints them.
TEMPORARY_PASSWORD_ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789"
TEMPORARY_PASSWORD_LENGTH = 12
BULK_LOGIN_LIMIT = 200


def password_problem(password) -> str | None:
    """Why `password` is not acceptable, or None when it is."""
    if not isinstance(password, str):
        return "Password must be text"
    if len(password) < PASSWORD_MIN:
        return f"Password must be at least {PASSWORD_MIN} characters"
    if len(password) > PASSWORD_MAX:
        return f"Password must be at most {PASSWORD_MAX} characters"
    return None


def make_temporary_password() -> str:
    return "".join(secrets.choice(TEMPORARY_PASSWORD_ALPHABET) for _ in range(TEMPORARY_PASSWORD_LENGTH))


def username_from_name(name: str, taken: set) -> str:
    """'José Luis' -> 'jose.luis'; a number is added when that is taken ('jose.luis2'). `taken` holds lower-case names
    and is updated, so a batch never hands out the same username twice."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii").lower()
    base = re.sub(r"[^a-z0-9]+", ".", ascii_name).strip(".")[:70] or "user"
    candidate, number = base, 1
    while candidate in taken:
        number += 1
        candidate = f"{base}{number}"
    taken.add(candidate)
    return candidate


def department_of(person) -> str:
    return (person.department or "").strip() or DEFAULT_DEPARTMENT


def person_of(user):
    """The person a login is linked to, or None."""
    return db.session.get(Person, user.person_id) if user.person_id else None


def log_admin(action, *, person=None, login=None, old=None, new=None) -> None:
    """Write one row to the activity log for the logged-in admin, inside the current transaction.
    The row is saved together with the change, or thrown away with it. 
    Never pass a password or a password hash as old or new.
    """
    activity_log.append_entry(current_user, action, now_local(), person=person, login=login, old=old, new=new)


def log_system(action, *, person=None, login=None, old=None, new=None) -> None:
    """For the command line, where there is no logged-in admin: the row says the system did it."""
    activity_log.append_entry(None, action, now_local(), person=person, login=login, old=old, new=new)


def ensure_attendance_schema() -> None:
    """Recreate person/attendance_record if the DB predates the schema.

    db.create_all() never alters existing tables. An older attendance_record table
    (e.g. from pandas to_sql or a previous model) may exist without person_id.
    """
    from sqlalchemy import inspect

    inspector = inspect(db.engine)
    if not inspector.has_table("attendance_record"):
        return
    cols = {c["name"] for c in inspector.get_columns("attendance_record")}
    required = {"person_id", "day_of_week", "updated_at"}
    if required <= cols:
        return
    logger.warning(
        "attendance_record is missing person_id, day_of_week, or updated_at (stale schema). "
        "Recreating person and attendance_record tables."
    )
    AttendanceRecord.__table__.drop(db.engine, checkfirst=True)
    Person.__table__.drop(db.engine, checkfirst=True)
    db.create_all()


def ensure_person_department() -> None:
    """Make sure person.department exists and nobody is left without one.

    db.create_all() never alters existing tables. A missing column is added as NOT NULL
    with the default department, and blank or NULL values left by an older column are
    filled with that default. Existing rows are kept.

    """
    from sqlalchemy import inspect, text

    inspector = inspect(db.engine)
    if not inspector.has_table("person"):
        return
    has_column = "department" in {c["name"] for c in inspector.get_columns("person")}
    default_literal = DEFAULT_DEPARTMENT.replace("'", "''")
    with db.engine.begin() as conn:
        if not has_column:
            logger.warning("person.department is missing. Adding the column.")
            conn.execute(text(
                f"ALTER TABLE person ADD COLUMN department VARCHAR(80) NOT NULL DEFAULT '{default_literal}'"
            ))
        conn.execute(
            text("UPDATE person SET department = :d WHERE department IS NULL OR TRIM(department) = ''"),
            {"d": DEFAULT_DEPARTMENT},
        )


def ensure_person_former() -> None:
    """Add person.former to a database created before it existed. Everyone already there stays active."""
    from sqlalchemy import inspect, text

    inspector = inspect(db.engine)
    if not inspector.has_table("person"):
        return
    if "former" in {c["name"] for c in inspector.get_columns("person")}:
        return
    logger.warning("person.former is missing. Adding the column.")
    with db.engine.begin() as conn:
        conn.execute(text("ALTER TABLE person ADD COLUMN former BOOLEAN NOT NULL DEFAULT 0"))


def ensure_user_person_link() -> None:
    """Add user.person_id (which person a login belongs to) to a database created before it existed.

    db.create_all() never alters existing tables. The column is nullable, so existing logins stay
    unlinked until an admin links them. SQLite cannot add a UNIQUE column in place, so the
    one-login-per-person rule is added as a unique index.
    """
    from sqlalchemy import inspect, text

    inspector = inspect(db.engine)
    table = User.__table__.name
    if not inspector.has_table(table):
        return
    if "person_id" in {c["name"] for c in inspector.get_columns(table)}:
        return
    logger.warning("%s.person_id is missing. Adding the column.", table)
    quoted = db.engine.dialect.identifier_preparer.quote(table)
    with db.engine.begin() as conn:
        conn.execute(text(f"ALTER TABLE {quoted} ADD COLUMN person_id INTEGER REFERENCES person (id)"))
        conn.execute(text(f"CREATE UNIQUE INDEX uq_user_person_id ON {quoted} (person_id)"))


def ensure_user_account_columns() -> None:
    """Add user.must_change_password and user.requested_department to a database created before they existed.

    db.create_all() never alters existing tables. Existing logins keep working: nobody is forced to change a
    password they already have, and nobody has a pending department request.
    """
    from sqlalchemy import inspect, text

    inspector = inspect(db.engine)
    table = User.__table__.name
    if not inspector.has_table(table):
        return
    existing = {c["name"] for c in inspector.get_columns(table)}
    quoted = db.engine.dialect.identifier_preparer.quote(table)
    with db.engine.begin() as conn:
        if "must_change_password" not in existing:
            logger.warning("%s.must_change_password is missing. Adding the column.", table)
            conn.execute(text(f"ALTER TABLE {quoted} ADD COLUMN must_change_password BOOLEAN NOT NULL DEFAULT 0"))
        if "requested_department" not in existing:
            logger.warning("%s.requested_department is missing. Adding the column.", table)
            conn.execute(text(f"ALTER TABLE {quoted} ADD COLUMN requested_department VARCHAR(80)"))


def _resolve_secret_key() -> str:
    """Refuse to boot in production without an explicit SECRET_KEY.

    Local dev (no ENV set, or ENV=development) gets a fixed insecure default
    so `python app.py` keeps working out of the box. Set ENV=production to
    opt in to the strict check.
    """
    secret = os.environ.get("SECRET_KEY")
    if secret:
        return secret
    if os.environ.get("ENV") == "production":
        raise RuntimeError("SECRET_KEY must be set when ENV=production")
    logger.warning("Using insecure dev SECRET_KEY. Set SECRET_KEY in production.")
    return "dev-only-change-me"

def admin_required(fn):
    """Use *below* @login_required so current_user is authenticated."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user.is_admin:
            return jsonify(code=403, error="Admin privileges required"), 403
        return fn(*args, **kwargs)
    return wrapper

def clean_department(value):
    """Return (department, error). Blank or missing means the default department."""
    if value is None:
        return DEFAULT_DEPARTMENT, None
    if not isinstance(value, str):
        return None, "Department must be text."
    value = value.strip() or DEFAULT_DEPARTMENT
    allowed = [DEFAULT_DEPARTMENT, *DEPARTMENTS]
    if value not in allowed:
        return None, f"Department must be one of: {', '.join(allowed)}."
    return value, None

def create_app() -> Flask:
    app = Flask(__name__)
    # SQLAlchemy reads SQLALCHEMY_DATABASE_URI at init_app() time, so config
    # MUST be set before db.init_app(app) below.
    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
    "DATABASE_URL", f"sqlite:///{(BASE_DIR / 'attendance.db').as_posix()}"
    )
    app.config["SECRET_KEY"] = _resolve_secret_key()
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    # Open sign-up is OFF unless ALLOW_REGISTRATION is set to 1/true/yes. With it off, only admins create logins.
    app.config["ALLOW_REGISTRATION"] = os.environ.get("ALLOW_REGISTRATION", "").strip().lower() in ("1", "true", "yes")
    # Session cookie hardening. SAMESITE="Lax" still lets the SPA call us from
    # the same registrable domain; switch to "None" + SECURE=True only if the
    # frontend lives on a separate site and is served over HTTPS.
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = os.environ.get("ENV") == "production"

    # supports_credentials=True is required for the browser to send the
    # Flask-Login session cookie on cross-origin fetches; the frontend must
    # also set fetch(..., { credentials: "include" }). Origin must be exact
    # (no "*") when credentials are enabled.
    CORS(app, supports_credentials=True, origins=["http://localhost:5173"],
         expose_headers=["Content-Disposition"])  # lets the frontend read the download's file name
    db.init_app(app)
    login_manager.init_app(app)
    @login_manager.unauthorized_handler
    def unauthorized():
        return jsonify(code=401, error="Unauthorized. Please login to continue."), 401
    # A login that must change its password (new login, or reset by an admin) may only change it, ask who it is,
    # or log out. The server enforces this, so the screen is not the only thing standing in the way.
    PASSWORD_CHANGE_ENDPOINTS = {"me", "logout", "change_my_password", "login"}

    @app.before_request
    def require_password_change():
        if request.method == "OPTIONS" or not request.path.startswith("/api/"):
            return None
        if request.endpoint in PASSWORD_CHANGE_ENDPOINTS:
            return None
        if current_user.is_authenticated and current_user.must_change_password:
            return jsonify(code=403, password_change_required=True,
                           error="You must change your password before you can continue."), 403
        return None

    '''Endpoint Section'''

    @app.get("/api/departments")
    @login_required
    def departments():
        return jsonify(departments=[DEFAULT_DEPARTMENT, *DEPARTMENTS], default=DEFAULT_DEPARTMENT)

    @app.route("/api/login", methods=["POST"])
    def login():
        body = request.get_json(silent=True) or {}
        username = body.get("username")
        password = body.get("password")
        if not username or not password:
            return jsonify(code=400, error="Username and password are required"), 400
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
            try:
                login_user(user)
                return jsonify(code=200, status="success", message="Logged in successfully")
            except Exception:
                logger.exception("Failed to login user: %s", username)
                return jsonify(code=500, error="Internal server error"), 500
        else:
            return jsonify(code=401, error="Invalid username or password"), 401
    @app.route("/api/logout", methods=["POST"])
    @login_required
    def logout():
        try:
            logout_user()
            return jsonify(code=200, status="success", message="Logged out successfully")
        except Exception:
            logger.exception("Failed to logout user: %s", current_user.username)
            return jsonify(code=500, error="Internal server error"), 500
        
    
    @login_manager.user_loader
    def load_user(user_id: str) -> User | None:
        # Only ValueError/TypeError are expected here (bad cookie payload).
        # DB errors should bubble up so we notice them.
        # The id in the cookie is "<id>:<fingerprint of the password>", so a password change or reset ends
        # every session that was started with the old password.
        try:
            id_part, _, fingerprint = user_id.partition(":")
            user = db.session.get(User, int(id_part))
        except (ValueError, TypeError):
            return None
        if user is None or not hmac.compare_digest(fingerprint, user.session_fingerprint()):
            return None
        return user

    @app.get("/api/auth/options")
    def auth_options():
        """What the login screen needs before anyone is logged in: is sign-up open, and which departments to offer."""
        is_open = bool(app.config["ALLOW_REGISTRATION"])
        return jsonify(registration_open=is_open,
                       departments=[DEFAULT_DEPARTMENT, *DEPARTMENTS] if is_open else [],
                       default_department=DEFAULT_DEPARTMENT)

    @app.route("/api/register", methods=["POST"])
    def register():
        if not app.config["ALLOW_REGISTRATION"]:
            return jsonify(code=403, error="Sign-up is closed. Ask an admin to create your account."), 403
        body = request.get_json(silent=True) or {}
        username = body.get("username")
        password = body.get("password")
        confirmPassword = body.get("confirmPassword")
        if not isinstance(username, str) or not username.strip() or not password or not confirmPassword:
            return jsonify(code=400, error="Username and password are required"), 400
        username = username.strip()
        if len(username) > 80:
            return jsonify(code=400, error="Username must be at most 80 characters"), 400
        problem = password_problem(password)
        if problem:
            return jsonify(code=400, error=problem), 400
        if password != confirmPassword:
            return jsonify(code=400, error="Passwords do not match"), 400
        # Optional: the department they would like. Blank means no request. An admin confirms it later.
        requested_department = None
        raw_department = body.get("department")
        if isinstance(raw_department, str) and raw_department.strip():
            requested_department, error = clean_department(raw_department)
            if error:
                return jsonify(code=400, error=error), 400
        elif raw_department is not None and not isinstance(raw_department, str):
            return jsonify(code=400, error="Department must be text."), 400
        if User.query.filter_by(username=username).first():
            return jsonify(code=400, error="User already exists"), 400
        try:
            new_user = User(username=username, password=generate_password_hash(password),
                            requested_department=requested_department)
            db.session.add(new_user)
            db.session.commit()
        except IntegrityError:
                db.session.rollback()
                return jsonify(code=409, error="User already exists"), 409
        except Exception:
            # Log the real traceback server-side; return a generic message so
            # we never echo internals (table names, integrity errors, etc.).
            db.session.rollback()
            logger.exception("Failed to create user: %s", username)
            return jsonify(code=500, error="Internal server error"), 500
        return jsonify(code=200, status="success", message="User created successfully")

    @app.get("/api/me")
    @login_required
    def me():
        return jsonify(code=200, username=current_user.username, is_admin = bool(current_user.is_admin),
                       must_change_password=bool(current_user.must_change_password))

    @app.post("/api/me/password")
    @login_required
    def change_my_password():
        body = request.get_json(silent=True) or {}
        current = body.get("current_password")
        new = body.get("new_password")
        if not isinstance(current, str) or not check_password_hash(current_user.password, current):
            return jsonify(code=400, error="Your current password is wrong."), 400
        problem = password_problem(new)
        if problem:
            return jsonify(code=400, error=problem), 400
        if new == current:
            return jsonify(code=400, error="The new password must be different from the current one."), 400
        user = db.session.get(User, current_user.id)
        try:
            user.password = generate_password_hash(new)
            user.must_change_password = False
            db.session.commit()
        except Exception:
            db.session.rollback()
            logger.exception("Failed to change password for user id=%s", current_user.id)
            return jsonify(code=500, error="Internal server error"), 500
        login_user(user)  # new session id (the password is part of it); every other browser is logged out
        return jsonify(code=200, status="success", message="Password changed")


    '''Admin Commands'''
    @app.get("/api/predict")
    @login_required
    @admin_required
    def predict():
        raw = request.args.get("date")
        try:
            target = input_to_date(raw)
            if target < date.today():
                return jsonify(error="Date must be today or in the future."), 400
        except ValueError:
            return jsonify(error="Bad date format. Use YYYY-MM-DD."), 400
        result = predict_all_for_date(target)
        if "error" in result:
            return jsonify(error=result["error"]), 400
        predictions = result["predictions"]
        message = result["message"]
        return jsonify ({
            "status": "success",
            "requested_date": target.isoformat(),
            "predictions": predictions,
            "message": message
        })

    @app.get("/api/evaluate")
    @login_required
    @admin_required
    def evaluate():
        return jsonify(evaluate_all())

    @app.get("/api/people")
    @login_required
    @admin_required
    def people():
        # Admin-only: the list holds everyone's name and department, and which login belongs to whom.
        # Former people are left out unless ?include_former=1, so no screen shows them by accident.
        query = Person.query
        if request.args.get("include_former") != "1":
            query = query.filter(Person.former.is_(False))
        rows = query.order_by(Person.id).all()
        result = [{"id": p.id, "name": p.name, "department": department_of(p), "former": bool(p.former)}
                  for p in rows]
        logins = {u.person_id: u.id for u in User.query.filter(User.person_id.isnot(None))}
        for entry in result:
            entry["user_id"] = logins.get(entry["id"])
        return jsonify(result)
    @app.cli.command("create-admin")
    @click.argument("username")
    @click.password_option()
    def create_admin(username, password):
        user = User(username=username.strip(),
                    password=generate_password_hash(password),
                    is_admin=True)
        db.session.add(user)
        log_system("login_created", login=user, new="admin")
        db.session.commit()
        click.echo(f"Admin '{username}' created.")

    @app.cli.command("set-password")
    @click.argument("username")
    @click.password_option()
    def set_password(username, password):
        """Set a login's password from the command line, e.g. when the only admin has forgotten theirs."""
        user = User.query.filter_by(username=username.strip()).first()
        if user is None:
            raise click.ClickException(f"No login called '{username}'.")
        problem = password_problem(password)
        if problem:
            raise click.ClickException(problem)
        user.password = generate_password_hash(password)
        user.must_change_password = False
        log_system("password_reset", login=user, person=person_of(user))   # never the password itself
        db.session.commit()
        click.echo(f"Password for '{user.username}' changed.")

    @app.get("/api/users") #GETS user info from API
    @login_required
    @admin_required
    def list_users():
        rows = User.query.order_by(User.username).all()
        return jsonify([
            {"id": u.id, "username": u.username, "is_admin": bool(u.is_admin), "person_id": u.person_id,
             "must_change_password": bool(u.must_change_password), "requested_department": u.requested_department}
            for u in rows
        ])
    
    @app.post("/api/users") #POSTS to API, ADDS users
    @login_required
    @admin_required
    def add_user():
        body = request.get_json(silent=True) or {}
        username = body.get("username")
        username = username.strip() if isinstance(username, str) else ""
        password = body.get("password") or ""
        is_admin = bool(body.get("is_admin", False))

        if not username or not password:
            return jsonify(code=400, error="Username and password are required"), 400
        problem = password_problem(password)
        if problem:
            return jsonify(code=400, error=problem), 400

        # The admin knows this password, so by default the new user must replace it at first login.
        must_change = body.get("must_change_password", True) is not False
        user = User(
            username=username,
            password=generate_password_hash(password),
            is_admin=is_admin,
            must_change_password=must_change,
        )
        try:
            db.session.add(user)
            log_admin("login_created", login=user, new="admin" if is_admin else "user")
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            return jsonify(code=409, error="User already exists"), 409
        except Exception:
            db.session.rollback()
            logger.exception("Failed to create user: %s", username)
            return jsonify(code=500, error="Internal server error"), 500

        return jsonify(code=201, status="success", id=user.id, username=user.username), 201

    @app.post("/api/users/<int:user_id>/password")
    @login_required
    @admin_required
    def reset_password(user_id: int):
        """Give a login a temporary password (the one in the body, or a generated one). They must change it at next login."""
        if user_id == current_user.id:
            return jsonify(code=400, error="Use Change password for your own account."), 400
        user = db.session.get(User, user_id)
        if user is None:
            return jsonify(code=404, error="User not found"), 404
        body = request.get_json(silent=True) or {}
        password = body.get("password")
        generated = password is None or (isinstance(password, str) and not password.strip())
        if generated:
            password = make_temporary_password()
        else:
            problem = password_problem(password)
            if problem:
                return jsonify(code=400, error=problem), 400
        try:
            user.password = generate_password_hash(password)
            user.must_change_password = True
            log_admin("password_reset", login=user, person=person_of(user))   # never the password itself
            db.session.commit()
        except Exception:
            db.session.rollback()
            logger.exception("Failed to reset password for user id=%s", user_id)
            return jsonify(code=500, error="Internal server error"), 500
        return jsonify(code=200, status="success", username=user.username,
                       temporary_password=password, generated=generated)

    @app.post("/api/users/bulk")
    @login_required
    @admin_required
    def bulk_create_users():
        """One login per chosen person, linked to them, with a generated temporary password each.

        All or nothing: a person who already has a login, or an unknown person, stops the whole batch.
        The passwords are in this response only, so the admin has to hand them out (or download them).
        """
        body = request.get_json(silent=True) or {}
        ids = body.get("person_ids")
        if not isinstance(ids, list) or not ids:
            return jsonify(code=400, error="Choose at least one person."), 400
        if len(ids) > BULK_LOGIN_LIMIT:
            return jsonify(code=400, error=f"At most {BULK_LOGIN_LIMIT} logins at a time."), 400
        if any(type(i) is not int for i in ids):  # type() so that true/false are not accepted as 1/0
            return jsonify(code=400, error="person_ids must be a list of integers."), 400
        ids = list(dict.fromkeys(ids))
        people = {p.id: p for p in Person.query.filter(Person.id.in_(ids))}
        missing = [i for i in ids if i not in people]
        if missing:
            return jsonify(code=404, error=f"Person not found: {', '.join(map(str, missing))}."), 404
        former = sorted(p.name for p in people.values() if p.former)
        if former:
            return jsonify(code=409, error=f"These are former people and cannot have a login: {', '.join(former)}."), 409
        linked = {u.person_id for u in User.query.filter(User.person_id.in_(ids))}
        if linked:
            names = ", ".join(sorted(people[i].name for i in linked))
            return jsonify(code=409, error=f"These people already have a login: {names}."), 409

        taken = {u.username.lower() for u in User.query.with_entities(User.username)}
        created = []
        try:
            for person in sorted(people.values(), key=lambda p: p.name.lower()):
                username = username_from_name(person.name, taken)
                temporary = make_temporary_password()
                new_user = User(username=username, password=generate_password_hash(temporary), is_admin=False,
                                person_id=person.id, must_change_password=True)
                db.session.add(new_user)
                log_admin("login_created", login=new_user, person=person, new="user")
                created.append({"person_id": person.id, "person": person.name, "username": username,
                                "temporary_password": temporary})
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            return jsonify(code=409, error="Someone else changed the logins at the same time. Nothing was created; try again."), 409
        except Exception:
            db.session.rollback()
            logger.exception("Failed to create logins in bulk")
            return jsonify(code=500, error="Internal server error"), 500
        return jsonify(code=201, status="success", created=created), 201

    @app.post("/api/users/<int:user_id>/department-request")
    @login_required
    @admin_required
    def answer_department_request(user_id: int):
        """Confirm (the linked person moves to the requested department) or dismiss a sign-up department request."""
        user = db.session.get(User, user_id)
        if user is None:
            return jsonify(code=404, error="User not found"), 404
        body = request.get_json(silent=True) or {}
        action = body.get("action")
        if action not in ("confirm", "dismiss"):
            return jsonify(code=400, error="action must be 'confirm' or 'dismiss'."), 400
        requested = user.requested_department
        if not requested:
            return jsonify(code=409, error="This login has no pending department request."), 409
        person = person_of(user)
        if action == "confirm":
            if person is None:
                return jsonify(code=409, error="Link this login to a person first."), 409
            department, error = clean_department(requested)
            if error:
                return jsonify(code=400, error=error), 400
            old_department = person.department
            person.department = department
        user.requested_department = None
        try:
            if action == "confirm":
                log_admin("department_request_confirmed", person=person, login=user, old=old_department, new=department)
            else:
                log_admin("department_request_dismissed", person=person, login=user, old=requested)
            db.session.commit()
        except Exception:
            db.session.rollback()
            logger.exception("Failed to answer department request of user id=%s", user_id)
            return jsonify(code=500, error="Internal server error"), 500
        return jsonify(code=200, status="success", action=action, department=requested)

    @app.delete("/api/users/<int:user_id>") #DELETES users from API
    @login_required
    @admin_required
    def remove_user(user_id: int):
        if user_id == current_user.id:
            return jsonify(code=400, error="You cannot delete your own account"), 400

        user = db.session.get(User, user_id)
        if user is None:
            return jsonify(code=404, error="User not found"), 404

        # Copies taken first: the log keeps who and what was removed after the rows themselves are gone.
        login_copy = SimpleNamespace(id=user.id, username=user.username)
        person = person_of(user)
        person_copy = None if person is None else SimpleNamespace(id=person.id, name=person.name)
        role = "admin" if user.is_admin else "user"
        try:
            db.session.delete(user)
            log_admin("login_deleted", login=login_copy, person=person_copy, old=role)
            db.session.commit()
        except Exception:
            db.session.rollback()
            #code to update latest modification
            logger.exception("Failed to delete user id=%s", user_id)
            return jsonify(code=500, error="Internal server error"), 500

        return jsonify(code=200, status="success", message="User removed")

    '''Creates attendance users'''
    @app.post("/api/table_persons")
    @login_required
    @admin_required
    def add_person():
        body = request.get_json(silent=True) or {}
        name = (body.get("name") or "").strip()
        if not name:
            return jsonify(error="Name is required"), 400
        existing = Person.query.filter_by(name=name).first()
        if existing is not None:
            if existing.former:
                return jsonify(error=f"{name} is a former person. Restore them instead of adding them again."), 409
            return jsonify(error="Person already exists"), 409
        if len(name) > Person.name.type.length:
            return jsonify(error=f"Name must be at most {Person.name.type.length} characters."), 400
        department, error = clean_department(body.get("department"))
        if error:
            return jsonify(error=error), 400
        person = Person(name=name, department=department)
        try:
            db.session.add(person)
            log_admin("person_added", person=person, new=department)
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            return jsonify(error="Person already exists"), 409
        except Exception:
            db.session.rollback()
            logger.exception("Failed to create person: %s", name)
            return jsonify(error="Internal server error"), 500
        return jsonify(id=person.id, name=person.name, department=person.department, former=False), 201

    @app.patch("/api/people/<int:person_id>")
    @login_required
    @admin_required
    def update_person(person_id: int):
        """Change a person's name, department, linked login, or whether they are a former person.

        Removing someone is {"former": true}: they and their attendance stay in the database, their login is
        unlinked (the login itself is kept), and they drop out of the board, predictions and head-counts.
        {"former": false} brings them back; their login is not re-linked automatically.
        """
        person = db.session.get(Person, person_id)
        if person is None:
            return jsonify(error="Person not found"), 404
        body = request.get_json(silent=True) or {}
        if not any(key in body for key in ("department", "user_id", "name", "former")):
            return jsonify(error="Nothing to update."), 400

        if "department" in body:
            department, error = clean_department(body["department"])
            if error:
                return jsonify(error=error), 400

        new_name = None
        if "name" in body:
            new_name = body["name"].strip() if isinstance(body["name"], str) else ""
            if not new_name:
                return jsonify(error="Name is required"), 400
            if len(new_name) > Person.name.type.length:
                return jsonify(error=f"Name must be at most {Person.name.type.length} characters."), 400
            clash = Person.query.filter(Person.name == new_name, Person.id != person.id).first()
            if clash is not None:
                kind = "A former person" if clash.former else "Someone"
                return jsonify(error=f"{kind} is already called {new_name}."), 409

        if "former" in body and type(body["former"]) is not bool:
            return jsonify(error="former must be true or false."), 400
        will_be_former = body["former"] if "former" in body else bool(person.former)

        new_login = None
        if "user_id" in body:
            user_id = body["user_id"]
            if user_id is not None:
                # type() so that true/false are not accepted as 1/0
                if type(user_id) is not int:
                    return jsonify(error="user_id must be an integer or null."), 400
                if will_be_former:
                    return jsonify(error="A former person cannot have a login linked. Restore them first."), 409
                new_login = db.session.get(User, user_id)
                if new_login is None:
                    return jsonify(error="User not found"), 404

        was_former = bool(person.former)
        removing = will_be_former and not was_former
        renamed = new_name is not None and new_name != person.name
        unlinked_username = None
        unlinked_login = None
        # What the activity log needs to say what changed, taken before anything is changed.
        old_name, old_department = person.name, person.department
        was_linked = [SimpleNamespace(id=u.id, username=u.username) for u in User.query.filter(User.person_id == person.id)]
        moved_from = None
        if new_login is not None and new_login.person_id not in (None, person.id):
            other = db.session.get(Person, new_login.person_id)
            moved_from = other.name if other else None
        try:
            if "department" in body:
                person.department = department
            if renamed:
                person.name = new_name
            if will_be_former != was_former:
                person.former = will_be_former
                if will_be_former:
                    # Removing someone also unlinks their login; the login itself stays.
                    for old in User.query.filter(User.person_id == person.id):
                        old.person_id = None
                        unlinked_username = old.username
                        unlinked_login = SimpleNamespace(id=old.id, username=old.username)
            if "user_id" in body:
                # One login per person: whoever was linked before is unlinked first.
                for old in User.query.filter(User.person_id == person.id):
                    if new_login is None or old.id != new_login.id:
                        old.person_id = None
                db.session.flush()
                if new_login is not None:
                    new_login.person_id = person.id  # moves them off any other person

            # The log: one row for each thing that really changed, in this order, in the same transaction.
            if "department" in body and department != old_department:
                log_admin("department_changed", person=person, old=old_department, new=department)
            if renamed:
                log_admin("person_renamed", person=person, old=old_name, new=new_name)
            if will_be_former != was_former:
                log_admin("person_removed" if will_be_former else "person_restored", person=person, login=unlinked_login)
            if "user_id" in body:
                if not removing:   # removing someone already logged the login it unlinked
                    for before in was_linked:
                        if new_login is None or before.id != new_login.id:
                            log_admin("login_unlinked", person=person, login=before, old=person.name)
                if new_login is not None and all(before.id != new_login.id for before in was_linked):
                    log_admin("login_linked", person=person, login=new_login, old=moved_from, new=person.name)
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            return jsonify(error="Someone else changed the people at the same time. Try again."), 409
        except Exception:
            db.session.rollback()
            logger.exception("Failed to update person id=%s", person_id)
            return jsonify(error="Internal server error"), 500

        if renamed or will_be_former != was_former:
            # The models are keyed by name and trained only on active people; neither change touches updated_at.
            try:
                invalidate_models()
            except OSError:
                logger.exception("Person %s was changed but the model cache could not be cleared", person_id)
        linked = User.query.filter_by(person_id=person.id).first()
        return jsonify(id=person.id, name=person.name, department=person.department,
                       user_id=linked.id if linked else None, former=bool(person.former),
                       unlinked_username=unlinked_username)

    '''Updating or changing attendance'''
    STATUSES = {0: "Out", 1: "In", 2: "Partial Day"}

    def valid_status(value) -> bool:
        # type() rather than isinstance(): True/False are ints in Python and must not count as 1/0.
        return type(value) is int and value in STATUSES

    @app.put("/api/attendance")
    @login_required
    @admin_required
    def record_attendance():
        body = request.get_json(silent=True) or {}
        try:
            day = input_to_date(body.get("date"))
        except ValueError:
            return jsonify(error="Bad date format. Use YYYY-MM-DD."), 400
        if day > local_today():
            return jsonify(error="Cannot record attendance for a future date."), 400
        if day.weekday() > 4:
            return jsonify(error="Attendance can only be recorded for weekdays."), 400

        entries = body.get("records")
        if not isinstance(entries, list) or not entries:
            return jsonify(error="'records' must be a non-empty list."), 400

        # Validate everything before touching the DB
        try:
            ids = {int(e["person_id"]) for e in entries}
        except (KeyError, TypeError, ValueError):
            return jsonify(error="Each record needs an integer person_id."), 400
        if len(ids) != len(entries):
            return jsonify(error="Duplicate person_id in records."), 400
        # "status": null removes the person's record for that day (back to "Not Recorded").
        # The key must be present, so a record with no status can never delete by accident.
        if any("status" not in e for e in entries):
            return jsonify(error="Each record needs a status (0, 1, 2, or null to remove)."), 400
        if any(e["status"] is not None and not valid_status(e["status"]) for e in entries):
            return jsonify(error=f"status must be one of {sorted(STATUSES)}, or null to remove."), 400

        found = {p.id: p for p in Person.query.filter(Person.id.in_(ids))}
        if ids - set(found):
            return jsonify(error=f"Unknown person_id(s): {sorted(ids - set(found))}"), 400
        former = sorted(p.name for p in found.values() if p.former)
        if former:
            return jsonify(error=f"Former people cannot be recorded: {', '.join(former)}. "
                                 "Reload the page to see the current list."), 400

        existing = {r.person_id: r for r in AttendanceRecord.query.filter_by(date=day)}

        def as_text(status):
            """How the log writes one day's status, e.g. '2026-10-13 In'."""
            return f"{day.isoformat()} {STATUSES[status]}"

        saved = removed = 0
        try:
            for e in entries:
                pid = int(e["person_id"])
                who, status = found[pid], e["status"]
                if status is None:
                    if pid in existing:
                        was = existing[pid].status
                        db.session.delete(existing[pid])
                        removed += 1
                        log_admin("attendance_removed", person=who, old=as_text(was))
                elif pid in existing:
                    was = existing[pid].status
                    existing[pid].status = status
                    saved += 1
                    if was != status:   # saving the same status again changes nothing, so there is nothing to log
                        log_admin("attendance_set", person=who, old=as_text(was), new=as_text(status))
                else:
                    db.session.add(AttendanceRecord(
                        person_id=pid, date=day,
                        day_of_week=day.isoweekday(), status=status,
                    ))
                    saved += 1
                    log_admin("attendance_set", person=who, new=as_text(status))
            db.session.commit()
        except Exception:
            db.session.rollback()
            logger.exception("Failed to save attendance for %s", day)
            return jsonify(error="Internal server error"), 500

        if removed:
            # Deleting a row does not touch updated_at, so the model cache would never notice.
            try:
                invalidate_models()
            except OSError:
                logger.exception("Records were removed but the model cache could not be cleared")

        return jsonify(status="success", date=day.isoformat(), saved=saved, removed=removed)


    @app.get("/api/attendance")
    @login_required
    @admin_required
    def get_attendance():
        try:
            day = input_to_date(request.args.get("date"))
        except ValueError:
            return jsonify(error="Bad date format. Use YYYY-MM-DD."), 400
        rows = (AttendanceRecord.query.filter_by(date=day)
                .join(Person, Person.id == AttendanceRecord.person_id)
                .filter(Person.former.is_(False)).all())
        return jsonify(date=day.isoformat(),
                    records=[{"person_id": r.person_id, "status": r.status} for r in rows])

    '''Today, self-service and tally'''
    @app.get("/api/today")
    @login_required
    def today_info():
        now = now_local()
        return jsonify(date=now.date().isoformat(), weekend=now.weekday() > 4, timezone=TIMEZONE_LABEL)

    def linked_person():
        return db.session.get(Person, current_user.person_id) if current_user.person_id else None

    @app.get("/api/me/attendance")
    @login_required
    def my_attendance():
        now = now_local()
        day = now.date()
        person = linked_person()
        status = None
        if person is not None:
            record = AttendanceRecord.query.filter_by(person_id=person.id, date=day).first()
            status = record.status if record else None
        window_open, seconds_left = self_service_window(now)
        return jsonify(
            date=day.isoformat(),
            weekend=now.weekday() > 4,
            timezone=TIMEZONE_LABEL,
            cutoff=SELF_SERVICE_CUTOFF,
            window_open=window_open,
            seconds_left=seconds_left,
            linked=person is not None,
            person=None if person is None else {"id": person.id, "name": person.name,
                                                "department": department_of(person)},
            status=status,
            can_edit=person is not None and window_open,
        )

    @app.put("/api/me/attendance")
    @login_required
    def update_my_attendance():
        """A user sets their OWN status for today, until the cutoff. The person comes from the
        account's admin-made link, never from the request, so nobody can edit someone else."""
        body = request.get_json(silent=True) or {}
        status = body.get("status")
        if not valid_status(status):
            return jsonify(error=f"status must be one of {sorted(STATUSES)}"), 400
        person = linked_person()
        if person is None:
            return jsonify(error="Your account isn't linked to a person yet. Ask an admin to link it."), 403
        now = now_local()
        if now.weekday() > 4:
            return jsonify(error="Attendance can only be recorded for weekdays."), 400
        window_open, _ = self_service_window(now)
        if not window_open:
            return jsonify(error=f"Changes closed at {SELF_SERVICE_CUTOFF} ({TIMEZONE_LABEL}). "
                                 "Ask an admin to update your attendance."), 403
        day = now.date()
        try:
            record = AttendanceRecord.query.filter_by(person_id=person.id, date=day).first()
            if record is None:
                db.session.add(AttendanceRecord(person_id=person.id, date=day,
                                                day_of_week=day.isoweekday(), status=status))
            else:
                record.status = status
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            return jsonify(error="Your attendance was changed at the same moment. Please try again."), 409
        except Exception:
            db.session.rollback()
            logger.exception("Failed to save self-service attendance for person id=%s", person.id)
            return jsonify(error="Internal server error"), 500
        return jsonify(status="success", date=day.isoformat(), attendance=status)

    @app.get("/api/attendance/summary")
    @login_required
    def attendance_summary():
        """Head-counts for today with no names, for admins and for logins linked to a person.

        A login with no person behind it (never linked, or its person was removed) gets nothing, so someone who
        has left cannot keep watching the office numbers."""
        if not current_user.is_admin and current_user.person_id is None:
            return jsonify(error="Your account isn't linked to a person, so there are no head-counts for you."), 403
        department = (request.args.get("department") or "").strip()
        if len(department) > Person.department.type.length:
            return jsonify(error="Unknown department."), 400
        day = local_today()
        people_query = Person.query.filter(Person.former.is_(False))
        records_query = (
            db.session.query(AttendanceRecord.status, db.func.count())
            .join(Person, Person.id == AttendanceRecord.person_id)
            .filter(AttendanceRecord.date == day, Person.former.is_(False))
        )
        if department:
            people_query = people_query.filter(Person.department == department)
            records_query = records_query.filter(Person.department == department)
        total = people_query.count()
        by_status = {status: 0 for status in sorted(STATUSES)}
        for status, count in records_query.group_by(AttendanceRecord.status):
            by_status[status] = count
        return jsonify(
            date=day.isoformat(),
            department=department or None,
            by_status={str(status): count for status, count in by_status.items()},
            not_recorded=total - sum(by_status.values()),
            total=total,
        )

    '''Backup'''
    @app.get("/api/export/attendance.csv")
    @login_required
    @admin_required
    def export_attendance_csv():
        """Everyone and every attendance record as one CSV (format described in backup.py)."""
        filename = f"attendance-backup-{local_today().isoformat()}.csv"
        return Response(
            backup.export_csv_text(),
            mimetype="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store",  # it is a copy of everything: do not let anything cache it
            },
        )

    @app.cli.command("export-backup")
    @click.argument("path", type=click.Path(dir_okay=False, writable=True))
    def export_backup_command(path):
        """Write the attendance backup CSV to PATH (the same file the Download backup button gives)."""
        Path(path).write_text(backup.export_csv_text(), encoding="utf-8", newline="")
        click.echo(f"Backup written to {path}")

    @app.cli.command("import-backup")
    @click.argument("path", type=click.Path(exists=True, dir_okay=False))
    @click.option("--replace", is_flag=True,
                  help="Delete ALL current people and attendance first. Without it, the database must be empty.")
    @click.option("--yes", is_flag=True, help="Do not ask for confirmation with --replace.")
    def import_backup_command(path, replace, yes):
        """Load a backup CSV made by export-backup (or the Download backup button)."""
        if replace and not yes:
            click.confirm("This deletes every current person and attendance record first. Continue?", abort=True)
        try:
            with open(path, newline="", encoding="utf-8-sig") as handle:
                result = backup.restore_csv(handle, allowed_statuses=set(STATUSES), replace=replace)
        except backup.BackupError as err:
            raise click.ClickException(str(err))
        # Backup Step
        log_system("backup_restored", old="replaced existing data" if replace else None,
                   new=f"{result['people']} people, {result['records']} attendance records")
        db.session.commit()
        click.echo(f"Restored {result['people']} people and {result['records']} attendance records.")
        click.echo("Logins are not part of a backup: create them again under Accounts (or link existing ones under People).")

    return app

    



if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    app = create_app()
    with app.app_context():
        db.create_all()
        ensure_attendance_schema()
        ensure_person_department()
        ensure_person_former()
        ensure_user_person_link()
        ensure_user_account_columns()
        activity_log.ensure_activity_log()
        import_csv()
    app.run(debug=True, port=5000)