# 12 — Issues and recommendations

Finding ID, severity, status, and recommendation are authoritative here. Independent Technical Reviewer checked items 001–008, 010; 009 was reviewed against the source order by lead. All line numbers refer to audit snapshot HEAD plus its dirty working-tree content and should be checked against the corresponding file before remediation.

## AUDIT-001 — Test fixture may destroy a non-disposable database

- **Agent/category:** QA / Security; Testing and safety
- **Severity/status/confidence:** HIGH / CONFIRMED / HIGH
- **Location:** `tests/conftest.py:43-63`, `app` fixture; `db.drop_all()` setup and teardown
- **Description:** Any database whose path begins with `rag_test` passes the safety guard. The fixture then drops and recreates all tables.
- **Evidence:** Guard validates only `database_name.startswith("rag_test")`; both setup and finalizer call `db.drop_all()`.
- **Expected/actual:** Require a demonstrably disposable isolated database / a prefix-matching DB can contain valuable data and still pass.
- **Impact:** A misconfigured integration test can erase data.
- **Reproduction:** Static: set TEST_DATABASE_URL path to `rag_test_prod`; fixture accepts it and invokes drop_all. Not executed.
- **Recommendation:** Require an explicit disposable test marker plus unique database name/owner; fail closed unless an isolated service/database is independently provisioned.
- **Effort/priority/regression:** Medium / P1 / test guard rejects a shared or production-like database while accepting dedicated isolated DB.

## AUDIT-002 — No authentication or document authorization for shared use

- **Agent/category:** Security; API/security/deployment
- **Severity/status/confidence:** HIGH / CONFIRMED / HIGH
- **Location:** `app/__init__.py:37-48`; document/chat routes `app/routes/documents.py`, `app/routes/chat.py`
- **Description:** Application is explicitly loopback-only and has no user identity, login, role, or per-document access rule.
- **Evidence:** request hook checks `remote_addr` and CSRF, but there is no identity/ACL path in route/model execution.
- **Expected/actual:** Shared employee service needs authenticated principal and document access enforcement / current system is a local single-user tool.
- **Impact:** Do not expose as a shared service; employee access isolation cannot be enforced.
- **Reproduction:** Static route/model inventory; no network test performed.
- **Recommendation:** Design authentication, authorization and trusted proxy boundary before enabling remote access.
- **Effort/priority/regression:** Large / P1 for shared deployment / test employee A cannot read employee B restricted documents.

## AUDIT-003 — Rewrite provider exception fails the chat request

- **Agent/category:** Backend/RAG; query rewriting/reliability
- **Severity/status/confidence:** MEDIUM / CONFIRMED / HIGH
- **Location:** `app/services/rag.py:117-123`, `ask`
- **Description:** When opt-in rewrite is enabled for mixed/unknown input, adapter exception escapes to outer request handler instead of original candidates.
- **Evidence:** `client.rewrite_query(question)` has no local try/except; outer handler marks QueryLog error at lines 181-185.
- **Expected/actual:** Fallback to original query candidates / provider timeout or quota error makes full chat fail.
- **Impact:** Optional optimization reduces availability.
- **Reproduction:** Enable flag and mock rewrite method to raise; then observe outer error path. Not executed.
- **Recommendation:** Catch rewrite-only exceptions, record degraded trace, continue with original candidates; preserve deadline and maximum one request.
- **Effort/priority/regression:** Small / P2 / enabled rewrite failure still completes dense/hybrid retrieval with original question.

## AUDIT-004 — nDCG can exceed 1 when a hit matches multiple gold units

- **Agent/category:** QA/RAG; evaluation correctness
- **Severity/status/confidence:** MEDIUM / CONFIRMED / HIGH
- **Location:** `app/evaluation.py:14-31`, `ranking_metrics`; gold matching `:59-73`
- **Description:** DCG gives gain `2**gain - 1` where `gain` counts multiple units matched by one hit, but ideal DCG assumes one binary unit per rank.
- **Evidence:** Numerator/ideal denominator use different gain assumptions; a hit can match more than one gold unit.
- **Expected/actual:** nDCG bounded to [0,1] with same gain model / overlap case can exceed 1.
- **Impact:** Evaluation rankings and comparisons can be mathematically invalid.
- **Reproduction:** Synthetic one-hit, two-relevant-unit case; formula inspection sufficient. Runtime reproducer not executed.
- **Recommendation:** Define graded gain consistently for observed and ideal rankings; deduplicate gold units and add overlap hand-calculated test.
- **Effort/priority/regression:** Small / P2 / all nDCG outputs remain within [0,1] and expected ranking order.

## AUDIT-005 — Output language validator checks script share, not Mongolian

- **Agent/category:** Multilingual NLP; language compliance
- **Severity/status/confidence:** MEDIUM / CONFIRMED / HIGH
- **Location:** `app/services/language.py:88-93`, `answer_is_mongolian`
- **Description:** The validator accepts text with sufficient Cyrillic-letter ratio, which does not distinguish Mongolian from Russian or other Cyrillic prose.
- **Evidence:** Character/script ratio heuristic; no calibrated language ID or Mongolian-specific lexical evidence at this boundary.
- **Expected/actual:** Enforce Mongolian Cyrillic prose while ignoring protected names/source identifiers / Cyrillic-only foreign prose may pass.
- **Impact:** UI may present non-Mongolian model output as compliant and skip repair.
- **Reproduction:** Supply a Cyrillic Russian sentence; inspect script ratio. Not executed.
- **Recommendation:** Add calibrated language detection or conservative Mongolian-specific validation and explicit protected-token handling; human-review examples.
- **Effort/priority/regression:** Medium / P2 / Russian/Cyrillic non-Mongolian fails, valid Mongolian with names/URLs passes.

