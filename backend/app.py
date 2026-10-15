import logging
import os
import click
import math
from datetime import date, datetime, timedelta, timezone

from flask import Flask, Response, jsonify, request
from flask_cors import CORS
from flask_login import login_user, login_required, logout_user, current_user
from functools import wraps
from extensions import db, login_manager
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


def department_of(person) -> str:
    return (person.department or "").strip() or DEFAULT_DEPARTMENT


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
    '''Endpoint Section'''
    @app.get("/api/predict")
    @login_required
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
    def evaluate():
        return jsonify(evaluate_all())

    @app.get("/api/people")
    @login_required
    def people():
        rows = Person.query.order_by(Person.id).all()
        result = [{"id": p.id, "name": p.name, "department": department_of(p)} for p in rows]
        if current_user.is_admin:
            # Which login is linked to whom is admin business; other users never see it.
            logins = {u.person_id: u.id for u in User.query.filter(User.person_id.isnot(None))}
            for entry in result:
                entry["user_id"] = logins.get(entry["id"])
        return jsonify(result)

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
        try:
            return db.session.get(User, int(user_id))
        except (ValueError, TypeError):
            return None

    @app.route("/api/register", methods=["POST"])
    def register():
        body = request.get_json(silent=True) or {}
        username = (body.get("username") or "").strip()
        password = body.get("password")
        confirmPassword = body.get("confirmPassword")
        if not username or not password or not confirmPassword:
            return jsonify(code=400, error="Username and password are required"), 400
        if len(password) < 8:
            return jsonify(code=400, error="Password must be at least 8 characters"), 400
        if password != confirmPassword:
            return jsonify(code=400, error="Passwords do not match"), 400
        if User.query.filter_by(username=username).first():
            return jsonify(code=400, error="User already exists"), 400
        try:
            new_user = User(username=username, password=generate_password_hash(password))
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
        return jsonify(code=200, username=current_user.username, is_admin = bool(current_user.is_admin))


    '''Admin Commands'''
    @app.cli.command("create-admin")
    @click.argument("username")
    @click.password_option()
    def create_admin(username, password):
        user = User(username=username.strip(),
                    password=generate_password_hash(password),
                    is_admin=True)
        db.session.add(user)
        db.session.commit()
        click.echo(f"Admin '{username}' created.")

    @app.get("/api/users") #GETS user info from API
    @login_required
    @admin_required
    def list_users():
        rows = User.query.order_by(User.username).all()
        return jsonify([
            {"id": u.id, "username": u.username, "is_admin": bool(u.is_admin), "person_id": u.person_id}
            for u in rows
        ])
    
    @app.post("/api/users") #POSTS to API, ADDS users
    @login_required
    @admin_required
    def add_user():
        body = request.get_json(silent=True) or {}
        username = (body.get("username") or "").strip()
        password = body.get("password") or ""
        is_admin = bool(body.get("is_admin", False))

        if not username or not password:
            return jsonify(code=400, error="Username and password are required"), 400
        if len(password) < 8:
            return jsonify(code=400, error="Password must be at least 8 characters"), 400

        user = User(
            username=username,
            password=generate_password_hash(password),
            is_admin=is_admin,
        )
        try:
            db.session.add(user)
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            return jsonify(code=409, error="User already exists"), 409
        except Exception:
            db.session.rollback()
            logger.exception("Failed to create user: %s", username)
            return jsonify(code=500, error="Internal server error"), 500

        return jsonify(code=201, status="success", id=user.id, username=user.username), 201

    @app.delete("/api/users/<int:user_id>") #DELETES users from API
    @login_required
    @admin_required
    def remove_user(user_id: int):
        if user_id == current_user.id:
            return jsonify(code=400, error="You cannot delete your own account"), 400

        user = db.session.get(User, user_id)
        if user is None:
            return jsonify(code=404, error="User not found"), 404

        try:
            db.session.delete(user)
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
        if Person.query.filter_by(name=name).first():
            return jsonify(error="Person already exists"), 409
        department, error = clean_department(body.get("department"))
        if error:
            return jsonify(error=error), 400
        person = Person(name=name, department=department)
        try:
            db.session.add(person)
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            return jsonify(error="Person already exists"), 409
        except Exception:
            db.session.rollback()
            logger.exception("Failed to create person: %s", name)
            return jsonify(error="Internal server error"), 500
        return jsonify(id=person.id, name=person.name, department=person.department), 201

    @app.patch("/api/people/<int:person_id>")
    @login_required
    @admin_required
    def update_person(person_id: int):
        person = db.session.get(Person, person_id)
        if person is None:
            return jsonify(error="Person not found"), 404
        body = request.get_json(silent=True) or {}
        if "department" not in body and "user_id" not in body:
            return jsonify(error="Nothing to update."), 400

        if "department" in body:
            department, error = clean_department(body["department"])
            if error:
                return jsonify(error=error), 400

        new_login = None
        if "user_id" in body:
            user_id = body["user_id"]
            if user_id is not None:
                # type() so that true/false are not accepted as 1/0
                if type(user_id) is not int:
                    return jsonify(error="user_id must be an integer or null."), 400
                new_login = db.session.get(User, user_id)
                if new_login is None:
                    return jsonify(error="User not found"), 404

        try:
            if "department" in body:
                person.department = department
            if "user_id" in body:
                # One login per person: whoever was linked before is unlinked first.
                for old in User.query.filter(User.person_id == person.id):
                    if new_login is None or old.id != new_login.id:
                        old.person_id = None
                db.session.flush()
                if new_login is not None:
                    new_login.person_id = person.id  # moves them off any other person
            db.session.commit()
        except Exception:
            db.session.rollback()
            logger.exception("Failed to update person id=%s", person_id)
            return jsonify(error="Internal server error"), 500
        linked = User.query.filter_by(person_id=person.id).first()
        return jsonify(id=person.id, name=person.name, department=person.department,
                       user_id=linked.id if linked else None)

    '''Updating or changing attendance'''
    ALLOWED_STATUSES = {0, 1, 2}

    def valid_status(value) -> bool:
        # type() rather than isinstance(): True/False are ints in Python and must not count as 1/0.
        return type(value) is int and value in ALLOWED_STATUSES

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
        # "status": null removes the person's record for that day (back to "Not recorded").
        # The key must be present, so a record with no status can never delete by accident.
        if any("status" not in e for e in entries):
            return jsonify(error="Each record needs a status (0, 1, 2, or null to remove)."), 400
        if any(e["status"] is not None and not valid_status(e["status"]) for e in entries):
            return jsonify(error=f"status must be one of {sorted(ALLOWED_STATUSES)}, or null to remove."), 400

        known = {p.id for p in Person.query.filter(Person.id.in_(ids))}
        if ids - known:
            return jsonify(error=f"Unknown person_id(s): {sorted(ids - known)}"), 400

        existing = {r.person_id: r for r in AttendanceRecord.query.filter_by(date=day)}
        saved = removed = 0
        try:
            for e in entries:
                pid = int(e["person_id"])
                if e["status"] is None:
                    if pid in existing:
                        db.session.delete(existing[pid])
                        removed += 1
                elif pid in existing:
                    existing[pid].status = e["status"]
                    saved += 1
                else:
                    db.session.add(AttendanceRecord(
                        person_id=pid, date=day,
                        day_of_week=day.isoweekday(), status=e["status"],
                    ))
                    saved += 1
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
        rows = AttendanceRecord.query.filter_by(date=day).all()
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
            return jsonify(error=f"status must be one of {sorted(ALLOWED_STATUSES)}"), 400
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
        """Head-counts for today with no names, so any logged-in user can sanity-check the numbers."""
        department = (request.args.get("department") or "").strip()
        if len(department) > Person.department.type.length:
            return jsonify(error="Unknown department."), 400
        day = local_today()
        people_query = Person.query
        records_query = (
            db.session.query(AttendanceRecord.status, db.func.count())
            .join(Person, Person.id == AttendanceRecord.person_id)
            .filter(AttendanceRecord.date == day)
        )
        if department:
            people_query = people_query.filter(Person.department == department)
            records_query = records_query.filter(Person.department == department)
        total = people_query.count()
        by_status = {status: 0 for status in sorted(ALLOWED_STATUSES)}
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
                result = backup.restore_csv(handle, allowed_statuses=ALLOWED_STATUSES, replace=replace)
        except backup.BackupError as err:
            raise click.ClickException(str(err))
        click.echo(f"Restored {result['people']} people and {result['records']} attendance records.")
        click.echo("Logins are not part of a backup: link them to people again under Record attendance.")

    return app

    



if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    app = create_app()
    with app.app_context():
        db.create_all()
        ensure_attendance_schema()
        ensure_person_department()
        ensure_user_person_link()
        import_csv()
    app.run(debug=True, port=5000)