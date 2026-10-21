# Phase 3 — E1 ба E2.1–E2.7 үр дүн

## Туршилтын нөхцөл

- Frozen corpus: 151 parsed + 3 өмнөх Load failure, нийт 154 input; SHA-256 `15b24382aeba13e943382370f5a4bfb4ede50b4033805006bad692d23bf3d56a`.
- Parser `ordered-blocks-v1`; splitter A0 `1,800 characters / 250 characters overlap`; 2,468 chunks.
- Одоогийн OpenAI config identity: `text-embedding-3-small`, 1,536 dimensions, tokenizer `cl100k_base`, `tiktoken 0.12.0`, `raw-input-v1`, `l2-v1`, symmetric query/document convention.
- E2.1 нь tokenizer-only. E2.4–E2.7 нь deterministic mock vectors ба isolated local Chroma. API requests = 0; production DB/Chroma writes = 0.

## E1 — Integration/Chapter 5 audit

Traceability: [CHAPTER5_TRACEABILITY.mn.md](CHAPTER5_TRACEABILITY.mn.md). Integration code path, idempotency, recovery, cache feasibility: [PHASE3_INTEGRATION_AUDIT.mn.md](PHASE3_INTEGRATION_AUDIT.mn.md). Өмнөх user-authorized smoke артефакт 60 chunk, 1,536 dim, ID/ref roundtrip амжилттай, 74,251 token usage хадгалсан. Шинэ paid request хийгээгүй. Provider latency болон billing total нь хуучин артефактэд байхгүй тул сэргээж хэмжээгүй.

## E2.1 — Tokenization benchmark

Run `d2031800b3ec`, private JSON/CSV: `instance/split-evaluation/embed-benchmark/tokenization/`.

| Хэмжүүр | Утга |
|---|---:|
| A0 chunks | 2,468 |
| Нийт token | 2,667,761 |
| Token/chunk P50 / P95 / P99 / max | 1,313 / 1,459 / 1,540 / 1,730 |
| App guard 7,900-аас давсан chunk | 0 |
| Cyrillic-containing heuristic chunks | 2,460 |
| `Ө/Ү` тэмдэгт агуулсан heuristic chunks | 2,412 |
| NFC normalization хийснээр token count өөрчлөгдсөн chunk | 0 / 2,468 |
| Өмнөх 2-doc smoke-д preflight vs provider `prompt_tokens` | 74,251 vs 74,251 (зөрүү 0) |

Script/numbered identifier бүлэг нь heuristic; Mongolian Latin ангилал нь 6 chunk бөгөөд corpus-ийн баталгаатай хэлний шошго биш. 7,900 threshold-д багтсан ч үйлчилгээний далд limit эсвэл truncation-ийн нотолгоо биш.

## E2.2–E2.3 — Vector compatibility, correctness ба similarity

`tests/test_embedding_contract.py`, `tests/test_sdk.py`, `tests/test_embed_smoke.py`:

- Ижил dimensions ч model, revision, preprocessing, normalization, query/document convention өөр бол compatibility false.
- Collection contract hash өөр/дутуу үед write fail closed; шинэ contract-той retrieval mismatch-ийг зогсооно.
- Vector-ийн dimension, finite value, unit-L2 norm шалгалт; zero/non-unit вектор reject.
- HTTP mock: 429 → нэг retry → хоёр unit vector зөв index order; response model identity, usage token ба latency instrumentation тэмдэглэгдсэн.
- Unit-vector үед `cosine = dot`, `squared L2 = 2 − 2*cosine` relationship шалгасан. Chroma isolated smoke cosine collection-д oracle vector query болон source reference roundtrip хийсэн.

Runtime model revision configured биш. OpenAI response model ID суурь нэр л өгнө; immutable provider revision unavailable. Одоогийн legacy collection-үүдийн физик metadata-г энэ benchmark өөрчлөөгүй. Шинэ contract fingerprint-тэй collection дээр mismatch бол хаана.

## E2.4–E2.7 — Offline/mock operational measurements

Основной three-size run `5cad4b7d3288` (Windows 11, Python 3.12.15, ChromaDB 1.5.9), `instance/split-evaluation/embed-benchmark/runs/` дотор:

| Mock batch size | Batch count | Mock gen total (s), batch P50/P95 (ms) | Chroma upsert total (s), batch P50/P95 (ms) | End-to-end (s) | CPU (s) | Python allocation peak (MiB) | Chroma disk (MB) |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 8 | 463 | 3.84; 7.5 / 11.7 | 31.27; 61.6 / 109.1 | 41.55 | 13.09 | 151.3 | 60.6 |
| 16 | 446 | 3.87; 7.6 / 15.7 | 35.90; 68.1 / 131.4 | 46.21 | 8.16 | 151.0 | 60.6 |
| 32 | 445 | 3.90; 7.6 / 16.6 | 30.64; 64.8 / 105.9 | 40.65 | 7.88 | 151.2 | 60.7 |

Эдгээр нь нэг local run-ийн ялгаатай batch-size туршилт; byte-budget (16,000 UTF-8 byte) ихэнх batch-ийг эрт тасалж байгаа тул 8, 16, 32 item cap бодит batch count-д бага нөлөө үзүүлэв. Batch-size-ийн тогтвортой хурдны давуу тал харагдсангүй. Batch P50/P95 нь зөвхөн тухайн run-ийн олон batch call-ын distribution; end-to-end P50/P95 гэж үзэхэд олон давталт алга. Disk хэмжээ ижил 2,468 × 1,536 mock vector хадгалсан local Chroma хэмжилт. Raw float32 payload estimate 15,163,392 bytes (~14.5 MiB). Process RSS хэмжигч байхгүй (`null`); Python `tracemalloc` peak ~151 MiB нь process RSS биш. Mock generation нь OpenAI provider performance биш.

| Latency-ийн төрөл | Яаж хэмжсэн | Хязгаар |
|---|---|---|
| Provider request latency | Өмнөх paid artifact-д N/A | Шинэ paid API request хийгээгүй. |
| Mock generation latency | Deterministic local vector үүсгэлтийн нийт/батч хугацаа | Provider latency, token/sec биш. |
| Batch orchestration | Interleaved mock generation + Chroma upsert wall time | Scheduling, Python/Chroma overhead хамарна. |
| Chroma persistence | Upsert call elapsed-time нийлбэр | Local DB, disk/OS cache-ээс хамаарна; provider-ээс тусдаа. |
| End-to-end mock | Benchmark wall-clock; Chroma get/verification орно | Production ingest throughput биш. |

Багцын retry test-ээр HTTP 429 дараа амжилттай хүсэлтийг шалгав. `ready` idempotency болон fail/reconcile recovery кодын audit хийсэн боловч PostgreSQL-тэй failure-injection туршилт энэ milestone-д ажиллаагүй. Cache хэрэгжүүлээгүй: одоогийн smoke хэмжээний estimate ойролцоогоор $0.00149 байсан ба давтамжтай дахин embedding/cost хэмжилт байхгүй; бүтэн corpus paid cost гаргаж батлаагүй.

## Regression tests ба тайлбар

Шинэ targeted tests: **19 passed** (`test_embedding_contract.py`, `test_sdk.py`, `test_embed_smoke.py`). Эхний pytest оролдлого Windows-ийн global `%TEMP%` permission-оос болж smoke fixture үүсгээгүй; дараа нь ignored `instance/pytest-tmp` руу `--basetemp` зааж тестүүд амжилттай ажилласан. Бүх засварын дараах full suite: **136 passed, 38 skipped**; skip шалтгаан ба readiness хязгаарыг [OPERATIONAL_READINESS.mn.md](OPERATIONAL_READINESS.mn.md)-д тайлбарласан.

## Дүгнэлт

E1 smoke нь embedding-to-isolated-Chroma plumbing ажилласны хязгаартай нотолгоо. E2.1 token/input diagnostics, E2.2 contract correctness, E2.3 geometry, E2.4–E2.7 offline orchestration/storage/performance аудит бэлэн. Үүнээс retrieval чанар, multilingual superiority, cache ROI, live API rate-limit, production failure-recovery, real billed cost-ыг дүгнэж болохгүй.
