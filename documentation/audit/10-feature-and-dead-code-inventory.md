# 10 — Feature ба dead-code inventory

Классификаци: `ACTIVE`, `OPTIONAL`, `TEST_ONLY`, `CLI_ONLY`, `DISABLED`, `PARTIAL`, `UNKNOWN`. Reachability-г Flask route/CLI registration, direct caller, provider factory, runtime flag болон tests-ийн reference-ээр үнэлэв. `UNUSED` тэмдэглэгээг зөвхөн бүрэн итгэлтэй reference scan хийсэн тохиолдолд хэрэглэх ёстой; dynamic import/callback-ийг бүрэн баталгаажуулаагүй тул баталгаатай unused production module энд зарлаагүй.

| Бүрэлдэхүүн | Классификаци | Caller/flag | Баталгаа |
|---|---|---|---|
| Flask app factory, CSRF, error handlers | ACTIVE | app import / request lifecycle | Static; DB route execution partial |
| Document upload/review/process/delete | ACTIVE | documents routes, `flask process/approve/delete` | Static; lifecycle integration NOT TESTED |
| PDF/DOCX/DOC parsing + OCR/conversion | ACTIVE/PARTIAL | ingestion → parsing; native tool config | Synthetic/native parse tested; real OCR skipped |
| Chunking/provenance IDs | ACTIVE | ingestion, reindex | Core tests PASS |
| OpenAI provider | ACTIVE if selected | `AI_PROVIDER=openai` | Mocked SDK tests PASS; no live calls |
| Gemini provider | ACTIVE if selected/legacy | `AI_PROVIDER=gemini` or unset | Mocked SDK tests PASS; no live calls |
| Chroma dense index | ACTIVE | retrieval/ingestion | restart test PASS; DB activation untested |
| BM25 + RRF | ACTIVE default | `RETRIEVAL_MODE=hybrid` | unit test PASS; integration filters NOT TESTED |
| CrossEncoder reranker | OPTIONAL/DISABLED | `RERANKING_ENABLED`, local `RERANKER_MODEL_PATH` | Dependency missing; not enabled/tested |
| Conditional query rewrite | OPTIONAL/DISABLED | `QUERY_REWRITING_ENABLED` | Static; exception fallback defect confirmed |
| Sparse backfill/reconcile | CLI_ONLY | `flask backfill-sparse`, `flask reconcile` | Static; command integration NOT TESTED |
| Provider index migration | CLI_ONLY | `flask migrate-indexes ...` | Static + unit tests; PG activation NOT TESTED |
| Evaluation engine/review UI | ACTIVE | evaluation routes and CLI | metrics unit tests PASS; PG run/review NOT TESTED |
| Browser server harness | TEST_ONLY | `tests/browser_server.py` | Not run: hardcoded DB/repo-local side effects |
| Synthetic corpus | TEST_ONLY | tests/evaluation | Smoke not run (DB); multilingual synthetic unit coverage partial |
| Multimodal image/audio/video | NOT IMPLEMENTED | No active path found | Not penalized as broken; roadmap only |

Dependency and configuration matrix is in [11](11-configuration-and-dependencies-audit.md). No automated vulture/callgraph scanner was installed.
