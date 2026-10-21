# Phase 4 — Store: Chapter 6 traceability

**Төлөв:** хэрэгжүүлэлтийн audit дууссан; performance хэмжилт `RESULTS.mn.md`-д байна.  
**Хүрээ:** энэ trace нь Chapter 6-ийн онолыг шаардлага гэж шууд хуулбарлахгүй; бодит Flask + PostgreSQL + ChromaDB код, tests, offline хэмжилттэй тулгав.

## Chapter-to-code matrix

| Бүлэг | Судалгааны гол санаа | Одоогийн код / test нотолгоо | Ангилал ба нөлөө |
|---|---|---|---|
| **6.1 Vector-store architecture** | Vector index ба relational source-of-truth-ийн хариуцлагыг салгах; vector payload-ийг source identity-тай холбох. | `app/models.py`: document/version, active version ownership FK; `app/services/vectorstore.py`: Chroma persistent collection; `app/services/ingestion.py`: Chroma write баталгаажсаны дараа PostgreSQL activation. `tests/test_integration.py`. | **Implemented and verified (lifecycle түвшинд).** Store benchmark-д application DB дуудахгүй. Cross-store атомик байдал байхгүй нь хязгаар хэвээр. |
| **6.2 Relational DB ба vector store** | Transaction, metadata, filtering, backup/recovery-ийн өөр өөр үүргийг зөв тодорхойлох. | PostgreSQL canonical version төлөв; Chroma collection нь `version_id` filter-тэй. Phase 3.5 recovery/reconcile tests. | **Implemented and verified with limitation.** PostgreSQL болон Chroma нь нэг ACID transaction биш. |
| **6.3 Exact nearest-neighbor** | Ижил metric/candidate set дээр exhaustive search нь ANN-ийн fidelity reference болно. | Production `VectorStore.query()` Chroma-д query дамжуулна; production exact reference байхгүй. `app/store_benchmark.py` нь stored vector-уудаас NumPy cosine exact top-k гаргана. | **Offline experiment implemented and tested.** Index fidelity хэмжинэ; relevance хэмжихгүй. |
| **6.4 Approximate nearest-neighbor** | ANN-ийн recall–latency–memory trade-off-ийг exact result-тэй харьцуулах. | Chroma collection; query нь `where={version_id: {$in: ...}}`. `tests/test_vector_restart.py` persistence smoke. `app/store_benchmark.py` synthetic exact-vs-Chroma. | **Implemented; synthetic index fidelity measured.** Production scale болон semantic quality нь тусдаа. |
| **6.5 HNSW** | Graph build/search тохиргоо тусдаа өртөгтэй; `M`, `efConstruction`, `efSearch`-ийг effective API-гаар батлах. | `app/services/vectorstore.py`: cosine, requested `batch_size=1`, `sync_threshold=1`. Chroma 1.5.9 isolated collection configuration probe: effective `max_neighbors=16`, `ef_construction=100`, `ef_search=100`, `sync_threshold=1`; `batch_size` буцаж харагдахгүй. | **Implemented; effective settings and alternatives measured offline.** `batch_size=1` нь effective collection option гэдэг нь батлагдаагүй. |
| **6.6 IVF** | Centroid partition ба probe-ийн нөлөө; жижиг corpus дээр training feasibility-г нотлох. | IVF / `nlist` / `nprobe` code эсвэл current Chroma configuration байхгүй. | **Deferred / not applicable to current Chroma experiment.** FAISS/pgvector суулгаагүй; Chapter-ийн candidate grid-ийг ашиглаагүй. |
| **6.7 Product Quantization** | Vector compression нь storage хэмнэх ч distortion, ranking өөрчилж болно. | PQ/IVF-PQ implementation байхгүй; synthetic benchmark float32 vectors хэрэглэнэ. | **Deferred.** Хэмжсэн memory bottleneck одоогоор шаардаагүй; precision-loss туршилт хийгдээгүй. |
| **6.8 Insert/update/delete/rebuild lifecycle** | Idempotency, version consistency, failure recovery, delete visibility, reconciliation. | `app/services/ingestion.py`, `app/services/vectorstore.py`; `tests/test_integration.py`: write/retry, activation fail/orphan repair, delete fail/retry, missing vector reconciliation, version filter. Phase 3.5 gate өмнөх full suite: 169 passed, 1 skipped. | **Implemented and verified for named failure cases.** Бүх OS/process crash window ба production-scale concurrency батлагдаагүй. Энэ task-д давхардсан lifecycle тест нэмээгүй. |
| **6.9** | Chapter-ийн 6.9 subsection. | Өгсөн DOCX-д 6.8-аас 6.10 руу дугаар үсэрсэн; 6.9 subsection байхгүй. | **Source document omits section.** Контент зохиож нөхөөгүй. |
| **6.10 Chroma, FAISS, pgvector** | Технологийн зорилго, filtering, operations-ийг workload-той харьцуулах. | Одоогийн stack PostgreSQL + Chroma; `chromadb` 1.5.9. FAISS/pgvector package/engine энэ туршилтад ороогүй. | **Current architecture implemented; alternatives deferred.** Ижил workload-той харьцуулалт хийгдээгүй тул engine winner байхгүй. |
| **6.11 Recall–latency–memory** | ANN Recall ба хүний relevance Recall-ийг ялгах; search-only latency-г provider/generation-оос салгах. | `app/store_benchmark.py` precomputed 1536-d vector, exact index recall, filtered query, latency, disk, peak RSS хэмжинэ. Өмнөх ground truth: 2 verified questions. | **Index fidelity/performance measured synthetically. Human relevance untested / not ready.** Монгол RAG accuracy дүгнэхгүй. |
| **6.12 Complexity** | Exact, HNSW, IVF, PQ алгоритмын asymptotic cost ба space-ийг бодит хэмжилтээс салгах. | Frozen Split тайлан 2,468 A0 chunk-ийг өгдөг; энэ Store benchmark-д N=2,468 нь хэмжээний цэг. Synthetic vector benchmark бодит query workload биш. | **Theory mapped; selected Chroma measurements collected.** Томьёо нь хэмжилт биш; N≤10,000-аас цааш scale-г дүгнэхгүй. |

