# Phase 1 — Load ба Parsing судалгааны gap analysis

## Executive summary

Chapter 3 is a research discussion and proposed design, not a binding implementation specification. The repository already has a bounded, format-specific parser; an explicit document/version lifecycle; review before indexing; ordered parser blocks; character-based chunking; and source/version metadata for retrieval citations. Those capabilities support starting a controlled Split experiment.

The real-corpus run measured **154 files: 151 parser successes and 3 `empty_document` failures**. It did **not** measure extraction accuracy: no manually verified references were supplied, so CER, heading recall, clause recall, and table accuracy remain **N/A**. The real directory contained DOCX and DOC only—no PDF—so the PDF reading-order and real scanned-page behavior remain outside that run.

Two parser-to-Split losses are now directly confirmed by targeted tests: (1) DOCX automatic list numbering and heading levels are absent from the block contract; (2) tables are flattened into text and merged-cell relationships are lost. They can affect clause context, table interpretation, retrieval, and the precision of citations. These findings do not prevent a Split algorithm comparison on the current input contract, but that comparison must not be described as a full-structure or production-quality evaluation.

**Final decision: READY WITH DOCUMENTED LIMITATIONS.** Start Split evaluation against the existing parser output. Keep the Load limitations below visible, and do not infer extraction completeness or correctness from `success` or the observed clause-marker counts.

## Scope and evidence reviewed

- Research source: `Advanced_RAG_Chapter3_Academic_Revised.docx`, sections 3.1–3.10. Its architecture recommendations and research techniques are treated as proposals/evidence, not executable instructions or mandatory requirements.
- Parser and lifecycle: `app/services/parsing.py`, `app/services/parse_worker.py`, `app/services/ingestion.py`, `app/models.py`.
- Split interface and citations: `app/services/chunking.py`, `app/services/rag.py`, `app/services/preview.py`.
- Tests: `tests/test_core.py`, `tests/test_native.py`, `tests/test_load_benchmark.py`, `tests/test_integration.py`, and new audit sentinels in `tests/test_load_split_interface.py`.
- Baseline evidence: synthetic `evaluation/load/reports/load-20261021T040121Z.{md,json,csv}` and real-corpus `evaluation/load/reports/real-load-20261021T042112Z.{md,json,csv}`. Selected-sample reference template: `evaluation/load/ground_truth_templates/real-corpus.template.json`.

The real run ID is `e948b4c9-c855-42fe-b939-d635364b9a2c`; corpus SHA-256 is `15b24382aeba13e943382370f5a4bfb4ede50b4033805006bad692d23bf3d56a`. The report records the parser revision/configuration and Windows/Python environment. It was parser-only and serial: `database_connections=0`, `vector_store_calls=0`. It is not a concurrency or end-to-end upload/indexing benchmark.

## Research-to-implementation traceability matrix

