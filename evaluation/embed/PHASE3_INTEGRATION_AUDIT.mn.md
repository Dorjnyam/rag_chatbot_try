# Phase 3 — Embed integration audit ба E1 smoke evidence

## Хураангуй

Одоогийн production defaults `text-embedding-3-small`, 1,536 dimensions, `cl100k_base` болон Phase 2 provisional A0 (`1,800` characters / `250` characters overlap) хэвээр. Энэ ажлын хүрээнд production PostgreSQL, Chroma collection, parser, splitter, embedding settings өөрчлөөгүй. Шинэ model-quality comparison эхлүүлээгүй, шинэ paid API request хийгээгүй.

## Integration audit

| Шат | Баталгаажсан кодын зам | Дүгнэлт |
|---|---|---|
| AI client | `app/services/openai_provider.py::openai_client` | Provider selection байхгүй; embedding болон generation OpenAI adapter-аар ажиллана. Тохиргоо `OPENAI_*` хувьсагчаас уншигдана. |
| Tokenization/input guard | `app/services/openai_provider.py::_encoding/count/embed/fit_chunks` | Model tokenizer, per-input 7,900-token app safety guard, oversized input-ийн split. Guard нь provider-ийн нийт API limit-ийн баталгаа биш. |
| Batch/orchestration | `app/services/ingestion.py::embedding_batches/process` | Chunk count ба UTF-8 byte budget-аар batch; batch бүр embed дараа Chroma upsert. Бодит provider болон Chroma latency-г тус тусад нь одоогийн хуучин log-оос сэргээх боломжгүй. |
| Retry | `app/services/openai_provider.py::_call` | Connection/timeout, 429/5xx transient retry-д дээд 3 attempt; SDK-ийн далд retry-г унтраасан; deadline/quota handling байна. HTTP mock тестээр нэг 429 дараа амжилттай retry-г шалгав. |
| Idempotency/recovery | `app/services/ingestion.py::process/reconcile` | `ready` version-д шууд буцна. Failed version retry хийхдээ өөрийн үлдсэн vectors-ийг устгаж бүх batch-ийг дахин embed хийнэ; reconcile incomplete index илрүүлнэ. DB failure-injection integration-г энэ орчинд тусад нь шалгаагүй. |
| Cache/hash | `app/services/chunking.py::assign_ids`; ingestion config/content hash | Chunk ID deterministic; Chroma upsert ижил ID-г overwrite. Persistent vector cache байхгүй. Одоогийн evidence cache нэвтрүүлэхийг зөвтгөхгүй; хэрэгжүүлээгүй. |
| Vector validation | `app/services/embedding_contract.py`; `app/services/vectorstore.py::upsert` | Тоо, dimension, finite numeric value, nonzero L2 norm, collection fingerprint-ийг write өмнө батална. |
| Storage/retrieval | `app/services/vectorstore.py`, `app/services/retrieval.py` | Chroma cosine space; retrieval нь distance өсөхөөр эрэмбэлнэ; reference metadata хамт хадгалагдана. Production data хүрээгүй. |

OpenAI telemetry-д `provider_attempt_latency_ms` нь SDK-ийн нэг HTTP/API attempt-ийн хэмжигдэх хугацаа, `embedding_operation_latency_ms` нь retry/backoff хугацааг оролцуулсан нийт `embed()` ажиллагааны хугацаа. Эдгээр шинэ хэмжүүр хуучин smoke артефактэд байхгүй; шинэ төлбөртэй дуудлага хийгээгүй тул бодит provider distribution одоогоор N/A.

## Өмнөх bounded smoke-ийн бодит evidence

Артефакт: `instance/split-evaluation/embed-smoke/runs/88e507f8b31f/result.json` (private, Git-ignored). Энэ нь одоогийн ажлаас өмнөх нэг удаагийн, user-authorized paid plumbing smoke бөгөөд шинээр давтаагүй.

| Шалгалт | Өмнөх smoke-ийн үр дүн |
|---|---:|
| Verified асуулт / хэлний variation | 2 / 6 |
| A0 chunk | 60 |
| Embedding dimension | 1,536 |
| Stored ID set / manifest | таарсан |
| Source evidence→parser/chunk references | 2 / 2 roundtrip |
| Recorded provider token usage | 74,251 input tokens |
| API attempts | 14 |
| Бодит provider latency | **N/A** — артефакт хадгалаагүй |
| Төлбөрийн нэхэмжлэл | **N/A** — API usage token бүртгэл нь billing receipt биш |
| Production PostgreSQL/Chroma touched | үгүй |

Smoke-ийн top-5 candidate diagnostic: DOC0086-ийн Монгол кирилл variation-д evidence chunk recall `0.5`, English болон draft Latin variation-д `0.0`; DOC0133-ын гурван variation-д `1.0`. Энэ хоёр асуултын үр дүнг corpus retrieval quality, verified cross-language recall, эсвэл strategy-ийн superiority гэж тайлбарлаж болохгүй. GT нийт 2 verified question тул retrieval evaluation **NOT READY**.

## Нууцлал ба давтагдах байдал

Frozen input: `real-corpus-15b24382-v1`, dataset SHA-256 `15b24382aeba13e943382370f5a4bfb4ede50b4033805006bad692d23bf3d56a`; 154 input, 151 parse success, 3 Load failure. A0 config ба parser revision хоёул бүртгэгдсэн. Дэлгэрэнгүй token/count mapping болон Chroma run-ууд зөвхөн Git-ignored `instance/split-evaluation/embed-benchmark/` дотор.
