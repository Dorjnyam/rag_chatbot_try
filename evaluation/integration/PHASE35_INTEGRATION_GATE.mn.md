# Phase 3.5 — PostgreSQL Integration Gate ба Pre-Store шалгалт

**Шийдвэр: READY WITH DOCUMENTED LIMITATIONS**

## Хураангуй

Flask → PostgreSQL → ChromaDB хоорондын metadata, document/version lifecycle, vector contract, retrieval handoff болон failure recovery-г fake deterministic embedding ашигласан isolated integration орчинд шалгав. Бүтэн suite **169 passed, 1 skipped** болсон. Tesseract суулгаагүй тул OCR-ийн нэг тест skip хэвээр байна. Бодит хэрэглэгчийн баримт болон private annotation dataset ашиглаагүй; OpenAI API дуудлага хийгээгүй.

Энэ үр дүн Phase 4 — Store-ийн ажлыг эхлүүлэхэд зөвшөөрнө, гэхдээ PostgreSQL ба ChromaDB нэг atomic transaction биш; бодит production өгөгдөл дээрх эцсийн баталгаа биш гэдгийг анхаарна.

## Өмнөх тестийн нөхцөл ба inventory

Өмнөх `app` fixture нь `TEST_DATABASE_ADMIN_URL` байхгүй үед PostgreSQL integration тест бүрийг skip хийдэг байсан. Хэрэглэгчийн өгсөн өмнөх тайлан **136 passed / 38 skipped** гэж тэмдэглэсэн. Энэ workspace дэх миний туршилтын өмнөх бодит run **130 passed / 38 skipped** байсан; энэ зөрүү нь baseline-ийн suite revision өөр байсныг харуулж байгаа тул тоонуудыг шууд нэг ижил run гэж үзээгүй.

| Файл | PostgreSQL шаардсан тестүүд | Өмнөх prerequisite / эрсдэл |
|---|---|---|
| `tests/test_integration.py` | `test_lifecycle_review_idempotence_and_persistent_chroma`, OpenAI migration/rollback ба migration dry-run, review/index build, folder import, retry, delete/reconcile, cross-document FK, RAG/chat, batch, evaluation runner болон bilingual smoke | Isolated PostgreSQL `CREATEDB`; Chroma болон file artifacts тест бүрийн temp directory-д байх |
| `tests/test_navigation_reliability.py` | Chat/evaluation pagination, query batching, context trace, review navigation | PostgreSQL fixture болон isolated app storage |
| `tests/test_preview.py` | Preview setup, OCR proposal workflow, crop/map invalidation, cleanup | PostgreSQL fixture; OCR proposal тест нь OCR engine-ийг заавал ажиллуулахгүй |
| `tests/test_rag_reliability.py` | Нэмэлт hit citation, rewrite fallback, quota-safe error | PostgreSQL fixture болон deterministic provider double |
| `tests/test_readiness.py` | `test_ready_with_real_disposable_postgres_and_synthetic_indexes` | Бодит PostgreSQL, зөвхөн synthetic индекс |
| `tests/test_native.py` | `test_native_ocr_page_preserves_provenance` | `pytesseract` болон Tesseract binary; энэ орчинд binary олдоогүй |

Одоогийн кодын нөхцөлд disposable database үүсгэж, дараа нь `DROP DATABASE` хийхийн тулд test admin role-д `CREATEDB` шаардлагатай. Production application role-д privilege нэмээгүй. Fixture зөвхөн owner marker таарсан database-ээ устгадаг. Хэрэв target identity батлагдахгүй, provisioning алдаа гарвал fixture skip хийхгүйгээр fail хийнэ.

## Тусгаарлалт ба аюулгүй ажиллагааны баталгаа

- Тусгай PostgreSQL 16.11 cluster: `127.0.0.1:55439`, data directory `instance/pg-phase35`; хөгжүүлэлтийн 55438 болон өөр процессын 5432 порт руу холбогдоогүй.
- Тест role `rag_phase35_test`: `CREATEDB=true`, `SUPERUSER=false`, `CREATEROLE=false`.
- Cluster identity marker, database comment, host/port, role, privileges, data directory зэргийг тест эхлэхээс өмнө шалгана. Тайлангийн private artifact: `instance/test-results/phase35/environment.json`; credential бичээгүй.
- Тест бүр өөрийн санамсаргүй UUID database үүсгэж, PostgreSQL migration `flask_migrate.upgrade()`-аар ажиллуулж, ownership marker шалгасны дараа цэвэрлэнэ.
- ChromaDB `tmp_path/DATA_DIR/chroma`-д тусгаарлагдсан. Integration tests production Chroma path ашиглахгүй.
- Fixture `load_dotenv`-ийг унтрааж, `create_app()`-д үүсгэсэн disposable DB URI-г шууд өгнө. `.env`-д test URI нэмээгүй.
- PostgreSQL schema migration test DB дээр л гүйцэтгэсэн. Production PostgreSQL/ChromaDB, бодит баримтын corpus, annotation dataset өөрчлөгдөөгүй.