| Chapter | Research capability discussed | Current implementation and applicability | Verification and evidence | Status and downstream effect |
|---|---|---|---|---|
| **3.1 Ingestion pipeline** | Validate, detect/route, parse, check quality, retain provenance, isolate errors, make work repeatable. | Flask upload validates extension/claimed MIME/signature and size; parser runs in a bounded subprocess; parser warnings gate manual review before indexing. PostgreSQL tracks document/version state; Chroma writes are verified before activation. There is no dedicated asynchronous job queue. | Upload and parser checks in `tests/test_load_benchmark.py`; lifecycle behavior has integration coverage in `tests/test_integration.py`. Real benchmark confirms parser outcomes only. | Core staged pipeline is **Implemented and verified** for exercised paths; async worker/queue is **Missing**, but not required for a controlled Split experiment. The real run had 3 failed files and no quality-pass metric. |
| **3.2 PDF, DOC, DOCX internals** | Format-specific validation and parsing; distinguish PDF page geometry, legacy DOC binary conversion, and DOCX package structure. | PDF signature/encryption/page-limit validation; DOC signature validation then isolated LibreOffice conversion; DOCX ZIP/document-part/expanded-size/VBA checks then python-docx. This is application-appropriate and does not require Apache Tika. | `tests/test_core.py` corrupt DOCX/renamed DOC and simple DOCX test; `tests/test_native.py` native PDF, encryption/page cap, and real DOC conversion. Legacy conversion test passed in targeted run. Real corpus: DOCX 143/146, DOC 8/8 success. No real PDF. | Format handling is **Implemented and verified** for tested structural/validation cases. DOC conversion **fidelity is Partially implemented / not quality-verified**: 8 real conversions succeeded, but there is no paired source/reference fidelity score. |
| **3.3 Text extraction and layout-aware parsing** | Extract text with reading order, layout blocks, headings, columns, tables, page headers/footers and source locations where recoverable. | DOCX uses ordered top-level paragraph/table iteration. PDF uses `pypdf` page `extract_text()` as returned; there is no column segmentation or parser-level coordinate sort. The preview subsystem can map extracted text to rendered pages, but that mapping is separate from the main parser blocks. | Simple order/table and one-page native PDF tests exist. No real PDF in the corpus and no multi-column reading-order reference test. Real DOCX corpus reports 8 multi-column sections but does not measure reading order quality. | Basic ordered extraction is **Implemented and verified**; layout-aware reconstruction is **Partially implemented**. Multi-column order, running headers/footers, and reading order remain **untested**. Bad order can combine unrelated clauses before Split and weaken retrieval/citations. |
| **3.4 OCR for scanned documents** | Detect image-only/mixed pages; OCR only where needed; select native/OCR output; evaluate language-specific character and clause accuracy. | PDF OCR is page-level when native text is sparse, replacement characters exceed the threshold, or a page is forced. Both candidates and selected method are stored; review can switch native/OCR. Configured languages are `mon+eng`. DOCX embedded images are not OCRed by the parser. Preview offers manual region OCR proposals, not automatic DOCX image extraction. | `tests/test_native.py::test_real_bilingual_ocr` is **skipped: Tesseract missing**. OCR path has no accuracy reference. Real corpus has no PDF, so scanned/mixed detection was not exercised there. | PDF OCR path is **Implemented but unverified in this environment for OCR execution**; accuracy is **untested**. DOCX image OCR is **Missing in the main parser**. No new OCR engine is justified absent measured reference error. |
| **3.5 Tables, headings, clauses, lists** | Preserve heading hierarchy, clause IDs/parent relations, nested list semantics and table row/cell/header/merge relations for structure-aware chunking. | Parser emits `paragraph`, `table`, or PDF `page` blocks with sequential `block`, text and one current heading string. Inline/typed clause numbers remain in text. DOCX style level, numbering IDs/list labels, parent-child hierarchy and table cell graph are not emitted. Tables become rows joined with ` | `; merged cells may be repeated. Chunker respects page/heading string boundaries and flags partial tables but does not know clause or cell structure. | Existing simple table test verifies text order only. New tests in `tests/test_load_split_interface.py` confirm auto-number labels/heading levels are absent and merged-cell output is flattened; direct typed clause number remains. Real corpus reports 7,843 line-start clause markers in 148 files, but this is not recall. | Plain-text clause preservation is **Partially implemented**; hierarchy/list/cell semantics are **Missing from the interface**. This is the clearest Split/retrieval/citation risk; it is documented, not silently presumed solved. |
| **3.6 Unicode and normalization** | Canonicalize Unicode safely while preserving meaningful Mongolian letters, numbers, punctuation, and source form. | Parser applies NFC, removes NUL, collapses horizontal whitespace and trims lines. It does not casefold or transliterate parser output. Original file bytes are retained. There is no separately named text-normalization revision; `normalization: l2-v1` in indexing config is vector normalization, not text normalization. | `tests/test_core.py::test_normalization_preserves_mongolian` checks Ө/Ү, whitespace and NUL. No CER or composed/decomposed/corrupt-font corpus test. | Basic normalization is **Implemented and verified**; semantic safety across real OCR/PDF encodings is **Partially implemented / untested**. Original bytes allow reprocessing; offsets do not map back through whitespace/NUL transformations. |
| **3.7 Metadata and provenance** | Retain document/version/parser identity, element order, page/source spans and traceable evidence for citations. | Document/version IDs, SHA-256, parser config, parser revision and extraction artifact are stored. Blocks have order and type; PDF blocks have page. Chunk metadata carries block range, heading, local character offsets and PDF page bounds where present. Ingestion adds `document_id`, `version_id`, language and config hash; RAG citations use version/title/filename/location. Preview may provide region coordinates separately. No canonical `section_path`, element ID, PDF text bbox, or original-source character span is emitted. | Code-path inspection in `models.py`, `ingestion.py`, `chunking.py`, `rag.py`; chunk page/offset tests in `tests/test_core.py`. Preview mapping has separate tests in `tests/test_native.py`. No end-to-end citation-to-original-span accuracy benchmark. | Version-level provenance is **Implemented and verified**; element/source-span provenance is **Partially implemented**. DOCX/DOC parser has no page anchors. Citation can name a document/version and block/heading but not reliably identify a clause or exact original span. |
| **3.8 Duplicate detection and versioning** | Distinguish byte-identical copies, semantically similar content, and genuine policy revisions. | SHA-256 detects exact-byte duplicate uploads under the same indexing config; document version numbers and active-version pointer preserve revisions. Reindexing creates a new version/config. There is no normalized-content or near-duplicate (MinHash/Jaccard) detector. | Exact duplicate behavior is tested in `tests/test_load_benchmark.py` and `tests/test_integration.py`; version lifecycle covered by integration tests. Real benchmark does not evaluate semantic duplicates. | Exact duplicate and version lifecycle are **Implemented and verified**. Near-duplicate detection is **Not applicable as a baseline requirement**; do not add it without a demonstrated duplicate problem. Different-byte revisions should remain distinct versions. |
| **3.9 Incremental ingestion and error handling** | Cache unchanged work, make processing idempotent, classify failure, retry safely, and avoid partially active indexes. | Extraction cache keys include source hash/parser/OCR/native-tool fingerprints. `ready` processing returns early; chunk IDs are deterministic for version/config/ordinal. Parser timeouts and failed states exist; Chroma and sparse IDs are checked before activation. Retry is an explicit reprocess path, not a queued backoff policy. | Error/timeout and lifecycle cases exist across `test_native.py`, `test_load_benchmark.py`, and DB-backed `test_integration.py`. Current targeted test command did not run the DB integration suite. Real report shows three parser failures, no retries. | Cache/idempotency/failure isolation are **Implemented**; the DB/vector transaction path is **Implemented but not reverified in this review**. Automated queue/retry is **Missing**, an operations concern, not a Split prerequisite. |
| **3.10 Parsing complexity and performance** | Measure latency distribution, throughput, CPU/memory and scale behavior on representative formats; avoid asserting generic complexity as measured fact. | A reproducible parser-only runner records environment/config, dataset hash, outcomes, P50/P95, throughput, worker CPU/RSS and structural counts. It is single-process/sequential and excludes HTTP upload, DB, Chroma, embedding and concurrent load. | Real report `real-load-20261021T042112Z`: 154 files, 151 success, P50 973.52 ms, P95 3,869.25 ms, 0.820 files/s, worker peak RSS 183,857,152 bytes; synthetic report is a separate 16-DOCX dataset. | Serial local performance is **Implemented and measured**; scalability/concurrency is **Untested**. The two datasets are not condition-matched for accuracy or a direct latency comparison. |

