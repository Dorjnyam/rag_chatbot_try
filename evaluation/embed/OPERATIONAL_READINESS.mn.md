# Phase 3 — Operational Embed readiness decision

**Шийдвэр: READY WITH DOCUMENTED LIMITATIONS**  
**Retrieval-quality benchmark: NOT READY.**  
**E3 embedding-model comparison: DEFERRED.**

Operational plumbing ба шинэ vector identity checks-ийг эхлүүлэхэд critical blocker илрээгүй. Харин энэ нь production indexing-ийн бүрэн баталгаа биш: PostgreSQL-backed lifecycle/recovery тестүүд энэ орчинд ажиллаагүй, мөн quality annotations цөөн.

## Дууссан ажлууд

- E1: Chapter 5-ийн 5.1–5.11 traceability ба бодит pipeline audit. [CHAPTER5_TRACEABILITY.mn.md](CHAPTER5_TRACEABILITY.mn.md), [PHASE3_INTEGRATION_AUDIT.mn.md](PHASE3_INTEGRATION_AUDIT.mn.md).
- E2.1: Frozen 151 parsed-doc corpus-ийн 2,468 A0 chunk-д tokenizer benchmark (`cl100k_base`, `tiktoken 0.12.0`), total 2,667,761 tokens; max 1,730; 7,900 safety guard давсан 0 chunk.
- E2.2: Model compatibility нь provider/model/model revision/dimensions/preprocessing/normalization/query-document convention-ийг хамарсан fingerprint. Ижил dimension төдийд зөвшөөрөхгүй.
- E2.3: Vector dimension, finite values, L2 normalization баталгаажуулалт; cosine collection/query smoke болон unit-vector cosine/dot/L2 relationship шалгалт.
- E2.4–E2.7: Local deterministic mock дээр batch 8/16/32, Chroma upsert, wall/CPU time, Python allocation, storage footprint хэмжсэн; ямар ч API call хийгээгүй.
- Cache: хэрэгжүүлээгүй. Одоогоор re-embedding-ийн давтамж, үүнээс үүсэх бодит давтагдсан төлбөр/latency хэмжээгүй. Өмнөх bounded smoke-ийн estimate ~$0.00149 байсан ба бүх corpus-ийн cost биш.
- A0 болон production embedding config хэвээр: `text-embedding-3-small`, 1,536 dimensions, 1,800 chars / 250 overlap.

## Нээлттэй хязгаарлалт ба эрсдэл

1. **P1 — DB-backed recovery бүрэн баталгаажаагүй.** Full suite `136 passed, 38 skipped`; 37 skip PostgreSQL integration fixture-д `TEST_DATABASE_ADMIN_URL` + `CREATEDB` эрхгүй тул, 1 skip Tesseract байхгүйгээс. Иймээс `ready` short-circuit, failed retry cleanup, reconcile code байгаа ч DB/chroma lifecycle failure-injection энэ орчинд нотлогдоогүй.
2. **P1 — Legacy collection identity.** Өмнөх version config-д шинэ fingerprint байхгүй бол field defaults-аар backward-compatible шалгана. Хуучин Chroma collection-д fingerprint байхгүй байгааг өмнөх бүх vector ижил model-оор үүссэн гэж шинээр нотлох боломжгүй; шинэ contract collection fingerprint mismatch бол fail closed.
3. **P1 — Retrieval ground truth бага.** Зөвхөн 2 human-verified question / 6 variation. Энэ нь evidence Recall@k, MRR, nDCG, multilingual quality зэрэг benchmark-д хангалтгүй.
4. **P2 — Provider performance/cost incomplete.** Өмнөх paid smoke-д 74,251 provider-reported tokens, 14 attempts хадгалагдсан боловч per-request latency болон billing receipt байхгүй. Энэ ажлын хүрээнд шинэ төлбөртэй request хийгээгүй.
5. **P2 — Memory measurement.** Offline run-д `tracemalloc` Python allocation ~151 MiB; process RSS sampler ажиллахгүй тул RSS unavailable. Chroma disk local machine, one run-ийн хэмжилт.
6. **P2 — Runtime model revision.** Model revision env var боломжтой ч одоогоор тохируулаагүй; provider response нь хүссэн base model ID-тэй таарсныг л шалгасан.

## Гол source / tests

- `app/services/embedding_contract.py`: canonical vector-space identity, hash, vector/collection validation.
- `app/services/openai_provider.py`: requested vs returned model check, SDK retries off, app retry, usage/request latency counters.
- `app/services/ingestion.py`: index config identity, batch orchestration, idempotency and failure cleanup.
- `app/services/vectorstore.py`, `app/services/retrieval.py`, `app/services/index_migration.py`: collection write/query/migration compatibility.
- Tests: `tests/test_embedding_contract.py`, `tests/test_sdk.py`, `tests/test_embed_smoke.py`; full suite `135 passed, 38 skipped`.

## Дараагийн зөв алхам

PostgreSQL test user-д database create эрхтэй `TEST_DATABASE_ADMIN_URL` тохируулж full integration/recovery suite-ийг ажиллуул. Дараа нь annotation review-ээр representative verified evidence цуглуулж, хэрэглэгчээс cost estimate-ийг урьдчилан зөвшөөрүүлсний дараа тусгаарласан paid retrieval evaluation төлөвлө. Шинэ model download, embedding setting өөрчлөх, production collection migration одоогоор шаардлагагүй.

## Reproduction

```powershell
# E2.1 tokenizer-only: provider, PostgreSQL, Chroma дуудахгүй
.\.venv\Scripts\python.exe -m flask --app app benchmark-embed tokenization

# E2.4–E2.7 mock + isolated Chroma, API дуудлагагүй
.\.venv\Scripts\python.exe -m flask --app app benchmark-embed offline-pipeline --batch-sizes 8,16,32

# New contract / retry / mock Chroma tests
.\.venv\Scripts\python.exe -m pytest tests/test_embedding_contract.py tests/test_sdk.py tests/test_embed_smoke.py -q -p no:cacheprovider --basetemp=instance/pytest-tmp

# Full suite; PostgreSQL skip-үүд арилгахын тулд isolated admin test DB шаардлагатай
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp=instance/pytest-tmp -rs
```

Private reports, raw count mappings, Chroma benchmark DB болон prior smoke артефактууд `instance/` дотор, Git-ignored хэвээр.
