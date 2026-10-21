# Phase 3 — Embed readiness audit

**Аудитын төлөв: READY FOR A SMALL ISOLATED SMOKE TEST; FORMAL EMBEDDING/RETRIEVAL COMPARISON NOT READY.**

Энэ тайлан implementation audit ба хоёр human-verified асуултын хязгаартай smoke бэлтгэлийг нэгтгэнэ. Production embedding configuration болон архитектурыг өөрчлөөгүй.

## Одоогийн runtime ба кодын traceability

| Capability | Төлөв / нотолгоо | Хязгаарлалт |
|---|---|---|
| Provider/model | OpenAI-only app config: `text-embedding-3-small`, 1,536 dimensions. Config: `app/config.py::settings`; client: `app/services/openai_provider.py::openai_client`. | Runtime тохиргоо deploy бүрт өөр байж болно; API key байгаа/утгыг public тайланд бичээгүй. |
| Tokenizer ба input cap | `app/services/openai_provider.py::OpenAIProvider._encoding/count/embed/fit_chunks`: `tiktoken.encoding_for_model(model)`; unknown model-д `o200k_base`, `tiktoken` байхгүй бол conservative UTF-8 byte estimate. Chunk per-input дээр 7,900 token safety guard. | Split offline run нь `cl100k_base` хэрэглэсэн; online provider encoding dynamic mapping-тэй. Тиймээс Phase 3 cost preflight runtime model encoding-ийг ашиглана. 7,900 нь аппын guard болохоос provider-ийн нийт баталгаат maximum гэж тайлбарлахгүй. |
| Batch processing | `app/services/ingestion.py::embedding_batches`: дээд тал нь 16 item болон 16,000 UTF-8 byte budget-аар batch. `ingestion.process()` batch бүрийг embed хийгээд Chroma-д upsert хийнэ. | Byte budget token count биш; provider дотор item бүрийн 7,900-token guard давхар үйлчилнэ. |
| Retry/rate-limit | `OpenAIProvider._call`: HTTP connection/timeout, 429, 5xx-д хамгийн их 3 attempt, exponential delay+jitter, request deadline; quota error дахин оролдохгүй. | API-specific load/rate-limit behavior-ийг энэ аудитад дуудаж шалгаагүй. |
| Cache, identity, content hash | Embedding vector response cache байхгүй. `chunking.assign_ids()` нь `version UUID + config fingerprint + ordinal`-оос deterministic chunk ID, текстийн `content_hash` өгнө. Chroma upsert ижил ID-г overwrite/idempotent болгоно. `app/services/sparse.py` content/index hash шалгана. | API retry давтагдвал cached vector-аас сэргээхгүй; тасарсан run дахин индексэлбэл төлбөртэй хүсэлт давтаж болзошгүй. Энэ нь хэмжсэн асуудал биш, code-level эрсдэл. |
| Embedding metadata ба provenance | `ingestion.process()` document/version/language/config hash-ийг chunk metadata-д нэмнэ; content hash, ordinal, parser block range, heading, page (байвал), source region (байвал) `app/services/chunking.py`-д үүснэ. | Parser page/clause/source span metadata өгөхгүй үед код зохиож нөхөхгүй. Хоёр annotation-той smoke-ийн тайлан missing metadata-г тусад нь жагсаана. |
| Chroma compatibility | `app/services/vectorstore.py::VectorStore`: `embedding_function=None`, cosine HNSW, app өөрөө embedding дамжуулна. Ingestion Chroma ID set-ийг manifest-тай тулгана. `app/services/retrieval.py::embedding_mismatch()` provider/model/dimensions-ийг runtime config-тай харьцуулна. | Collection нь эхний vector-аас dimension-ээ тогтооно; model/version дэд системийн metadata/collection identity-гаар нийцлийг хянадаг, Chroma өөрөө семантик model identity батлахгүй. Smoke нь private collection үүсгэнэ. |
| Error handling | Invalid vector length/non-finite/zero norm: `app/services/vectors.py::normalize_vector` and `embedding_contract.validate_vectors`; API retries: `openai_provider.py`. Failed ingestion version `failed` төлөвт орно; Chroma IDs manifest-тай mismatch бол activate хийхгүй. | Embedding API latency, rate-limit, transient-failure, billing usage production бус offline audit-аар шалгагдаагүй. |
| Configuration/versioning | `app/services/ingestion.py::index_config`; model/provider/dimensions, chunk config, tokenizer guard, parser/native-tool identity орсон config hash; шинэ collection нь тухайн config hash-аас үүсдэг. | Одоогийн хувилбар сонгох шийдвэрт model comparison evidence алга. |

## Бэлтгэсэн хамгийн бага smoke-test

