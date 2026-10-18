from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate

# Commit must end the transaction before OCR/API work. Automatic post-commit
# attribute refreshes would otherwise open an implicit read transaction again.
# Freshness-sensitive retrieval explicitly expires/requeries active versions.
db = SQLAlchemy(session_options={"expire_on_commit": False})
migrate = Migrate()
