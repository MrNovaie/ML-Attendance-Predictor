from flask_login import UserMixin
from extensions import db


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    # 255 chars to fit werkzeug.generate_password_hash output (pbkdf2/scrypt
    # produces ~150-200+ char strings; 80 truncates and breaks login).
    password = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default = False, nullable = False)
    def __repr__(self):
        return f"<User {self.username}>"


class Person(db.Model):
    __tablename__ = 'person'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
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
