import logging
import os
import click
from datetime import date

from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_login import login_user, login_required, logout_user, current_user
from functools import wraps
from extensions import db, login_manager
from import_csv import import_csv
from ml import input_to_date, get_name_list, load_data, predict_all_for_date, evaluate_all
# Importing users registers User on db's metadata so db.create_all() picks
# it up. Don't drop this import even though `User` is the only name we use.

from pathlib import Path
from sqlalchemy.exc import IntegrityError
from users import User, Person, AttendanceRecord
from werkzeug.security import check_password_hash, generate_password_hash


logger = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parent

def ensure_attendance_schema() -> None:
    """Recreate person/attendance_record if the DB predates the ORM long-format schema.

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
    CORS(app, supports_credentials=True, origins=["http://localhost:5173"])
    db.init_app(app)
    login_manager.init_app(app)
    @login_manager.unauthorized_handler
    def unauthorized():
        return jsonify(code=401, error="Unauthorized. Please login to continue."), 401
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
        return jsonify([{"id": p.id, "name": p.name} for p in rows])

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
            {"id": u.id, "username": u.username, "is_admin": bool(u.is_admin)}
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
        person = Person(name=name)
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
        return jsonify(id=person.id, name=person.name), 201

    '''Updating or changing attendance'''
    ALLOWED_STATUSES = {0, 1, 2}
    
    @app.put("/api/attendance")
    @login_required
    def record_attendance():
        body = request.get_json(silent=True) or {}
        try:
            day = input_to_date(body.get("date"))
            if day > date.today():
                return jsonify(error="Cannot record attendance for a future date."), 400
            if day.weekday() > 4:
                return jsonify(error="Attendance can only be recorded for weekdays."), 400
        except ValueError:
            return jsonify(error="Bad date format. Use YYYY-MM-DD."), 400
        if day > date.today():
            return jsonify(error="Cannot record attendance for a future date."), 400

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
        if any(e.get("status") not in ALLOWED_STATUSES for e in entries):
            return jsonify(error=f"status must be one of {sorted(ALLOWED_STATUSES)}"), 400

        known = {p.id for p in Person.query.filter(Person.id.in_(ids))}
        if ids - known:
            return jsonify(error=f"Unknown person_id(s): {sorted(ids - known)}"), 400

        existing = {r.person_id: r for r in AttendanceRecord.query.filter_by(date=day)}
        try:
            for e in entries:
                pid = int(e["person_id"])
                if pid in existing:
                    existing[pid].status = e["status"]
                else:
                    db.session.add(AttendanceRecord(
                        person_id=pid, date=day,
                        day_of_week=day.isoweekday(), status=e["status"],
                    ))
            db.session.commit()
        except Exception:
            db.session.rollback()
            logger.exception("Failed to save attendance for %s", day)
            return jsonify(error="Internal server error"), 500

        return jsonify(status="success", date=day.isoformat(), saved=len(entries))


    @app.get("/api/attendance")
    @login_required
    def get_attendance():
        try:
            day = input_to_date(request.args.get("date"))
        except ValueError:
            return jsonify(error="Bad date format. Use YYYY-MM-DD."), 400
        rows = AttendanceRecord.query.filter_by(date=day).all()
        return jsonify(date=day.isoformat(),
                    records=[{"person_id": r.person_id, "status": r.status} for r in rows])
    return app

    



if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    app = create_app()
    with app.app_context():
        db.create_all()
        ensure_attendance_schema()
        import_csv()
    app.run(debug=True, port=5000)