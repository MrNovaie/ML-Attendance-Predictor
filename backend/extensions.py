# Extensions are constructed WITHOUT an app and bound later via init_app(app)
# in create_app(). This decouples them from app.py so that modules like
# users.py / import_csv.py / ml.py can `from extensions import db` without
# triggering a circular import through app.py.
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager

db = SQLAlchemy()
login_manager = LoginManager()
# Endpoint name (not URL) used by @login_required to redirect anonymous users.
login_manager.login_view = "login"
login_manager.login_message = "Please login to access this page."
login_manager.login_message_category = "info"