## Олдсон доголдол ба засвар

1. **`ingestion.reconcile()` runtime `NameError`**: preview artifact шалгахдаа `root()` хэрэглэж байсан ч import хийгдээгүй. Import нэмээд lifecycle integration тестээр баталсан.
2. **Тестийн OpenAI dimension тохиргооны зөрүү**: fixture зөвхөн хуучин `EMBEDDING_DIMENSIONS` alias-ийг 128 болгож байсан; ingestion `OPENAI_EMBEDDING_DIMENSIONS`-ийг хэрэглэснээр 1536-dimension vector ба 128-dimension query холилдож, Chroma `InvalidArgumentError` өгч байв. Fixture болон config өөрчилдөг тестүүдийг canonical OpenAI keys-тэй тааруулж, migration тестийн target dimension-ийг зориуд 64 болгож compatibility migration-ийг бодитоор шалгадаг болгосон. Production default/model өөрчлөгдөөгүй.
3. **Cross-store activation failure-ийн gap**: нэмэлт тестээр Chroma write амжилттай болсон ч PostgreSQL active-version commit унахыг загварчилсан. Хуучин active version хэвээр, шинэ хувилбар failed, vector нь orphan гэж илэрч, `reconcile(repair=True)`-аар устгагддагийг баталсан.

Анхны бүх-DB run-д дээрх fixture тохиргооны зөрүү болон `root()` import-ын улмаас failure илэрсэн. Засварын дараа зорилтот integration run-ууд амжилттай; эцсийн бүтэн run цэвэр өнгөрсөн.

## Интеграцийн хамрах хүрээ

| Шалгалт | Үр дүн |
|---|---|
| Document үүсгэлт, metadata, version lifecycle, active version | PASSED |
| PostgreSQL migration path болон fresh schema | PASSED; `flask_migrate.upgrade()` ашигласан |
| Duplicate/repeated ingestion, idempotency | PASSED |
| Stable chunk ID, manifest, document/version reference | PASSED |
| Embedding contract, dimensions, mismatch rejection | PASSED; deterministic fake vector |
| Chroma upsert/read/query roundtrip ба per-test тусгаарлалт | PASSED |
| PostgreSQL staged state → Chroma upsert failure → retry | PASSED; active хувилбар аюулгүй хэвээр |
| Chroma write → PostgreSQL activation failure → orphan reconcile/repair | PASSED; шинэ нэмсэн failure-injection тест |
| Inactive болон өөр document-ийн chunk/version шүүлт | PASSED |
| Missing vector reconciliation, delete retry, cleanup | PASSED |
| Cross-document active-version FK integrity | PASSED |
| Resource isolation, test DB owner marker cleanup | PASSED; fixture execution болон safety guard |
| OCR engine-тэй page provenance | SKIPPED; Tesseract binary байхгүй |

Эдгээр нь integration correctness тестүүд. Real-corpus extraction accuracy, production load/throughput, multi-process concurrent recovery болон PostgreSQL/ChromaDB хоорондын жинхэнэ atomicity-г нотлоогүй.

## Эцсийн regression үр дүн

- **169 passed**
- **1 skipped** — `tests/test_native.py:52`, Tesseract байхгүй
- **0 failed / 0 errors**
- Хугацаа: **79.88 секунд**
- **76 deprecation warnings** — migration environment дахь Flask-SQLAlchemy `get_engine()` deprecation; тестийг унагаагүй.
- Нууц агуулгагүй pytest/JUnit дэлгэрэнгүй private artifacts: `instance/test-results/phase35/pytest-final.log`, `instance/test-results/phase35/junit-final.xml`.

Historical reference-ийн 136 passed/38 skipped-аас одоогийн үр дүн +33 pass, −37 skip. Нийт цуглуулсан тестийн тоо 174-өөс 170 болсон тул тухайн baseline ба одоогийн checkout-ийн suite revision өөр байна (өмнөх Gemini provider цэвэрлэгээ болон шинэ safety/PG failure-injection coverage). Иймээс энэ нь ижил commit дээрх цэвэр before/after харьцуулалт биш; харин одоогийн checkout-ийн эцсийн suite result юм.

## Үлдсэн хязгаарлалт ба эрэмбэ

- **P1 — Cross-store атомик бус байдал:** PostgreSQL ба ChromaDB нэг transaction-д ордоггүй. Шинэ тест нь алдааны үед хуучин хувилбарыг идэвхтэй хадгалж, orphan vector-г илрүүлж цэвэрлэдгийг баталсан; процесс/машины тасалдлын бүх цонхыг бүрэн model хийгээгүй. Reconcile/retry нь хамгаалалтын чухал механизм хэвээр.
- **P2 — OCR:** Tesseract байхгүй тул native OCR тест бодитоор ажиллаагүй. Phase 4 Store шийдвэрийг хориглохгүй ч OCR parsing quality-н нотолгоо биш.
- **P2 — Бодит өгөгдлийн coverage:** DB/Chroma handoff-ийг synthetic DOCX ба deterministic embedding-ээр шалгасан. Монгол байгууллагын 151 баримт, private ground truth эсвэл OpenAI API ашиглаагүй.
- **P2 — Migration deprecation:** `migrations/env.py` дахь `get_engine()` deprecation warning цаашид засах ажил; schema migration амжилттай.
- **P3 — OCR accuracy болон distributed transaction:** нэг OCR тестээс чанарын дүгнэлт хийхгүй; distributed transaction/outbox архитектурыг энэ integration gate-ийн хүрээнд хэрэгжүүлэх шаардлага гэж тогтоогоогүй.