## Critical Load quality checks

| Check | Evidence-backed conclusion | Current measurement adequacy |
|---|---|---|
| PDF/DOC/DOCX format correctness | Signature and structural checks exist; DOC conversion is isolated. DOCX MIME/signature checks are not a full forensic or malware scan. | Unit tests catch malformed/renamed inputs; real run contains only DOCX/DOC. Format correctness is not equivalent to content fidelity. |
| DOC conversion fidelity | Real DOCs all parsed successfully; one synthetic DOCX→DOC→parser round-trip checks a Cyrillic phrase. | No per-document paired references or fidelity metric; **not measured**. |
| PDF reading order / columns | Parser consumes `pypdf` output order without layout reconstruction. | Native one-page PDF is tested; actual/multi-column PDF is absent from real corpus; **not measured**. |
| Heading / numbered clauses / nested lists | Explicit numbers in paragraph text survive. Automatic Word numbering and heading level/parent relationships do not appear in block schema. | 148 files have line-start markers, 7,843 total; this is an extraction-side count, not expected-vs-found recall. Heading recall and nested list accuracy **N/A**. |
| Tables and merged cells | Row text is emitted as `cell | cell`; cells/header associations and merge map are not output. Real run found 11 package-table vs extracted-table-count mismatches. | No cell-level or header accuracy ground truth; **N/A**. Mismatch count is a triage signal, not table error rate. |
| Page/section provenance | PDF page number survives. DOC/DOCX parser outputs do not contain page; heading is a single string, not a section path. Preview mapping is separate. | PDF page-level unit check exists; real DOCX/DOC page count is unavailable; end-to-end source citation fidelity **not measured**. |
| Mongolian Cyrillic/Unicode | NFC and whitespace cleanup preserve Cyrillic in unit test; OCR config includes `mon+eng`. | One normalization unit test; real OCR unavailable because Tesseract is missing; corpus has no CER reference. |
| OCR selection/accuracy | Sparse/corrupt/forced page selection and human native/OCR choice are implemented. | OCR runtime test skipped; no OCR quality/selection false-positive/false-negative rates. |
| Safe normalization | Parser preserves source file, normalizes extracted text; removes NUL and collapses whitespace. | Specific behavior is unit tested; exact character/source-span reversibility is not maintained and no text-normalization revision is separately stored. |
| Exact duplicates/document versions | Exact SHA-256/config duplicate matching, numbered versions, and active-version tracking exist. | Duplicate path tested; semantic duplicates/content-equivalent exports are deliberately not measured. |
| Parser→Split compatibility | Parser blocks are ordered and typed; chunker consumes blocks, groups by heading/page, records block and character ranges, and flags partial table chunks. | New contract sentinels verify current behavior. Heading/list/table semantics are lost before chunking; see below. |

