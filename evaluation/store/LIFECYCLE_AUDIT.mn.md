# Phase 4 — S2 lifecycle evidence review

## Дүгнэлт

**Critical integrity blocker илрээгүй; өмнөх PostgreSQL + ChromaDB lifecycle tests-ийг дахин ашиглав.** Phase 3.5 evidence аль хэдийн document/version state, Chroma write, failed retry, deletion, reconciliation болон cross-store activation failure-г хамарсан. Энэ Phase 4-д яг ижил failure path-ийг давтах test нэмээгүй.

## Өмнөх regression evidence

`evaluation/integration/PHASE35_INTEGRATION_GATE.mn.md`-д бүртгэгдсэн хамгийн сүүлийн бүтэн suite нь **169 passed, 1 skipped, 0 failed**, 79.88 секунд. Skip нь Tesseract OCR prerequisite байхгүйгээс; Store lifecycle-д хамаарахгүй. Integration fixture PostgreSQL 16.11 isolated localhost cluster, owner-marked UUID test databases ашиглаж; Chroma-г per-test `tmp_path` руу тусгаарладаг.

Phase 4 хэрэгжүүлэлтийн үед хамаарах integration хэсгийг одоо дахин ажиллуулахад **23 passed, 0 failed**, 42.94 секунд; migration `get_engine()` deprecation-оос **44 warnings** гарсан. Run-д `tests/test_integration.py` болон `tests/test_vector_restart.py` орсон. Benchmark-specific unit suite: **4 passed**, Chroma legacy configuration deserialization-оос **7 warnings**. Эцсийн бүтэн suite: **173 passed, 1 skipped, 0 failed**, 78.97 секунд; нэг skip нь Tesseract байхгүйгээс.

Шууд хамаарах coverage:

- `test_lifecycle_review_idempotence_and_persistent_chroma`: retry/idempotency, manifest ба Chroma ID parity, дахин нээсэн client-ээс persistence.
- `test_failure_never_replaces_active_and_retry_recovers`: шинэ version-ийн write fail үед хуучин active хэвээр; retry амжилттай.
- `test_chroma_write_then_postgres_activation_failure_is_reconciled`: Chroma vector write амжилттай ч PostgreSQL activation унахад orphan илрүүлж repair хийдэг.
- `test_delete_failure_excludes_document_and_retry_cleans`: delete retry хүртэл хайлтаас version хасна.
- `test_reconcile_detects_missing_vectors_and_invalidates`: дутуу vector-г илрүүлж index mismatch repair хийнэ.
- `test_database_rejects_another_documents_active_version`: active version owner constraint.
- `tests/test_vector_restart.py::test_small_collection_search_survives_process_exit`: жижиг Chroma index-ийн process restart/query smoke.

## Хамрахгүй хэвээр

- PostgreSQL ба Chroma-ийн distributed atomicity байхгүй; процесс/машины бүх crash window туршигдаагүй.
- Production хэмжээний concurrent mutation/reader stress хэмжээгүй.
- Chroma HNSW exact index Recall, N=1,000–10,000 scale, search tail latency, disk/RSS нь Phase 3.5 suite-ийн scope биш; эдгээрийг шинэ synthetic S3/S4 runner хэмжинэ.
- Хүний relevance, Mongolian query quality, citation quality нь synthetic index benchmark-оор хэмжигдэхгүй.

## Дахин шалгах команд

Одоогийн isolated Phase 3.5 PostgreSQL орчныг эхлүүлж, зөвхөн зориулалтын role/port ашиглана:

```powershell
.\scripts\start-phase35-test-postgres.ps1
$env:TEST_DATABASE_ADMIN_URL = 'postgresql+psycopg://rag_phase35_test@127.0.0.1:55439/postgres'
$base = Join-Path $PWD ('instance\pytest-phase4-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory $base | Out-Null
.\.venv\Scripts\python.exe -m pytest tests\test_integration.py tests\test_vector_restart.py -q -p no:cacheprovider --basetemp=$base
Remove-Item Env:TEST_DATABASE_ADMIN_URL
```

`conftest.py` дахь safety check cluster identity, port 55439, role `rag_phase35_test`, database marker, `CREATEDB` privilege-ийг тулгадаг. Production DB URL энэ variable-д хийхгүй.
