# Evidence Lab — a learning RAG chatbot

A local Flask application exposing document extraction, chunking, semantic search,
evidence selection, grounded Gemini answers, and evaluation. Mongolian Cyrillic and
English are first-class evaluation groups. Included evaluation fixtures are synthetic;
approved company documents imported locally stay in the ignored `instance/` directory.

## Start locally (Windows / PowerShell)

Python 3.12, PostgreSQL, Tesseract with `eng`/`mon`, and LibreOffice are needed.
The Python lock was resolved on Windows CPython 3.12; it is not a cross-platform lock.

```powershell
uv venv --python 3.12 .venv
uv pip install --python .venv\Scripts\python.exe -r requirements.lock.txt
Copy-Item .env.example .env
```

Set `GEMINI_API_KEY` and a random `FLASK_SECRET_KEY` in `.env`. Do not commit it.
If you leave the secret unset, a local random key is generated in `instance/session.key`.
No API key is included or simulated in the application.

Choose one PostgreSQL option:

```powershell
# Docker option, if Docker Desktop is running
docker compose up -d

# Or installed PostgreSQL binaries; isolated cluster, no existing service changes
.\scripts\start-local-postgres.ps1
```

Both use port 55438. Do not start both on the same port. The helper creates `rag`
and disposable `rag_test` databases. Its trust authentication is for loopback-only
learning. To stop that cluster, use `pg_ctl -D .\instance\pg-test -w stop` with your
PostgreSQL bin directory on PATH. Docker uses the example local credentials.

Native tools can be installed with:

```powershell
winget install --id UB-Mannheim.TesseractOCR --exact --source winget
winget install --id TheDocumentFoundation.LibreOffice --exact --source winget
.\scripts\setup-ocr.ps1
```

The application detects standard Windows installation paths. Otherwise configure
`TESSERACT_CMD`, `TESSDATA_PREFIX`, and `LIBREOFFICE_CMD`. Language files are kept in
the workspace instance directory, not copied into the global OCR installation.

```powershell
.venv\Scripts\python.exe -m flask --app app db upgrade
.venv\Scripts\python.exe -m flask --app app doctor
.venv\Scripts\python.exe -m flask --app app doctor --live
.venv\Scripts\python.exe -m flask --app app run --host 127.0.0.1 --port 5000 --no-reload
```