## Architecture audit

- **PostgreSQL:** `Document` болон `DocumentVersion`-ийн canonical төлөв, version identity, `active_version_id`-ийн owner constraint.
- **ChromaDB:** `PersistentClient` (`app/services/vectorstore.py`), vectors/documents/metadatas, cosine HNSW; query нь идэвхтэй `version_id`-уудыг metadata filter-ээр хязгаарладаг.
- **Embedding identity:** `app/services/embedding_contract.py` collection metadata-д model contract fingerprint хадгалж, write/query дээр compatibility-г шалгана. Энэ benchmark production contract-ийг тойрч гарахгүй; synthetic collection тусдаа нэр/директортой, зөвхөн `benchmark_only` metadata-тай.
- **Ingestion/recovery:** Chroma write дараа ID set ба manifest тулган, дараа нь PostgreSQL version activate хийнэ. Хоёр store-ийн transaction атомик биш; reconcile нь orphan болон mismatch-ийг илрүүлдэг.
- **Benchmark isolation:** synthetic vectors санамсаргүй seed-ээр RAM-д үүснэ; Chroma temporary collection-д хадгалаад trial бүрийн дараа устгана. Production `instance/chroma`, PostgreSQL, API ашиглахгүй.

## Effective configuration ажиглалт

Chroma 1.5.9-д production code `configuration={"hnsw":{"space":"cosine","batch_size":1,"sync_threshold":1}}` хүсдэг. Шинээр үүсгэсэн disposable collection-ийн public `collection.configuration` дээр `batch_size` харагдаагүй; харин effective `sync_threshold=1`, `max_neighbors=16`, `ef_construction=100`, `ef_search=100`, `space=cosine` байсан. Иймээс тайлан `batch_size=1`-ийг **requested legacy field** гэж тэмдэглэж, бодит active option гэж нэрлэхгүй. Энэ ажлын хүрээнд production тохиргоог засварлаагүй.

Chapter 6 дахь HNSW/IVF/PQ complexity болон параметрийн жишээнүүд нь онолын санал; энэ repo-д хэмжсэн үр дүн биш. Хэмжсэн benchmark-ийн нөхцөл, тоонууд зөвхөн `RESULTS.mn.md` болон `EXPERIMENT_MANIFEST.json`-д байна.
