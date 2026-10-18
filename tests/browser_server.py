"""Browser-only test harness: real PostgreSQL/Chroma, explicitly fake Gemini."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import FakeGemini, docx_file
from app import create_app
from app.extensions import db
from app.services.ingestion import upload, process, approve

app = create_app(dict(SQLALCHEMY_DATABASE_URI="postgresql+psycopg://rag:rag@127.0.0.1:55438/rag_browser_test",
    DATA_DIR=Path("instance/browser-check").resolve(), GEMINI_FACTORY=FakeGemini, EMBEDDING_DIMENSIONS=128, TEST_HARNESS=True))
with app.app_context():
    db.create_all()
    version, duplicate = upload(docx_file(), "Synthetic leave policy", "en")
    process(version.id)
    if version.state == "review":
        approve(version.id)
        process(version.id)
    (Path(app.config["DATA_DIR"]) / "version.txt").write_text(version.id)

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5018, use_reloader=False, debug=False)