## AUDIT-006 — Sparse artifact load does not verify stored content fingerprint

- **Agent/category:** RAG/Database; index integrity
- **Severity/status/confidence:** MEDIUM / CONFIRMED / HIGH
- **Location:** `app/services/sparse.py:31-60`, write/load; `search :67-72`
- **Description:** Artifact contains `content_hash`, but loader validates schema/version and unique IDs without recomputing hash or canonical per-record metadata/term consistency.
- **Evidence:** Alter `terms` or `text` while retaining IDs/hash; accepted structure reaches BM25 search. Writes are atomic, so this concerns modified/corrupt valid JSON, not routine partial write.
- **Expected/actual:** Detect tampered/stale artifact and rebuild/degrade / silently altered ranking or evidence can be returned.
- **Impact:** Evidence ordering/text may drift from the verified dense index.
- **Reproduction:** In isolated artifact, change a term/text but retain metadata; call load. Not executed against live data.
- **Recommendation:** Recompute digest, validate canonical manifest IDs and metadata, and invalidate/rebuild on mismatch.
- **Effort/priority/regression:** Medium / P2 / altered artifact is rejected and dense degraded fallback is traced.

## AUDIT-007 — History and evaluation lists are unbounded

- **Agent/category:** DevOps/Frontend; performance
- **Severity/status/confidence:** MEDIUM / CONFIRMED / HIGH
- **Location:** `app/routes/chat.py:42-45,104-113`, chat and evaluation list/detail
- **Description:** Current conversation queries, evaluation runs and run results load/render without pagination or retention cap; chat also fetches all active docs into a select.
- **Evidence:** SQL scalar/list calls have no page limit for these collections; detail loops over result set.
- **Expected/actual:** Bounded/paginated response for growing history / response/database work grows with all stored rows.
- **Impact:** Slow pages, memory/HTML growth for long-lived local or shared usage.
- **Reproduction:** Static query inspection; no load test performed.
- **Recommendation:** Paginate history and evaluation lists/results; use searchable document picker.
- **Effort/priority/regression:** Medium / P2 / large fixture returns fixed-size pages with navigation and stable sort.

## AUDIT-008 — Health endpoint is liveness-only but says `ok`

- **Agent/category:** DevOps/Security; operations
- **Severity/status/confidence:** MEDIUM / CONFIRMED / HIGH
- **Location:** `app/__init__.py:50-52`, `/health`
- **Description:** Static response checks no PostgreSQL, Chroma, sparse store or selected-provider readiness.
- **Evidence:** Route returns `{"status":"ok"}` without service calls.
- **Expected/actual:** Distinct liveness/readiness semantics / dependency outage still returns `ok`.
- **Impact:** Orchestrator may route requests to a non-ready instance.
- **Reproduction:** Static route; not deployed.
- **Recommendation:** Retain lightweight liveness and add bounded, non-secret readiness checks with clear dependency status.
- **Effort/priority/regression:** Small / P2 before shared deployment / dependency outage fails readiness, process alive keeps liveness.

## AUDIT-009 — Upload DB failure can leave source file unreferenced

- **Agent/category:** Backend/Database; storage lifecycle
- **Severity/status/confidence:** LOW / LIKELY / MEDIUM
- **Location:** `app/services/ingestion.py:52-59,90-98`, `new_version` and `upload`
- **Description:** Source bytes are written to content-addressed storage before version/document transaction commits; DB exception path has no visible compensating file delete.
- **Evidence:** Write precedes `new_version`; reconcile begins from DB-known documents and cannot discover unknown upload files.
- **Expected/actual:** Cleanup or detectable orphan / a commit failure may retain unreferenced bytes.
- **Impact:** Small filesystem storage leak; does not activate invalid version.
- **Reproduction:** Inject DB commit exception after file write in isolated fixture. Not executed.
- **Recommendation:** On failed commit remove only file created by this call when unreferenced, or add safe orphan audit/reconcile.
- **Effort/priority/regression:** Small / P3 / failed upload leaves no file or flags it for cleanup.

## AUDIT-010 — Query detail performs per-hit source and Chroma lookup

- **Agent/category:** Backend/Performance; query trace
- **Severity/status/confidence:** LOW / CONFIRMED / HIGH
- **Location:** `app/routes/chat.py:84-97`, query detail handler
- **Description:** Each trace hit triggers source lookup and chunk retrieval separately.
- **Evidence:** Iteration performs repeated DB get and vector get calls rather than one batch fetch.
- **Expected/actual:** Batch fetch and join / database and vector calls scale with hit count.
- **Impact:** Slower trace page as `k`/citation count grows.
- **Reproduction:** Static per-hit loop; no latency benchmark.
- **Recommendation:** Batch metadata/source loading and Chroma IDs where supported.
- **Effort/priority/regression:** Small / P3 / fixed number of lookups independent of hit count.

## Давхардал ба limitation

AUDIT-003-ийг RAG болон NLP reviewer тус тусад нь олсон тул нэг finding болгосон. AUDIT-006 нь atomic writer байгаа тул normal partial write claim биш. Visual browser, dependency advisory sweep, company quality, integration lifecycle нь NOT TESTED/BLOCKED; finding count-д оролцуулаагүй.