Open <http://127.0.0.1:5000>. `doctor --live` makes small API requests and can incur
charges. It verifies embedding, generation token counting, and generation access.
The configured models are explicit, not moving aliases. If account/model access
changes, update configuration and rebuild all active indexes when the embedding
space changes. The embedding model does not support `count_tokens` (confirmed in
[the SDK maintainer issue](https://github.com/googleapis/python-genai/issues/1541)).
The baseline therefore splits embedding chunks at a conservative **1,900 UTF-8 byte
safety cap**, recorded in the index fingerprint. This is not an exact token count
or a claim about an undocumented tokenizer. Overlong questions are rejected with
a shortening hint; text is never silently truncated. Generation uses its own
supported token-counting endpoint. Live smoke tests must still verify provider behavior.

## The pipeline and ownership

```mermaid
flowchart LR
  Upload --> Parse[Native parsing / DOC conversion / OCR]
  Parse --> Review[Inspect extraction]
  Review --> Chunk[Character chunks]
  Chunk --> Embed[Gemini document embeddings]
  Embed --> Chroma[(Chroma)]
  Chroma --> Verify[Verify exact IDs]
  Verify --> PG[(PostgreSQL active version)]
  Question --> QE[Gemini query embedding]
  PG --> Retrieve[Filter active versions]
  QE --> Retrieve
  Chroma --> Retrieve
  Retrieve --> Context[Budgeted evidence context]
  Context --> Answer[Gemini answer + citation validation]
  Answer --> Trace[Query history / evaluation]
```

PostgreSQL owns metadata, versions, status, query traces, and evaluation. Chroma owns
chunk text, scalar metadata and vectors. Filesystem artifacts hold originals,
extractions, and chunk manifests. Full chunk text is not duplicated in query logs.
History inspection reloads the indexed chunk; deleted sources are marked unavailable.

`app/services/parsing.py` produces ordered blocks with provenance. `chunking.py`
preserves PDF page and DOCX heading boundaries. `gemini.py` owns remote calls,
retries, vector normalization and token counts. `ingestion.py` owns stage → verify
→ activate and recovery. `retrieval.py` is independent of generation. `rag.py`
owns context selection and citation validation. Routes remain thin, with Jinja2 and
plain JavaScript for the interface. `evaluation.py` contains readable metrics.

### Document workflow

1. Upload a PDF/DOCX/DOC. Only approved or synthetic files belong here.
2. Select **Process**. Validation and extraction run in a bounded subprocess.
3. Inspect page/block text against the original. OCR, DOC conversion, DOCX and
   table warnings require explicit review. Blank pages can be accepted during review.
4. Choose native or OCR text for pages with both representations. Approve extraction.
5. Select **Process** again to embed, upsert, verify, and activate.
6. Ask a question; expand its evidence and inspect the query trace.
7. Upload updates through that document's version page or create a reindex build.

Sparse pages trigger OCR automatically; mixed pages with a text layer can escape
that heuristic. Use the comma-separated OCR page override on **Create reindex build**.
PDF citations use physical pages. DOCX/DOC citations use headings and block numbers;
no renderer-dependent Word page numbers are fabricated. OCR is not full layout or
table understanding; compare financial amounts, exceptions and negations carefully.
Text boxes and images embedded inside DOCX are flagged for review, not OCRed in v1.

Exact byte hashes detect duplicates. Reindexing creates a new immutable build and
reuses original bytes. The previous active version remains searchable until the new
build verifies. One cross-process filesystem lock serializes document mutations.
There is no scheduler or worker queue. Long tasks block that request; closing its
browser page does not cancel them. Use the CLI for longer processing, stopping the
web server first for vector-mutating CLI commands: embedded Chroma is a single-process
prototype, not a shared vector service. Evaluation holds the document-mutation lock
for its full run so the active corpus cannot change between questions.

```powershell
.venv\Scripts\python.exe -m flask --app app process VERSION_UUID
.venv\Scripts\python.exe -m flask --app app approve VERSION_UUID --reviewed
.venv\Scripts\python.exe -m flask --app app reconcile
.venv\Scripts\python.exe -m flask --app app reconcile --repair
.venv\Scripts\python.exe -m flask --app app delete-document DOCUMENT_UUID
```

Reconciliation is read-only unless `--repair` is supplied. Repair marks interrupted
or incomplete builds failed, removes orphan vectors and invalidates incomplete
active pointers. It never activates a build automatically. Retry processing to
rebuild; retry deletion separately for documents left in `deleting` state.
There is no distributed transaction between PostgreSQL and Chroma.

Deletion excludes the document first, removes vectors from all of its collections,
then removes unreferenced artifacts. A cleanup failure leaves a retriable deleting
state. Metadata tombstones remain for historical query/evaluation records.

## Baseline configuration

| Setting | Default |
|---|---|
| Embedding / generation | `gemini-embedding-001` / `gemini-3.5-flash-lite` |
| Vectors | 768 dimensions, L2 normalization, cosine distance |
| Embedding tasks | `RETRIEVAL_DOCUMENT` / `RETRIEVAL_QUERY` |
| Chunks / overlap | 1,800 / 250 characters |
| Embedding input guard | Split further at 1,900 UTF-8 bytes; not an exact token count |
| Retrieval | Semantic top-k 5; controls for 1, 3, 5, 10 |
| Corpus filters | Active versions; optional document and language |
| Context / history / total input | 8,000 / 2,000 / 12,000 generation-model tokens |
| History / output | Last four question-answer turns / up to 1,000 output tokens |
| Per-call / chat deadline | 30 / 90 seconds |
| Upload / PDF / DOCX expanded | 20 MiB / 100 pages / 100 MiB |
| OCR / conversion / processing | 60 seconds per OCR call / 120 / 900 seconds |

API retries honor HTTP `Retry-After` and Google's structured `google.rpc.RetryInfo`
cooldowns, within the operation deadline. Bulk indexing can therefore take longer
on an account with a low embedding quota; verified versions remain available.

Queries search all evidence languages unless filtered. No automatic translation,
rewriting, reranking or BM25 is applied. History is conversational background, not
policy evidence; retrieval embeds only the current question. An ambiguous follow-up
should receive clarification rather than invented policy facts.

One collection is created per index-configuration fingerprint. Chunk IDs derive
from build UUID, configuration and ordinal. Staged vectors are excluded using active
version filters. Queries fail explicitly if an active embedding space differs from
configuration. Do not mix models merely because their vector dimensions match.
For this small, synchronous local corpus, HNSW batch and sync thresholds are both
1 so every mutation is persisted before the processing command exits. A separate
process regression verifies similarity search after restart, including a one-chunk
collection. This favors durability over bulk indexing throughput.

## Evaluation

`evaluation/corpus` contains 16 synthetic DOCX documents and 80 **draft** questions:
20 for each MN→MN, EN→EN, MN→EN and EN→MN group; 15 answerable and 5 unanswerable
per group. Each group has 10 development and 10 held-out examples. Related
translations stay in the same split. Labels require bilingual human review; no
generated dataset is described as reviewed. Questions cover direct facts, exceptions,
numbers, paraphrases, table lookups, multi-passage and multi-document synthesis,
and absent policies.

```powershell
# Existing committed samples: import (does not approve extraction automatically)
.venv\Scripts\python.exe -m flask --app app import-samples evaluation/corpus

# To generate a separate fresh corpus, choose an EMPTY directory
.venv\Scripts\python.exe -m flask --app app make-samples --directory instance/new-corpus

# After reviewing and indexing documents, review the JSONL question labels.
# Set review_status to reviewed only after a human confirms the question/gold pair.
.venv\Scripts\python.exe -m flask --app app evaluate evaluation/corpus/questions.jsonl --split dev
.venv\Scripts\python.exe -m flask --app app evaluate evaluation/corpus/questions.jsonl --split dev --generate
```

`--allow-unreviewed` permits an engineering smoke run and records that limitation
in the snapshot. It does not waive human acceptance. Open **Evaluation** for metrics,
gold evidence and per-answer human review. Leave inapplicable scores blank. Scores:
0 wrong/unsupported, 1 partial, 2 correct/supported. Save a reviewer identifier and
failure diagnosis. Query histories are local-session scoped; evaluation review is
a separate local workspace view.

Gold labels use immutable source hashes or explicit version IDs plus page/block
locations and evidence excerpts. An excerpt must occur in a retrieved chunk; a
block overlap alone is insufficient. Relabel ambiguous boundaries when experimenting.
Repeated overlapping chunks count the same gold evidence unit once. Precision@k
counts retrieved positions contributing new relevant evidence, divided by k; recall
counts unique gold units recovered. Document metrics collapse document IDs. MRR uses
the first relevant rank. Missing results occupy nonrelevant positions. Retrieval
metrics for unanswerable questions are null, not zero.

Run snapshots include sources, index configuration hashes, dataset revision,
package versions, prompt settings and language groups. Retrieval-only runs never
generate answers. Answer correctness, faithfulness and citation support remain
human-reviewed; syntactically valid citations cannot prove factual support.
Abstention status matching is an automatic indicator, not a complete human judgment.

```powershell
.venv\Scripts\python.exe -m flask --app app compare-runs RUN_A RUN_B
.venv\Scripts\python.exe -m flask --app app export-run RUN_A instance/reports/baseline.json
```

Compare the same corpus, labels and split, changing one setting. Inspect per-question
regressions. Alternate execution order, distinguish warm/cold runs, and repeat
generation when variability matters. Keep held-out questions untouched during tuning.

Initial acceptance targets are Recall@5 ≥ .80 in each language direction, ≥80%
correct/complete held-out answers, ≥90% correct abstention, 100% citation-ID validity,
and ≥90% human-reviewed citation support. Publish denominators. These targets have
not been demonstrated by mocked API tests.

### Experiments after the real baseline

| Change | Hypothesis | Measure |
|---|---|---|
| 900/1800/3600 character chunks, constant overlap ratio | Specificity versus retained qualifications | Recall/MRR, correctness, context usage |
| 0/250/500 overlap, fixed size | Boundary recovery versus redundancy | Boundary recall, duplicate hits, index size |
| top-k 1/3/5/10 | More evidence may add distraction | Recall, precision, correctness, latency |
| Another multilingual embedding model | Better language alignment may improve retrieval | Per-direction metrics, cost; separate index |
| Matched Mongolian/English questions | Parsing and alignment failures differ | Same-language versus cross-language errors |
| BM25 + reciprocal-rank fusion | Exact names/codes benefit from lexical search | Identifier recall and latency |
| Rerank top-20 to top-5 | Better candidate ordering | Candidate recall, MRR, added latency |
| One recorded query rewrite | Resolve phrasing without inventing intent | Recall and meaning preservation |
| Heading-aware chunks/title enrichment | Section context can disambiguate rules | Recall, faithfulness, index size |
| 2k/4k/8k context budget, fixed retrieval | More evidence can help or dilute | Correctness, citation coverage, token use |

The latter experimental algorithms are deliberately not implemented in the baseline.
Select and verify their dependencies when each experiment begins.

## Testing and learning checkpoints

```powershell
# The fixture refuses database names that do not begin rag_test.
$env:TEST_DATABASE_URL='postgresql+psycopg://rag:rag@127.0.0.1:55438/rag_test'
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m flask --app app db check
```

Tests use actual PostgreSQL and Chroma, deterministic API doubles, and a real SDK
mock transport that checks request serialization without network calls. Native OCR
and DOC conversion tests run when their tools exist and otherwise report skips.
API doubles never establish Gemini model quality. Integration tests rebuild tables
only inside the explicitly disposable `rag_test*` database. An opt-in live smoke is
`doctor --live`; no real-key tests run automatically.

| Phase | Inspect and learn | Validate | Explain independently |
|---|---|---|---|
| 1 Fundamentals | DB/vector/LLM boundaries, factory, config | doctor, migrations | Why separate retrieval and generation? |
| 2 Parsing | Original versus ordered extraction, OCR, tables | native and bilingual fixtures | Can visible facts be missing from text? |
| 3 Chunks | Adjacent windows, offsets, vector norms | coverage, dimensions, retry | What does overlap buy and cost? |
| 4 Retrieval | Top ten hits before any answer | rank metrics and active filters | Why is similarity not relevance? |
| 5 Generation | Selected evidence versus actual claims | invalid citations, errors, abstention | Why can correct retrieval yield a wrong answer? |
| 6 Interface | Upload-to-citation browser flow | history, escaping, loading/errors | What belongs in session versus server storage? |
| 7 Evaluation | Gold labels and failed questions | hand-calculated metrics, language groups | Was the change a real improvement? |
| 8 Experiments | Per-question A/B divergence | fixed corpus, one variable | Which stage explains the gain or regression? |

Common debugging order: extraction → boundaries → active/index compatibility →
ranking → selected prompt evidence → answer claims → citation support. Inspect
stage timers for latency and reconciliation for inconsistent state. Avoid fixing
a parsing problem with a longer prompt or interpreting a smooth answer as evidence.

## Acceptance boundaries

Engineering checks, live Gemini checks, rendered browser checks, dataset review,
and human answer acceptance are separate. See `validation.json` for recorded checks.
The user learning/explanation milestones require the learner's own demonstration.
The application does not claim to satisfy those automatically.

This is not a public service. There is no authentication, authorization, enterprise
audit infrastructure, scheduler, distributed queue, deployment pipeline or production
integration. Do not upload company documents without approval. Local CSRF and host
checks help the local workflow but are not enterprise access control.

## Official learning resources

- [Flask factories](https://flask.palletsprojects.com/en/stable/patterns/appfactories/)
- [Flask-SQLAlchemy](https://flask-sqlalchemy.palletsprojects.com/en/stable/quickstart/)
- [Flask-Migrate](https://flask-migrate.readthedocs.io/en/latest/)
- [Gemini SDK](https://github.com/googleapis/python-genai)
- [Gemini embeddings](https://ai.google.dev/gemini-api/docs/embeddings)
- [Gemini tokens](https://ai.google.dev/gemini-api/docs/tokens)
- [Gemini structured output](https://ai.google.dev/gemini-api/docs/structured-output)
- [Chroma collection configuration](https://docs.trychroma.com/docs/collections/configure)
- [pypdf extraction](https://pypdf.readthedocs.io/en/stable/user/extract-text.html)
- [python-docx](https://python-docx.readthedocs.io/en/latest/api/document.html)
- [Tesseract languages](https://tesseract-ocr.github.io/tessdoc/Data-Files-in-different-versions.html)
- [LibreOffice command-line conversion](https://help.libreoffice.org/latest/en-US/text/shared/guide/start_parameters.html)
- [Information retrieval evaluation](https://nlp.stanford.edu/IR-book/html/htmledition/evaluation-in-information-retrieval-1.html)
