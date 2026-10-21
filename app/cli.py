import json
import shutil
import subprocess
from pathlib import Path
import click
from flask import current_app
from sqlalchemy import text
from .extensions import db


def register(app):
    @app.cli.group("benchmark-embed")
    def benchmark_embed():
        """Prepare a private Phase 3 smoke test; paid calls require an explicit flag."""

    @benchmark_embed.command("prepare-smoke")
    @click.option("--local-mock", is_flag=True, help="Run Chroma mechanics with deterministic fake vectors; no API calls.")
    @click.option("--confirm-paid", is_flag=True, help="Run the bounded OpenAI smoke test after reviewing the printed estimate.")
    def benchmark_embed_prepare_smoke(local_mock, confirm_paid):
        if local_mock and confirm_paid:
            raise click.ClickException("Choose either --local-mock or --confirm-paid.")
        from .embed_smoke import (prepare_smoke, write_plan, run_isolated_smoke,
            deterministic_mock_vectors)
        from .services.openai_provider import OpenAIProvider
        config = current_app.config
        try:
            plan, chunks_by_doc, query_inputs, evidence = prepare_smoke(Path(config["DATA_DIR"]), config)
            plan_path = write_plan(plan, Path(config["DATA_DIR"]))
            click.echo(json.dumps({"plan": str(plan_path), "plan_id": plan["plan_id"],
                "verified_questions": plan["approved_question_count"],
                "query_variations": plan["verified_query_variation_count"],
                "documents": plan["document_count"], "chunks": plan["document_chunk_count"],
                "model": plan["model"], "dimensions": plan["dimensions"],
                "estimated_input_tokens": plan["total_embedding_input_tokens_estimate"],
                "estimated_cost_usd": plan["estimated_embedding_cost_usd"],
                "max_embedding_requests": plan["max_request_count_estimate"],
                "mode": "local_mock" if local_mock else "paid_openai" if confirm_paid else "plan_only"},
                ensure_ascii=False, indent=2))
            if not local_mock and not confirm_paid:
                click.echo("No embedding API, PostgreSQL, or Chroma call was made. Review the estimate before choosing an execution mode.")
                return
            client = None
            try:
                if local_mock:
                    embedder = lambda texts, task: deterministic_mock_vectors(texts, task, plan["dimensions"])
                else:
                    if not config.get("OPENAI_API_KEY"):
                        raise ValueError("OPENAI_API_KEY is not configured; no API request was sent.")
                    client = OpenAIProvider()
                    embedder = client.embed
                result = run_isolated_smoke(plan, chunks_by_doc, query_inputs,
                    Path(config["DATA_DIR"]), embedder, config["EMBEDDING_BATCH_SIZE"])
                result.update(mode="local_mock" if local_mock else "paid_openai",
                    actual_embedding_usage=(client.usage.get("embedding", {}) if client else {"api_calls": 0}),
                    api_attempts=(client.attempts if client else 0),
                    successful_embedding_operations=(len(client.embedding_request_latency_ms) if client else 0),
                    embedding_operation_latency_ms=(client.embedding_request_latency_ms if client else []),
                    provider_attempt_latency_ms=(client.provider_attempt_latency_ms if client else []),
                    embedding_model_response_ids=(sorted(client.embedding_response_models) if client else []),
                    embedding_model_revision=(client.embedding_model_revision if client else None),
                    result_recorded_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(timespec="seconds"))
                result_path = Path(result["isolated_chroma_path"]).parent / "result.json"
                temporary = result_path.with_suffix(".json.tmp")
                temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
                temporary.replace(result_path)
                click.echo(json.dumps({"result": str(result_path), "indexed_chunks": result["indexed_chunk_count"],
                    "dimensions": result["embedding_dimensions"], "semantic_quality_measured": False,
                    "production_data_touched": False}, ensure_ascii=False, indent=2))
            finally:
                if client:
                    client.close()
        except Exception as exc:
            raise click.ClickException(f"Embedding smoke preparation/run failed: {type(exc).__name__}: {exc}") from exc

    @benchmark_embed.command("tokenization")
    def benchmark_embed_tokenization():
        """Measure the configured tokenizer over frozen A0 chunks; no provider or Chroma access."""
        from .embedding_benchmark import run_tokenization_benchmark
        try:
            result = run_tokenization_benchmark(Path(current_app.config["DATA_DIR"]), current_app.config)
        except Exception as exc:
            raise click.ClickException(f"Tokenization benchmark failed: {type(exc).__name__}: {exc}") from exc
        click.echo(json.dumps(result, ensure_ascii=False, indent=2))

    @benchmark_embed.command("offline-pipeline")
    @click.option("--batch-sizes", default="8,16,32", show_default=True,
        help="Comma-separated local-mock batch sizes for isolated Chroma measurements.")
    def benchmark_embed_offline_pipeline(batch_sizes):
        """Measure mock batching and isolated Chroma persistence; no embedding API calls."""
        from .embedding_benchmark import run_offline_pipeline_benchmark
        try:
            parsed_sizes = tuple(int(value.strip()) for value in batch_sizes.split(",") if value.strip())
            if not parsed_sizes:
                raise ValueError("Provide at least one batch size.")
            result = run_offline_pipeline_benchmark(Path(current_app.config["DATA_DIR"]),
                current_app.config, parsed_sizes)
        except Exception as exc:
            raise click.ClickException(f"Offline Embed pipeline benchmark failed: {type(exc).__name__}: {exc}") from exc
        click.echo(json.dumps(result, ensure_ascii=False, indent=2))

    @app.cli.group("benchmark-store")
    def benchmark_store():
        """Measure isolated synthetic Chroma index fidelity and scale; no API/DB/production access."""

    @benchmark_store.command("run")
    @click.option("--sizes", default="1000,2468,5000,10000", show_default=True,
        help="Comma-separated synthetic vector counts (maximum 10,000).")
    @click.option("--repeats", type=click.IntRange(1, 3), default=2, show_default=True)
    @click.option("--queries", type=click.IntRange(1, 64), default=24, show_default=True)
    @click.option("--seed", type=click.IntRange(min=0), default=6102026, show_default=True)
    def benchmark_store_run(sizes, repeats, queries, seed):
        """Run deterministic 1536-d synthetic index benchmarks in temporary Chroma stores."""
        from .store_benchmark import run_store_benchmark
        try:
            parsed_sizes = tuple(int(value.strip()) for value in sizes.split(",") if value.strip())
            if not parsed_sizes:
                raise ValueError("Provide at least one vector count.")
            result = run_store_benchmark(Path(current_app.config["DATA_DIR"]), parsed_sizes,
                repeats=repeats, query_count=queries, seed=seed)
        except Exception as exc:
            raise click.ClickException(f"Store benchmark stopped/failed: {type(exc).__name__}: {exc}") from exc
        click.echo(json.dumps(result, ensure_ascii=False, indent=2))

    @app.cli.group("benchmark-split")
    def benchmark_split():
        """Freeze and measure Split inputs offline without DB, Chroma, or provider calls."""

    @benchmark_split.command("freeze")
    def benchmark_split_freeze():
        from .split_benchmark import freeze_snapshot
        try:
            click.echo(json.dumps(freeze_snapshot(), ensure_ascii=False, indent=2))
        except Exception as exc:
            raise click.ClickException(f"Split snapshot failed: {type(exc).__name__}: {exc}") from exc

    @benchmark_split.command("run")
    @click.option("--snapshot", "snapshot_id", default=None, help="Previously frozen snapshot ID.")
    def benchmark_split_run(snapshot_id):
        from .split_benchmark import run_baseline
        try:
            click.echo(json.dumps(run_baseline(snapshot_id) if snapshot_id else run_baseline(), ensure_ascii=False, indent=2))
        except Exception as exc:
            raise click.ClickException(f"Split baseline failed: {type(exc).__name__}: {exc}") from exc

    @benchmark_split.command("complexity-probe")
    def benchmark_split_complexity_probe():
        from .split_benchmark import run_complexity_probe
        try:
            click.echo(json.dumps(run_complexity_probe(), ensure_ascii=False, indent=2))
        except Exception as exc:
            raise click.ClickException(f"Synthetic complexity probe failed: {type(exc).__name__}: {exc}") from exc

    @benchmark_split.command("token-count-probe")
    def benchmark_split_token_count_probe():
        from .split_benchmark import run_token_count_probe
        try:
            click.echo(json.dumps(run_token_count_probe(), ensure_ascii=False, indent=2))
        except Exception as exc:
            raise click.ClickException(f"Tokenizer probe failed: {type(exc).__name__}: {exc}") from exc

    @benchmark_split.command("prepare-annotations")
    @click.option("--documents", type=click.IntRange(1, 40), default=20)
    @click.option("--questions-per-document", type=click.IntRange(2, 5), default=4)
    def benchmark_split_prepare_annotations(documents, questions_per_document):
        from .split_experiments import prepare_annotations
        try:
            click.echo(json.dumps(prepare_annotations(documents, questions_per_document), ensure_ascii=False, indent=2))
        except Exception as exc:
            raise click.ClickException(f"Annotation package preparation failed: {type(exc).__name__}: {exc}") from exc

    @benchmark_split.command("run-a1-a3")
    @click.option("--snapshot", "snapshot_id", default=None, help="Previously frozen snapshot ID.")
    def benchmark_split_run_a1_a3(snapshot_id):
        from .split_experiments import run_a1_a3
        try:
            click.echo(json.dumps(run_a1_a3(snapshot_id) if snapshot_id else run_a1_a3(), ensure_ascii=False, indent=2))
        except Exception as exc:
            raise click.ClickException(f"A1-A3 offline comparison failed: {type(exc).__name__}: {exc}") from exc

    @benchmark_split.command("overlap-ablation")
    @click.option("--snapshot", "snapshot_id", default=None, help="Previously frozen snapshot ID.")
    def benchmark_split_overlap_ablation(snapshot_id):
        from .split_experiments import run_overlap_ablation
        try:
            click.echo(json.dumps(run_overlap_ablation(snapshot_id) if snapshot_id else run_overlap_ablation(),
                                  ensure_ascii=False, indent=2))
        except Exception as exc:
            raise click.ClickException(f"Overlap ablation failed: {type(exc).__name__}: {exc}") from exc

    @app.cli.group("annotation-review")
    def annotation_review_cli():
        """Review and export private, human-verified Split evaluation annotations."""

    @annotation_review_cli.command("serve")
    @click.option("--port", type=click.IntRange(1024, 65535), default=8765, show_default=True)
    def annotation_review_serve(port):
        from .annotation_review import load_state
        review_root = Path(current_app.instance_path) / "split-evaluation" / "annotation-review"
        try:
            state = load_state(review_root)
        except Exception as exc:
            raise click.ClickException(f"Private review package бэлэн биш байна: {type(exc).__name__}") from exc
        click.echo(f"Local annotation review: http://127.0.0.1:{port}/annotation-review/ ({len(state['candidates'])} candidates)")
        current_app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False, threaded=True)

    @annotation_review_cli.command("status")
    def annotation_review_status():
        from .annotation_review import readiness_report
        instance = Path(current_app.instance_path)
        root = Path(__file__).resolve().parents[1]
        review_root = instance / "split-evaluation" / "annotation-review"
        try:
            result = readiness_report(review_root, root / "instance" / "uploads", instance / "split-evaluation")
            click.echo(json.dumps(result, ensure_ascii=True, indent=2))
        except Exception as exc:
            raise click.ClickException(f"Review status шалгалт амжилтгүй: {type(exc).__name__}") from exc

    @annotation_review_cli.command("export")
    @click.option("--version", "dataset_version", required=True, help="Immutable export version, e.g. v1.")
    def annotation_review_export(dataset_version):
        from .annotation_review import export_dataset
        instance = Path(current_app.instance_path)
        root = Path(__file__).resolve().parents[1]
        try:
            result = export_dataset(instance / "split-evaluation" / "annotation-review",
                root / "instance" / "uploads", instance / "split-evaluation",
                instance / "split-evaluation" / "datasets", dataset_version)
            click.echo(json.dumps(result, ensure_ascii=True, indent=2))
        except Exception as exc:
            raise click.ClickException(f"Verified dataset export амжилтгүй: {type(exc).__name__}: {exc}") from exc

    @app.cli.group("migrate-indexes")
    def migrate_indexes():
        """Re-embed active document versions into the configured OpenAI embedding space."""

    @migrate_indexes.command("prepare")
    @click.option("--manifest", type=str, help="Resumable JSON manifest path under the application data directory.")
    @click.option("--dry-run", is_flag=True, help="Report eligibility and rough token estimate without API calls or changes.")
    def migrate_prepare(manifest, dry_run):
        from .services.index_migration import prepare
        try:
            result = prepare(manifest, dry_run)
        except Exception as exc:
            raise click.ClickException(str(exc)) from exc
        click.echo(json.dumps(result, ensure_ascii=False, indent=2))

    @migrate_indexes.command("activate")
    @click.option("--manifest", required=True)
    def migrate_activate(manifest):
        from .services.index_migration import activate
        try:
            click.echo(json.dumps(activate(manifest), ensure_ascii=False, indent=2))
        except Exception as exc:
            raise click.ClickException(str(exc)) from exc

    @migrate_indexes.command("rollback")
    @click.option("--manifest", required=True)
    def migrate_rollback(manifest):
        from .services.index_migration import rollback
        try:
            click.echo(json.dumps(rollback(manifest), ensure_ascii=False, indent=2))
        except Exception as exc:
            raise click.ClickException(str(exc)) from exc

    @app.cli.command("doctor")
    @click.option("--live", is_flag=True, help="Make small billable calls to OpenAI.")
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
        selected = "OpenAI"
        click.echo(f"Embedding provider: {selected}")
        click.echo(f"Embedding model/dimensions: {current_app.config['EMBEDDING_MODEL']} / {current_app.config['EMBEDDING_DIMENSIONS']}")
        click.echo(f"Generation model: {current_app.config['GENERATION_MODEL']}")
        click.echo(f"{selected} key: " + ("configured" if current_app.config.get("OPENAI_API_KEY") else "missing"))
        from .services.retrieval import incompatible_active_count
        click.echo(f"Active indexes incompatible with OpenAI embeddings: {incompatible_active_count()}")
        if live:
            from .services.openai_provider import openai_client
            client = openai_client()
            client.embed(["Synthetic learning policy"], "RETRIEVAL_DOCUMENT")
            client.count("Synthetic learning policy")
            client.generate('No evidence. Question: what is the pension rate?', 'Return JSON with status insufficient_evidence, answer, sources as an empty list.')
            client.close()
            click.echo("OpenAI embedding, token counting, and generation smoke calls passed.")

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

    @app.cli.command("backfill-sparse")
    def backfill_sparse():
        """Build or repair sparse indexes for ready document versions."""
        from .services.ingestion import backfill_sparse as run_backfill
        click.echo(json.dumps(run_backfill(), ensure_ascii=False, indent=2))

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

    @app.cli.command("import-folder")
    @click.argument("directory", type=click.Path(exists=True, file_okay=False, path_type=Path))
    @click.option("--language", type=click.Choice(["mn", "en", "mixed"]), default="mixed", show_default=True)
    @click.option("--recursive", is_flag=True, help="Also scan subfolders.")
    def import_folder(directory, language, recursive):
        """Import a folder of PDF/DOCX/DOC files and pause each at extraction review."""
        from werkzeug.datastructures import FileStorage
        from .services.ingestion import upload, process
        iterator = directory.rglob("*") if recursive else directory.glob("*")
        files = sorted(file for file in iterator if file.is_file() and file.suffix.casefold() in (".pdf", ".docx", ".doc"))
        if not files:
            raise click.ClickException("Сонгосон хавтсанд PDF, DOCX эсвэл DOC файл олдсонгүй.")
        failures = []
        for file in files:
            try:
                with file.open("rb") as handle:
                    version, duplicate = upload(FileStorage(handle, filename=file.name), file.stem, language)
                state = version.state if version.state == "review" else process(version.id, require_review=True).state
                marker = " (duplicate)" if duplicate else ""
                click.echo(f"Staged {file.name}: {state} ({version.id}){marker}")
            except Exception as exc:
                failures.append((file, exc))
                click.echo(f"Failed {file.name.encode('ascii', 'backslashreplace').decode('ascii')}: {str(exc).encode('ascii', 'backslashreplace').decode('ascii')}", err=True)
        click.echo("Review extracted text in Documents, approve each version, then process it to build indexes.")
        if failures:
            raise click.ClickException(f"{len(failures)} files failed; inspect the error output.")

    @app.cli.command("approve-all")
    @click.option("--reviewed", is_flag=True, required=True,
        help="Confirm you checked every pending extraction against its original source.")
    @click.option("--dry-run", is_flag=True, help="List review versions without approving or indexing them.")
    def approve_all(reviewed, dry_run):
        """Approve review items and resume failed reviewed builds."""
        from sqlalchemy import select, or_, and_
        from .models import Document, DocumentVersion
        from .services.ingestion import approve, process
        rows = db.session.execute(select(DocumentVersion, Document)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(Document.state == "active", or_(
                DocumentVersion.state == "review",
                and_(DocumentVersion.state == "failed", DocumentVersion.approved_at.is_not(None))))
            .order_by(Document.title, DocumentVersion.version_number)).all()
        if not rows:
            click.echo("Шалгах эсвэл үргэлжлүүлэн боловсруулах хувилбар алга.")
            return
        click.echo(f"Found {len(rows)} versions awaiting review.")
        if dry_run:
            for version, _document in rows:
                click.echo(f"Preview version {version.id}")
            return
        failures = 0
        for version, document in rows:
            try:
                if version.state == "review":
                    approve(version.id)
                result = process(version.id)
                click.echo(f"Ready: {version.id}")
            except Exception as exc:
                failures += 1
                click.echo(f"Failed: {version.id} ({type(exc).__name__})", err=True)
        click.echo(f"Completed: {len(rows) - failures}/{len(rows)} versions succeeded.")
        if failures:
            raise click.ClickException(f"{failures} versions failed; inspect their state in Documents.")

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

    @app.cli.command("benchmark-plan")
    @click.argument("dataset", type=click.Path(exists=True, path_type=Path))
    @click.option("--split", type=click.Choice(["dev", "test"]), default="dev")
    @click.option("--top-k", type=click.Choice(["1", "3", "5", "10"]), default="5")
    def benchmark_plan_command(dataset, split, top_k):
        """Preview the experiment matrix without DB reads or AI requests."""
        from .benchmarking import benchmark_plan
        click.echo(json.dumps(benchmark_plan(dataset, app.config, split, int(top_k)), ensure_ascii=False, indent=2))

    @app.cli.command("benchmark-load")
    @click.option("--manifest", type=click.Path(exists=True, dir_okay=False, path_type=Path),
        default=Path(app.root_path).parent / "evaluation" / "load" / "manifest.json", show_default=True)
    @click.option("--output-dir", type=click.Path(file_okay=False, path_type=Path),
        default=Path(app.root_path).parent / "evaluation" / "load" / "reports", show_default=True)
    def benchmark_load(manifest, output_dir):
        """Run parser-only load benchmarks; does not open PostgreSQL or Chroma."""
        from .load_benchmark import run_load_benchmark
        report = run_load_benchmark(manifest, output_dir, app.config)
        click.echo(json.dumps({"dataset": report["dataset_name"], "summary": report["summary"],
            "reports": [str(output_dir / (report["report_stem"] + suffix)) for suffix in (".json", ".csv", ".md")]},
            ensure_ascii=False, indent=2))

    @app.cli.command("benchmark-load-directory")
    @click.option("--directory", type=click.Path(exists=True, file_okay=False, path_type=Path), default=Path("instance/uploads"), show_default=True)
    @click.option("--output-dir", type=click.Path(file_okay=False, path_type=Path), default=Path("evaluation/load/reports"), show_default=True)
    @click.option("--private-map", type=click.Path(path_type=Path), default=Path("instance/load-evaluation/private-source-map.csv"), show_default=True)
    @click.option("--template", type=click.Path(path_type=Path), default=Path("evaluation/load/ground_truth_templates/real-corpus.template.json"), show_default=True)
    def benchmark_load_directory(directory, output_dir, private_map, template):
        """Assess and benchmark confidential corpus files without DB or Chroma access."""
        from .load_benchmark import run_load_directory
        def progress(done, total, status): click.echo(f"Completed {done}/{total} ({status})")
        report = run_load_directory(directory, output_dir, private_map, template, app.config, progress)
        click.echo(json.dumps({"run_id": report["benchmark_run_id"], "dataset_sha256": report["dataset_sha256"],
            "summary": report["summary"], "inventory": report["inventory"], "report_stem": report["report_stem"]}, ensure_ascii=False, indent=2))

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