## Load-to-Split interface validation

### Fields passed and retained

- **Document/version IDs:** `DocumentVersion` owns `document_id`; after `chunk()`, `ingestion.process()` adds `document_id`, `version_id`, language and config hash to each chunk. Chunk IDs are deterministic within a version/config/order.
- **Ordered elements and types:** parser block sequence is numbered; `kind` distinguishes paragraph/table/page; `method` distinguishes native/OCR. Chunk metadata keeps block start/end and extraction method.
- **Heading:** current heading string propagates into chunk metadata and forms a grouping boundary.
- **Pages:** PDF page numbers propagate to chunk page start/end. DOC/DOCX do not receive page values from main parser.
- **Evidence anchors:** chunks carry local `char_start/char_end`, `block_start/block_end`, heading and page where available. OCR preview regions can be carried as `source_regions_json` when present.
- **Parser identity:** extraction artifact has `ordered-blocks-v1`; version config and config hash include parser settings. Chunk metadata has the config hash, not a direct parser/normalization-version field.

### Confirmed irreversible losses before Split

1. Heading level and parent path are collapsed into one heading string.
2. Word automatic list number and nesting/numbering identity are omitted. Typed inline clause numbers remain, but are not parsed into stable `clause_id`/parent fields.
3. Table rows are flattened to a delimited string; merged-cell repetition is possible and cell/header/merge relations are discarded. Chunking may split a long table and marks only `partial_table`.
4. DOCX/DOC source page and original text spans are absent. PDF text coordinates are not part of parser block output. Chunk offsets refer to the normalized, joined text group, not original byte/XML/PDF coordinates.
5. NFC/whitespace/NUL normalization is not reversible from offsets alone. The original file remains stored, so source re-render/reparse is possible.

These are **P1 interface limitations**, not proof that every real policy is wrong. The 7,843 observed markers show many clauses are typed into text, but do not establish completeness. They do not block running a comparative Split benchmark on current parser blocks; they do block claiming that Split was evaluated with preserved heading hierarchy, list semantics, cell structure, or exact source spans.

## Targeted missing tests and results

Added `tests/test_load_split_interface.py` because existing tests checked a simple DOCX paragraph/table and generic chunk offsets, but did not expose automatic list numbering, heading level, merged-cell mapping, or the concrete parser→chunk metadata contract. The tests are audit sentinels: they assert and document current behavior; they do not change the parser or claim a quality score.

