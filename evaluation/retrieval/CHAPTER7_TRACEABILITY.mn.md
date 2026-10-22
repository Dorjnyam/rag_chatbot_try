# Phase 5 — Chapter 7 Retrieval Traceability

**Төлөв:** кодын аудит болон offline correctness шалгалт. Судалгааны санал нь заавал хэрэгжүүлэх спецификаци биш.

## Судалгаанаас код руу

| Chapter 7 | Хамаарах санаа | Одоогийн хэрэгжилт ба кодын нотолгоо | Баталгаажуулалт / үлдсэн эрсдэл | Ангилал |
|---|---|---|---|---|
| 7.1 Information Retrieval үндэс | Corpus, query, relevance, ranked result-ийг ялгах | `app/services/retrieval.py::retrieve`, version-scoped active query; `app/services/sparse.py::search` | BM25 mathematical known-answer бий. Human relevance нь 2 verified question тул representative үнэлгээ биш. | Partially implemented |
| 7.2 Inverted index, TF–IDF, BM25 | Lexical index, TF, document length, IDF | `app/services/sparse.py::tokenize`, `write_index`, `load_index`, `search`; JSON version index | `tests/test_core.py::test_bm25_known_answer_scores_use_tf_length_and_robertson_idf`, corruption/identity tests. Mongolian morphology/stemming-ийн чанар хэмжигдээгүй. | Implemented and verified |
| 7.3 Dense semantic retrieval | Embedding-space-д ойр vector хайлт | `app/services/retrieval.py::dense_search`, `app/services/vectorstore.py::query`, OpenAI client | Production model compatibility нь Embed contract-аар шалгагдана. Энэ Phase benchmark-д deterministic random vector хэрэглэсэн; semantic relevance хэмжээгүй. | Implemented, operationally tested; quality untested |
| 7.4 Sparse retrieval | Exact term/identifier matching | BM25 дээр policy ID, date, clause-г token болгон хадгална; `app/services/language.py::query_candidates` exact token хамгаална | BM25 synthetic known-answer; Монгол Cyrillic/Latin цөөн fixture. Бодит query recall N/A. | Implemented and partially verified |
| 7.5 Hybrid architecture | Sparse+dense candidate set-ийг eligibility-тэй нэгтгэх | `retrieve()` active version, embedding compatibility, dense ба sparse branches, degraded fallback | Provenance бүр branch дээр RRF-ээс өмнө шалгагдана; isolated tests. Failure behavior production concurrency-д тусад нь load-tested биш. | Implemented and verified with limits |
| 7.6 Reciprocal Rank Fusion | Нийцэхгүй raw scores-ийг нийлүүлэхгүй, rank-ээр fusion хийх | `app/services/sparse.py::rrf`; `RRF_K=60` тохиргоо | Known-answer, tie-break, duplicate-per-branch, conflict tests. Raw BM25/distance-г RRF score-той хутгахгүй байхаар branch тус бүрт хадгалдаг болсон. | Implemented and verified |
| 7.7 Top-k, candidate pool | Retrieval K ба final context selection-ийг тусад нь удирдах | Dense/Sparse top-k 15; service `k ∈ {1,3,5,10,20}`; context assembler `app/services/rag.py::build_context`; chat route input validation `app/routes/chat.py` | Service/route allowed K жагсаалт өөр. Route-н зөвшөөрсөн утгууд service-т хүчинтэй бол энэ нь одоогийн хэрэглэгчийн request-д defect биш; public contract болгон баталгаажаагүй. | Implemented, contract discrepancy documented |
| 7.8 Metadata/filter retrieval | Active version, document, language scope-ийг candidate болон context-д барих | `active_versions`, `still_active`, `sparse.search(eligible_ids)`, Chroma version filter, provenance manifest | Wrong document/version, invalid manifest, hash mismatch, inactive-version flow tests. DB/vector cross-store race бүрэн баталгаажаагүй. | Implemented and verified with limits |
| 7.9 Multi-query/query expansion | Query хувилбаруудыг candidate retrieval-д ашиглах | `app/services/language.py::query_candidates`; top 3 candidate branches, current env `QUERY_REWRITING_ENABLED=false` | Exact IDs/date/clause/negation preservation fixture. Transliteration/translation coverage хязгаарлагдмал. LLM rewriting унтраалттай. | Implemented conservatively; partially verified |
| 7.10 Rewriting/decomposition | Query rewrite эсвэл subquestion generation | Одоогийн approved baseline-д идэвхгүй; LLM rewrite/decomposition код ашиглахгүй | Human relevance baseline, query-type annotation дутуу учир хэрэгжүүлэх үндэслэл алга. | Deferred |
| 7.11 Similarity threshold, fallback | Score threshold болон safe fallback/abstention | Dense distances хадгалагдана; sparse index missing үед degraded dense fallback; `app/services/rag.py::ask` source verification | Одоогоор үнэлгээний evidence-ээр тогтоосон similarity threshold байхгүй. RRF score-г cosine similarity гэж тайлбарлахгүй; fallback correctness tests бий. | Partially implemented; threshold tuning deferred |
| 7.12 Algorithms, complexity, comparison | Algorithmic cost ба reproducible comparative evaluation | `app/retrieval_benchmark.py`, `benchmark-retrieval offline`; BM25, mock-vector Chroma, RRF, provenance, context stages | 6 query variants × 5 timed runs; өртөг нь host/snapshot/config-т хамаарна. Semantic quality, real OpenAI provider latency хэмжээгүй. | Operational benchmark implemented; relevance comparison untested |

## Одоогийн тогтмол baseline

`RETRIEVAL_MODE=hybrid`, `DENSE_TOP_K=15`, `SPARSE_TOP_K=15`, `RRF_K=60`, `QUERY_REWRITING_ENABLED=false`, `RERANKING_ENABLED=false`. Production model, parser, splitter, PostgreSQL, ChromaDB өөрчлөгдөөгүй. Chapter-ийн tau/k жишээг production optimum гэж аваагүй.

## Гол research gap

Chapter 7-ийн relevance метрик (Recall@k, MRR, nDCG) тооцоход query бүрийн human-verified relevant chunk ID/evidence шаардлагатай. Одоогийн `gt-v1` нь 2 асуулт, 6 хэлний variation; 6 variation нь 6 бие даасан асуулт биш. Иймд эдгээр хэмжүүр N/A бөгөөд synthetic vector-аас semantic дүгнэлт хийхгүй.
