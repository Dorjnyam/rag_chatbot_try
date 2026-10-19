# 13 — Оноо ба эрэмбэ

## Онооны арга

Ангилал бүрийн оноо 0–100. Дотоод дэд хэмжээс: correctness 35%, reliability 20%, verification 20%, maintainability 15%, security/operational safety 10%. Нийлбэр нь зөвхөн 26 үнэлж болох ангиллын онооны энгийн дундаж; `N/A` хоёр ангиллыг оруулаагүй. Confidence: HIGH нь code+execution, MED нь code+хэсэгчилсэн execution, LOW нь статик/inference давамгай. Энэ нь Монгол хэлний чанарын үнэлгээ биш.

| Ангилал | Оноо | Confidence | Гол шалтгаан |
|---|---:|---|---|
| Overall Architecture | 72 | MED | Ownership ойлгомжтой, shared service boundary байхгүй |
| Python Code Quality | 70 | MED | Унших боломжтой модуль, зарим service orchestration төвлөрсөн; lint/dead-code scan алга |
| Flask API Correctness | 68 | LOW | Static contract боломжийн, route integration DB safety-гээс шалтгаалан ажиллаагүй |
| Document Ingestion | 75 | MED | Review/verify/activate flow, orphan edge case |
| Chunking | 82 | HIGH | Determinism/offset/coverage tests PASS |
| Embedding Integration | 78 | MED | Both adapters and mocked SDK, live provider unavailable |
| Dense Retrieval | 73 | MED | Chroma restart PASS; full active filter integration untested |
| Sparse/BM25 | 68 | MED | Unicode metadata and RRF unit PASS; artifact integrity gap |
| Hybrid/RRF | 72 | MED | Fusion unit PASS, lifecycle/filter integration untested |
| Reranking | 58 | LOW | Optional path static; model dependency absent, no benchmark |
| Query Rewriting | 55 | MED | Bounded opt-in path, exception abort finding |
| Context | 75 | MED | Budget/dedup/citation logic present, database route not executed |
| Gemini Integration | 72 | MED | Mocked SDK and legacy config tested; live access unverified |
| Grounded Generation | 70 | MED | Structured schema/repair contract, no live model or corpus review |
| Citations | 78 | MED | Allowed-label contract and unit coverage; route behavior not integration-tested |
| Multilingual | 58 | MED | Conservative candidate handling; fixed lexicon and heuristic |
| Mongolian UI/Answer Quality | 55 | LOW | Cyrillic script validator is not language detector; visual blocked |
| PostgreSQL | 65 | LOW | Models and test code reviewed; DB suite blocked |
| ChromaDB | 72 | MED | Restart regression PASS; DB/index cross-store tests blocked |
| CrossStore | 68 | LOW | Stage/verify/activate logic; transaction integration not run |
| Test Quality | 70 | MED | Useful mocks/edge cases, DB destructive guard issue, 32 runtime pass |
| Security | 64 | LOW | Local guard/CSRF; no auth/ACL for shared use, no scanner |
| Performance | 62 | LOW | Synchronous/unbounded lists/N+1; no load test |
| Deployment | 60 | LOW | Local run documented; shared service design absent |
| Observability | 55 | LOW | Query timing/logs and doctor; health lacks readiness and trace privacy not verified |
| Documentation Accuracy | 72 | MED | Good local constraints; provider ownership/default wording drift |
| Maintainability | 68 | MED | Modular services/tests; docs and implementation drift, no coverage/linter |
| OpenAI Integration | 74 | MED | SDK mocks/config/token path; live API/model access and quality not verified |

**Онооны хамрах хүрээ:** хүссэн 27 ангилал + OpenAI Integration = 28/28 оноотой. `Multimodal capability` нь нэмэлт feature inventory note; scoring category биш. `Current dependency vulnerability status` мөн хүссэн онооны ангилал биш бөгөөд `N/A — Not verified` гэж тусад нь үлдээв.

**Нийлбэр audit score:** 68/100, 26 ангиллын simple mean. Энэ оноо нь reviewed company corpus дээрх RAG accuracy-г илэрхийлэхгүй; deployment readiness decision-ийг орлохгүй.

## Priority view

| Эрэмбэ | Finding | Priority | Effort | Хамаарал |
|---|---|---|---|---|
| 1 | Test DB destructive guard | P1 | Medium | Integration tests-ийн өмнө |
| 2 | Auth + document ACL | P1 | Large | Shared deployment-ийн өмнө |
| 3 | Rewrite fallback | P2 | Small | Rewrite enabled үед |
| 4 | nDCG gain consistency | P2 | Small | Benchmark comparisons-ийн өмнө |
| 5 | Sparse fingerprint integrity | P2 | Medium | Hybrid reliability |
| 6 | History pagination | P2 | Medium | Corpus/history өсөхөөс өмнө |
| 7 | Readiness endpoint | P2 | Small | Shared deployment |
| 8 | Mongolian language validator | P2 | Medium | Language acceptance |
| 9 | Upload orphan cleanup | P3 | Small | Filesystem hygiene |
| 10 | Query trace batch fetch | P3 | Small | Trace performance |

Top 10 defects нь AUDIT-001–010. Top 10 improvements нь дээрх дараалалтай. No P0; P1 issues deployment/test safety boundary дээр.

## Correction note — 2026-10-19

Эхний тайлангийн intro/footer дахь “26 ангилал, хоёр N/A” гэсэн өгүүлбэр нь хүснэгтийн 28 numeric ангилалтай зөрчилдсөн. Хүснэгтэд 27 шаардсан ангилал болон OpenAI Integration нийт **28** ангилал бий; тэдгээрийн энгийн дундаж 68.18. Таван component-ийн тусдаа оноо/нотолгоо тайланд байгаагүй тул өмнө зарласан weighted 68/100-ийг reproducible, баталгаажсан үнэлгээ гэж ашиглаж болохгүй. Энэ нь retrieval эсвэл model quality baseline огт биш. Түүхэн текстийг хадгалж, шинэ benchmark нь metric revision, reviewed labels, ижил corpus/split/final K болон per-language evidence ашиглах шаардлагатайг энд залрууллаа. Одоо хэрэгжсэн засваруудын acceptance нь шинэ тестийн үр дүнгээр тусад нь тайлагнагдана.