Executed:

```text
pytest tests/test_load_split_interface.py tests/test_core.py tests/test_native.py tests/test_load_benchmark.py
43 passed, 1 skipped
```

The skipped test was bilingual OCR: Tesseract is unavailable. A separate DOC conversion check passed (`test_real_doc_conversion`). No parser configuration was changed. No full real-corpus benchmark was repeated; its coverage was adequate for serial outcomes/performance, and it had no ground truth to justify an accuracy rerun. No PostgreSQL or ChromaDB production data was touched.

## Confirmed defects, risks and priorities

### P0 — Blocks reliable Split evaluation

**None confirmed.** Current ordered blocks reach the character chunker; a controlled Split experiment can compare chunk sizes/overlap and retrieval-facing behavior using this contract.

### P1 — Important accuracy or reliability issue

1. **Heading/list/table structure is lost at the parser→Split boundary.** Confirmed by new tests and parser/chunker code. Benefit of a future structured representation would be better clause grouping, table-aware chunking, retrieval context and source citations. First measure impact in representative policies; do not implement a larger schema blindly.
2. **Three real DOCX files produce no usable text.** Real run IDs: `DOC0032`, `DOC0087`, `DOC0122`; all are classified `empty_document` and each has package-level tables. Ground-truth inspection should determine whether these are table-only policies or malformed/unsupported content. Until then they remain failed Load inputs and must not enter Split as if empty content were valid.
3. **Eleven table-count mismatches and broad image/textbox presence are unverified completeness risks.** 144 files have tables, 137 embedded media, 26 text boxes. Main DOCX parser does not OCR images or parse text boxes. The frequency is evidence for prioritizing manual review, not evidence those objects contain policy text.
4. **No real-corpus extraction ground truth.** The selected 20-document template is blank. CER, heading/clause recall and table accuracy are N/A. This blocks quality claims, not a first Split experiment.

### P2 — Useful evaluation / optimization

1. **PDF reading order and scanned-page behavior:** run a small, manually verified multi-column/mixed-PDF fixture if PDFs are expected in Phase 2. Current real corpus has zero PDFs; existing OCR runtime test is skipped because Tesseract is missing.
2. **DOC conversion time:** real DOC P50/P95 is 4,093/4,925 ms vs DOCX 968/1,113 ms; 8/8 DOC parse successes. Profile only if this threatens a stated latency target. Current parser-only benchmark excludes LibreOffice child CPU/RSS and whole upload/index latency.
3. **Provenance granularity:** document/version/block/page anchors work to varying levels, but paragraph/cell IDs and original spans are not passed through. Add only if Split or citation evaluation demonstrates that block/heading anchors are insufficient.
4. **Scalability:** current throughput is one sequential local parser run. Concurrency, queue saturation and full indexing throughput are not measured and should not be inferred from 0.820 files/s.

### P3 — Optional advanced enhancements

Do not add ML layout models, a new OCR engine, Apache Tika, W3C PROV storage, semantic near-duplicate/MinHash, or infrastructure/queue solely because Chapter 3 describes them. These are possible approaches, not requirements. Revisit only if a measured failure or operational target justifies the cost.

## Deferred / not required before Split

- Do not redesign the canonical document schema before establishing whether current block/heading metadata limits the Split comparison.
- Do not treat 7,843 observed clause markers, parser success, or table-count observations as accuracy metrics.
- Do not repeat the serial corpus baseline absent changed conditions or a suspected defect; the existing run answers that question.
- Do not compare synthetic and real latency as an apples-to-apples result: they differ in file mix and size. The synthetic set was 16 DOCX; the real set was 146 DOCX + 8 DOC.

## Final readiness recommendation

**READY WITH DOCUMENTED LIMITATIONS.** Proceed to Phase 2 with `ordered-blocks-v1` frozen as the Load input and compare Split variants on identical parser outputs. Keep failed/empty documents excluded and labeled; use the selected ground-truth template when human references become available. Report Split metrics as conditional on the current flattened structure. Do not claim validated Load accuracy, real PDF/layout coverage, or production scalability until those measurements exist.
