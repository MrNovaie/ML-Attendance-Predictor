import hashlib

from flask_login import UserMixin
from extensions import db

# Departments a person can belong to. This is a sample list: edit it here and the
# form dropdown and the server-side check both follow. People without a department
# are placed in DEFAULT_DEPARTMENT, which is always an allowed choice as well.
DEFAULT_DEPARTMENT = "Holding"
DEPARTMENTS = ["Intelligence", "Finance", "Human Resources", "Manpower", "Headquarters"]


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    person_id = db.Column(db.Integer, db.ForeignKey('person.id'), unique=True, nullable=True)
    # 255 chars to fit werkzeug.generate_password_hash output (pbkdf2/scrypt
    # produces ~150-200+ char strings; 80 truncates and breaks login).
    password = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default = False, nullable = False)
    # True = an admin created the login or reset its password. False = the user has picked their own password and can do anything else.
    must_change_password = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    # The department someone asked for when they signed up (only when sign-up is open). Sent to admin as a request.
    # Admin confirms it (it then becomes the linked person's department) or dismisses it.
    requested_department = db.Column(db.String(80), nullable=True)

    def session_fingerprint(self) -> str:
        # Part of the session cookie's user id (see get_id). Changing the password changes it,
        # so every other browser that was logged in as this user is logged out.
        return hashlib.sha256(self.password.encode("utf-8")).hexdigest()[:16]

    def get_id(self) -> str:
        return f"{self.id}:{self.session_fingerprint()}"

    def __repr__(self):
        return f"<User {self.username}>"

# A Person may not be a User, though typically Users are linked to Persons. Persons are the ones getting predicted.
class Person(db.Model):
    __tablename__ = 'person'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    department = db.Column(
        db.String(80),
        nullable=False,
        default=DEFAULT_DEPARTMENT,
        server_default=DEFAULT_DEPARTMENT,
    )
    # A former person has been "removed" by an admin: they stay in the database with all their attendance
    # (so history and backups are intact) but drop out of the board, predictions, head-counts and new logins.
    # An admin can restore them. 
    former = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    attendance_records = db.relationship('AttendanceRecord', backref='person', lazy=True)

class AttendanceRecord(db.Model):
    __tablename__ = 'attendance_record'
    id = db.Column(db.Integer, primary_key=True)
    person_id = db.Column(db.Integer, db.ForeignKey('person.id'), nullable=False)
    date = db.Column(db.Date, nullable=False)
    # day_of_week mirrors ml.DOW_NAMES (Mon=1..Fri=5); keep both in sync.
    day_of_week = db.Column(db.SmallInteger, nullable=False)
    # status mirrors ml.predict_attendance VERDICT (0=Out, 1=In, 2=Partial Day).
    status = db.Column(db.SmallInteger, nullable=False)
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    updated_at = db.Column(db.DateTime, nullable=False, default=db.func.now(), onupdate=db.func.now())

    __table_args__ = (
        db.UniqueConstraint('person_id', 'date', name='uq_person_date'),
        db.CheckConstraint('day_of_week IN (1, 2, 3, 4, 5)'),
        db.CheckConstraint('status IN (0, 1, 2)'),
    )