Frozen snapshot болон `gt-v1`-ийг hash/identity-гаар шалгаж A0 production chunker-ийн **1,800 char / 250 overlap** output үүсгэнэ. Document ID нь зөвхөн `DOC####` source identity; хувилбарын ID-г production-оос хуулж/зохиохгүй, benchmark-д зориулсан детерминист UUID ашиглана. Тусгай Chroma path нь `instance/split-evaluation/embed-smoke/runs/<run-id>/chroma`; PostgreSQL болон production Chroma path-д холболтгүй.

| Урьдчилсан хэмжилт | Үр дүн |
|---|---:|
| Human-confirmed асуулт / variation | 2 / 6 |
| Төлөөлөх эх баримт | 2 |
| A0 chunk | 60 |
| Document embedding token estimate | 74,126 |
| Query embedding token estimate | 125 |
| Нийт estimate | 74,251 tokens |
| Batch/request estimate | 14 хүртэл embedding хүсэлт (production batch/byte budget-аар) |
| Raw vector payload estimate | 368,640 bytes (60 × 1,536 × 4; metadata/index overhead ороогүй) |
| `text-embedding-3-small` үнэ | $0.02 / 1M input tokens; estimate **$0.00148502 USD** |
| Энэ гүйцэтгэл дэх billed API usage | 0 |

Үнэ нь 2026-10-21-нд [OpenAI model pricing](https://developers.openai.com/api/docs/models/text-embedding-3-small)-ээс шалгасан rate-аар тооцсон; бодит billing нь тухайн account/үеийн үнийн нөхцөлөөс хамаарна. Token count нь яг billed usage биш estimate. Estimated input болон query бүрийн агуулгыг тайланд хадгалахгүй.

### Төлбөргүй mechanical smoke

`--local-mock` нь бодит frozen A0 chunks-ийг deterministic mock vector-аар 1,536 хэмжээстэй болгож isolated Chroma-д бичин, ID/metadata/source reference roundtrip болон top-k query-механикийг шалгана. Энэ нь **OpenAI embedding compatibility-ийн бүрэн баталгаа биш**, mock нь semantic биш, query exact-match нь чанарын хэмжүүр биш. Энэ горим амжилттай гүйсэн: 60 ID manifest-тай таарсан; хоёр verified evidence-ийн parser block/chunk холбоос Chroma roundtrip хийсэн; known-target oracle-vector smoke хоёулаа reference рүү буцсан. Production data untouched.

### Бодит OpenAI smoke gate

API дуудаагүй. Зөвшөөрөл авсны дараах `--confirm-paid` горим яг дээрх 60 document chunk болон 6 verified query variation-ийг embed хийнэ, тусгаарлагдсан Chroma-д хадгална, query бүрийн top-5 result chunk/source reference-ийг буцааж, parser block-д холбогдсон evidence chunk-уудын top-k coverage-г зөвхөн smoke diagnostic хэлбэрээр тэмдэглэнэ. Мөн vector dimension, ID set, hash, missing page/clause/source metadata-г шалгана. Энэ нь **2 query-той plumbing smoke** болохоос representative Recall@k/MRR/nDCG benchmark эсвэл embedding-model comparison биш.

## Phase 3 туршилтын дараалал

1. Өнөөгийн `text-embedding-3-small` / 1,536 dimension-ээр дээрх хязгаартай smoke; эхлээд estimate-ийг хянаж, хэрэглэгчийн зөвшөөрөл хүлээнэ.
2. Хэрэв plumbing амжилттай бол index/query vector dimension, source identity, citation field, isolate/reproduction checks-ийг батална. Алдааг production index-т нөлөөлөлгүйгээр засна.
3. Formal embedding model comparison-ыг нэмэлт representative human-verified query/evidence хүрэлцсэний дараа эхлүүлнэ. Model-с бусад chunker, corpus order, query set, retrieval top-k, distance metric, context budget-ийг ижил байлгана. Model бүрийг тусдаа Chroma collection/path-д хадгалж, API usage, latency, vector bytes, failure/retry-г бүртгэнэ.
4. Retrieval quality-г chunking strategy-с тусад нь тайлагнана; хоёуланг нь нийлүүлж нэг өөрчлөлт зэрэг туршихгүй.

## Reproduction commands

```powershell
# Хэш/identity/chunk/token/cost preflight; API, PostgreSQL, Chroma огт дуудахгүй
.\.venv\Scripts\python.exe -m flask --app app benchmark-embed prepare-smoke

# Chroma plumbing ба provenance roundtrip; deterministic mock, API төлбөргүй
.\.venv\Scripts\python.exe -m flask --app app benchmark-embed prepare-smoke --local-mock

# Зөвшөөрөл олгосны дараа л OpenAI embedding API дуудах
.\.venv\Scripts\python.exe -m flask --app app benchmark-embed prepare-smoke --confirm-paid
```

Preflight plan болон run artifact нь Git-ignored `instance/split-evaluation/embed-smoke/` дотор. `candidate-review.json`, `review-state.json`, `gt-v1` болон frozen Load/Split artifacts өөрчлөгдөөгүй.
