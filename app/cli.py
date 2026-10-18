import json
import shutil
import subprocess
from pathlib import Path
import click
from flask import current_app
from sqlalchemy import text
from .extensions import db


def register(app):
    @app.cli.command("doctor")
    @click.option("--live", is_flag=True, help="Make small billable Gemini calls.")
    def doctor(live):
        """Report runtime dependencies without exposing credentials."""
        import platform
        click.echo(f"Python {platform.python_version()}")
        try:
            db.session.execute(text("SELECT 1"))
            db.session.commit()
            click.echo("PostgreSQL: connected")
        except Exception:
            db.session.rollback()
            click.echo("PostgreSQL: unavailable; configure DATABASE_URL and run flask db upgrade")
        for key in ("TESSERACT_CMD", "LIBREOFFICE_CMD"):
            command = current_app.config[key]
            click.echo(f"{key}: {'found' if shutil.which(command) or Path(command).is_file() else 'missing'}")
        try:
            import pytesseract
            pytesseract.pytesseract.tesseract_cmd = current_app.config["TESSERACT_CMD"]
            langs = pytesseract.get_languages(config=f'--tessdata-dir "{current_app.config["TESSDATA_PREFIX"]}"' if current_app.config["TESSDATA_PREFIX"] else "")
            click.echo("OCR languages: " + ", ".join(langs))
        except Exception:
            click.echo("OCR language check: unavailable")
        from .services.vectorstore import store
        click.echo(f"Chroma: {store().client.heartbeat()}")
        click.echo("Gemini key: " + ("configured" if current_app.config["GEMINI_API_KEY"] else "missing"))
        if live:
            from .services.gemini import provider
            client = provider()
            client.embed(["Synthetic learning policy"], "RETRIEVAL_DOCUMENT")
            client.count("Synthetic learning policy")
            client.generate('No evidence. Question: what is the pension rate?', 'Return JSON with status insufficient_evidence, answer, sources as an empty list.')
            client.close()
            click.echo("Gemini embedding, generation token-counting and generation smoke calls passed.")

    @app.cli.command("process")
    @click.argument("version_id")
    def process(version_id):
        from .services.ingestion import process
        click.echo(process(version_id).state)

    @app.cli.command("approve")
    @click.argument("version_id")
    @click.option("--reviewed", is_flag=True, required=True, help="Confirm you inspected extraction against the original.")
    def approve(version_id, reviewed):
        from .services.ingestion import approve
        click.echo(approve(version_id).state)

    @app.cli.command("reconcile")
    @click.option("--repair", is_flag=True)
    def reconcile(repair):
        from .services.ingestion import reconcile
        click.echo(json.dumps(reconcile(repair), indent=2))

    @app.cli.command("delete-document")
    @click.argument("document_id")
    def delete_document(document_id):
        from .services.ingestion import delete_document
        delete_document(document_id)
        click.echo("Deleted.")

    @app.cli.command("retrieve")
    @click.argument("question")
    @click.option("--top-k", type=click.Choice(["1", "3", "5", "10"]), default="5")
    def retrieve(question, top_k):
        from .models import uid
        from .services.rag import ask
        log, hits = ask(question, uid(), int(top_k), retrieval_only=True)
        click.echo(json.dumps(dict(id=log.id, status=log.status, error=log.error, hits=hits, timings=log.timings), ensure_ascii=False, indent=2))

    @app.cli.command("make-samples")
    @click.option("--directory", type=click.Path(path_type=Path), default=Path("evaluation/corpus"))
    def make_samples(directory):
        """Generate synthetic DOCX files and 80 DRAFT labels; never uploads automatically."""
        from .samples import generate
        if directory.exists() and any(directory.iterdir()):
            raise click.ClickException("Choose an empty output directory to preserve fixture hashes.")
        click.echo(str(generate(directory)))

    @app.cli.command("import-samples")
    @click.argument("directory", type=click.Path(exists=True, path_type=Path))
    def import_samples(directory):
        from werkzeug.datastructures import FileStorage
        from .services.ingestion import upload, process
        for file in sorted(directory.glob("*.docx")):
            with file.open("rb") as handle:
                version, duplicate = upload(FileStorage(handle, filename=file.name), file.stem, "mn" if file.stem.endswith("-mn") else "en")
            click.echo(f"{file.name}: {process(version.id).state} ({version.id})")
        click.echo("Review each extraction in Documents, approve it, then process again to index.")

    @app.cli.command("evaluate")
    @click.argument("dataset", type=click.Path(exists=True, path_type=Path))
    @click.option("--split", type=click.Choice(["dev", "test"]), default="dev")
    @click.option("--generate", is_flag=True)
    @click.option("--top-k", type=click.Choice(["1", "3", "5", "10"]), default="5")
    @click.option("--allow-unreviewed", is_flag=True)
    def evaluate(dataset, split, generate, top_k, allow_unreviewed):
        from .evaluation import run_evaluation
        run = run_evaluation(dataset, split, generate, int(top_k), allow_unreviewed)
        click.echo(json.dumps(dict(id=run.id, snapshot=run.snapshot, config=run.config, metrics=run.metrics), ensure_ascii=False, indent=2))

    @app.cli.command("compare-runs")
    @click.argument("first")
    @click.argument("second")
    def compare(first, second):
        from .evaluation import compare_runs
        from .models import EvaluationRun
        a, b = db.session.get(EvaluationRun, first), db.session.get(EvaluationRun, second)
        if not a or not b:
            raise click.ClickException("Unknown run ID")
        click.echo(json.dumps(compare_runs(a, b), ensure_ascii=False, indent=2))

    @app.cli.command("export-run")
    @click.argument("run_id")
    @click.argument("output", type=click.Path(path_type=Path))
    def export(run_id, output):
        from .evaluation import export_run
        from .models import EvaluationRun
        run = db.session.get(EvaluationRun, run_id)
        if not run:
            raise click.ClickException("Unknown run ID")
        if output.exists():
            raise click.ClickException("Choose a new report filename; existing files are preserved.")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(export_run(run), ensure_ascii=False, indent=2), encoding="utf8")
        click.echo(str(output))
