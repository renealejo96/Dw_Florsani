from flask_sqlalchemy import SQLAlchemy

# expire_on_commit=False prevents DetachedInstanceError when accessing object attributes after commit
db = SQLAlchemy(session_options={"expire_on_commit": False})