**Store gate: READY WITH DOCUMENTED LIMITATIONS.** Phase 4-ийг үргэлжлүүлж болно. Production deployment эсвэл бодит corpus quality claim хийхийн өмнө OCR prerequisite болон real-data validation-ийг тусад нь шийднэ.

## Дахин ажиллуулах

PowerShell дээр repo root-оос:

```powershell
.\scripts\start-phase35-test-postgres.ps1
$env:TEST_DATABASE_ADMIN_URL = 'postgresql+psycopg://rag_phase35_test@127.0.0.1:55439/postgres'
$base = Join-Path $PWD ('instance\pytest-phase35-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory $base | Out-Null
.\.venv\Scripts\python.exe -m pytest -q -rs -p no:cacheprovider --basetemp=$base --junitxml=instance\test-results\phase35\junit.xml
Remove-Item Env:TEST_DATABASE_ADMIN_URL
```

Бүрэн suite-ийг ажиллуулахад тест бүр шинэ UUID DB үүсгэж устгана. Test URI-г `.env` файлд бичихгүй. Хүсвэл тест дууссаны дараа зөвхөн тусгай cluster-ийг зогсооно:

```powershell
& 'C:\Program Files\PostgreSQL\16\bin\pg_ctl.exe' -D (Join-Path $PWD 'instance\pg-phase35') -m fast -w stop
```
## PostgreSQL fixture-тэй тестүүдийн нэрс

Өмнө нь environment variable байхгүйгээс доорх app-fixture тестүүд skip болсон. Одоогийн checkout-д 38 байна; шинээр нэмсэн `test_chroma_write_then_postgres_activation_failure_is_reconciled`-ийг хасвал 37 нь өмнөх PostgreSQL skip coverage-тэй таарна.

- `tests/test_integration.py`: `test_lifecycle_review_idempotence_and_persistent_chroma`, `test_openai_migration_stages_then_activates_and_rolls_back`, `test_openai_migration_dry_run_only_selects_incompatible_indexes`, `test_pending_review_build_uses_fixed_openai_embedding_config`, `test_import_folder_stages_all_files_for_review`, `test_failure_never_replaces_active_and_retry_recovers`, `test_chroma_write_then_postgres_activation_failure_is_reconciled`, `test_delete_failure_excludes_document_and_retry_cleans`, `test_reconcile_detects_missing_vectors_and_invalidates`, `test_database_rejects_another_documents_active_version`, `test_grounded_chat_trace_and_no_duplicate_content`, `test_bad_citation_retries_once_and_abstains_safely`, `test_changed_embedding_space_fails_explicitly`, `test_empty_corpus_no_api_key`, `test_document_collection_filters_groups_and_safe_return`, `test_sticky_review_action_keeps_review_and_csrf_requirements`, `test_ui_csrf_history_escaping_and_session_isolation`, `test_chat_explains_incompatible_active_embedding_indexes`, `test_cross_process_style_mutation_lock`, `test_embedding_batches_respect_request_budget`, `test_eval_runner_persists_and_requires_label_review`, `test_full_bilingual_dataset_engineering_smoke`.
- `tests/test_navigation_reliability.py`: `test_chat_pagination_newest_segment_chronological_and_return`, `test_evaluation_list_pagination_preserves_origin`, `test_query_batches_source_lookup_and_adds_context_only`, `test_chat_json_includes_context_trace_and_validates_return`, `test_evaluation_results_page_review_returns_to_same_page`.
- `tests/test_preview.py`: `test_preview_prepare_requires_existing_csrf`, `test_preview_prepare_exposes_safe_actionable_failure`, `test_preview_prepare_map_and_pdf_use_synthetic_source`, `test_ocr_proposal_creates_review_version_without_changing_active`, `test_ocr_crop_validation_rejects_outside_coordinates`, `test_changed_extraction_invalidates_preview_map`, `test_preview_sidecar_cleanup_is_version_scoped`.
- `tests/test_rag_reliability.py`: `test_context_can_cite_extra_hit_without_changing_final_k`, `test_rewrite_failure_preserves_original_and_closes_client`, `test_provider_quota_has_safe_mongolian_error_in_chat_and_detail`.
- `tests/test_readiness.py`: `test_ready_with_real_disposable_postgres_and_synthetic_indexes`.

Эдгээр нь тус бүр шинэ DB үүсгэж migration хийдэг. Chroma хамааралт тестүүд temporary DATA_DIR ашиглана. Destructive operation-уудыг DB-ийн owner marker шалгасан disposable fixture хүрээнд л ажиллуулсан.
