import pandas as pd
import logging
from extensions import db
from pathlib import Path
from users import AttendanceRecord, Person
import ml

logger = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parent
CSV_PATH = BASE_DIR / "data" / "sample_attendance.csv" #change to attendance.csv

YEAR = 2026
def wide_to_long(df: pd.DataFrame) -> pd.DataFrame:
    """Convert the wide attendance CSV to long format."""
    long_df = pd.melt(df, id_vars=ml.META_COLS, var_name='person_name', value_name='status')
    long_df["date"] = pd.to_datetime(dict(year=YEAR, month=long_df["Month"], day=long_df["Day"])).dt.date
    long_df["day_of_week"] = long_df["Day of Week"].astype(int)
    long_df["status"] = long_df["status"].astype(int)
    long_df = long_df.drop(columns=ml.META_COLS)
    return long_df


def import_csv(replace_existing=False):
    # Any people or attendance means the DB is already seeded.
    if not replace_existing and (
        Person.query.first() is not None or AttendanceRecord.query.first() is not None
    ):
        return
    if not CSV_PATH.exists():
        logger.warning("No CSV at %s; starting with an empty database.", CSV_PATH)
        return
    print(f"Importing CSV from {CSV_PATH}")
    df = pd.read_csv(CSV_PATH)

    if replace_existing:
        AttendanceRecord.query.delete()
        Person.query.delete()
        db.session.flush()
    long_df = wide_to_long(df)
    people_by_name = {}
    for _, row in long_df.iterrows():
        name = row["person_name"]
        if name not in people_by_name:
            person = Person.query.filter_by(name=name).first()
            if person is None:
                person = Person(name=name)
                db.session.add(person)
                db.session.flush()  # assigns person.id without full commit
            people_by_name[name] = person
        db.session.add(AttendanceRecord(
            person_id=people_by_name[name].id,
            date=row["date"],
            day_of_week=int(row["day_of_week"]),
            status=int(row["status"]),
        ))
        
    db.session.commit()
    print(f"CSV data successfully imported to 'attendance_record' table!")
   