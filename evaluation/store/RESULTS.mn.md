# Phase 4 — Store: benchmark үр дүн ба readiness

## Executive summary

**S1 traceability, S2 lifecycle review, S3 exact-vs-ANN, S4 synthetic scale benchmark дууссан.** Chroma 1.5.9 дээр deterministic synthetic 1536-d float32 vectors ашигласан 28 isolated trial, 2 давталт, N=1,000 / 2,468 / 5,000 / 10,000 хэмжээнд ажиллав. Тус бүрт 24 тогтмол query, unfiltered болон metadata-filtered candidate set-үүд шалгасан. Нийт 135,744 synthetic vector instance upsert хийгдсэн. **API calls 0; PostgreSQL болон production Chroma-д хандаагүй.**

Гол олдвор: HNSW search-only latency хэдэн миллисекунд байсан боловч random өндөр хэмжээст synthetic corpus-ийн ANN `Recall@10` N=2,468 дээр ойролцоогоор **0.75**, N=10,000 дээр **0.38–0.39** байв (M=16, `ef_search=100`). M=8 graph нь үүнээс бага recall үзүүлсэн. Энэ бол **index fidelity-ийн synthetic үр дүн**; company-policy Монгол асуултын relevance/accuracy биш. Иймээс production index тохиргоог өөрчлөхгүй.

## Туршилтын нөхцөл ба хувийн artifact

- Run ID: `d11ef3d2a1e0`; үр дүнгийн SHA-256: `fbb83fb5a463e513fd5c1ca4f2c4c439bb7b433ed7d4197b7ba45a350387ea29`.
- Private, Git-ignored run JSON: `instance/store-evaluation/runs/d11ef3d2a1e0/result.json`.
- Windows 11, Python 3.12.15, ChromaDB 1.5.9, NumPy 2.5.4, CPU logical count 20.
- Run эхлэхэд available RAM ~1.72 GiB, free disk ~54.2 GiB. Guard: RAM ≥1 GiB, free disk ≥2 GiB, N ≤10,000.
- Ижил N-ийн давталт бүр ижил synthetic vector/query seed ба SHA-256-тай. Хоёр давталт нь run-to-run хэлбэлзлийг ажиглахад ашиглагдсан; статистикийн баттай дүгнэлтэд хангалтгүй.
- HNSW index-ийг isolation temporary Chroma directory-д үүсгэж, client reopen + retrieval хийсний дараа тухайн trial directory-г устгасан. Aggregate хэмжилт, synthetic matrix-ийн hash л private JSON-д байна; vector payload ороогүй.
- Exact cosine reference нь тухайн Chroma collection-оос буцааж авсан vector-уудыг ашигласан. Filter тус бүрийн Chroma `get(where=...)` eligible ID set нь exact reference-д ашигласан ID-тай яг таарсныг trial бүрээр баталсан.
- Өгөгдсөн 2,468 нь Phase 2 A0 chunk count-той тэнцүү **synthetic хэмжээний цэг**; бодит Chroma collection-оос vector уншсан гэсэн үг биш.

## Effective Chroma тохиргоо

Production код cosine, `batch_size=1`, `sync_threshold=1` хүсдэг. Chroma 1.5.9 дээр isolated collection-ийн public `configuration`-оос дараах effective утгууд ажиглагдав:

| Тохиргоо | Effective утга |
|---|---:|
| Distance space | cosine |
| `sync_threshold` | collection-аас өгсөн утга (1 эсвэл 1,000) |
| `max_neighbors` (HNSW M) | 16; alternative 8 |
| `ef_construction` | 100 |
| `ef_search` | collection үүсгэхдээ 50 эсвэл 100 |
| `batch_size` | public effective configuration-д харагдаагүй |

Иймд `batch_size=1`-ийг active HNSW buffer гэж батлаагүй. `ef_search`-ийг runtime `modify()` хийх probe-д config утга солигдсон ч recall өөрчлөгдөөгүй тул эцсийн runner search parameter-ийг collection үүсгэхэд тогтоосон тусдаа collection-уудаар харьцуулсан. Index construction тохиргоо (`sync_threshold`, `max_neighbors`, `ef_construction`) болон search тохиргоо (`ef_search`) тусдаа тайлагнагдана.

## Chroma build, upsert, storage ба search

Доорх утгууд нь 2 trial-ийн average; latency баганууд нь тухайн trial бүрийн 24 warm query-ийн P50/P95/P99-ийг дараа нь дундажласан. Search time нь зөвхөн бэлэн query vector дээрх Chroma query; embedding, PostgreSQL, reranking, generation ороогүй. `sync_threshold=1` нь production-requested current; `sync_threshold=1,000` бол isolated alternative. Аль аль нь `max_neighbors=16`, `ef_construction=100`, `ef_search=100`.

| N | Тохиргоо | Build ms | Upsert vector/s | Disk MiB | Peak process RSS MiB | Unfiltered P50/P95/P99 ms | Index R@1 / R@5 / R@10 | Filter P95 ms | Filter R@10 |
|---:|---|---:|---:|---:|---:|---:|---|---:|---:|
| 1,000 | sync=1 | 629 | 1,833 | 7.47 | 195.5 | 1.51 / 2.32 / 5.62 | .979 / .958 / .956 | 3.37 | .998 |
| 1,000 | sync=1,000 | 898 | 1,396 | 14.31 | 214.3 | 1.61 / 3.79 / 6.13 | .958 / .954 / .956 | 4.03 | .998 |
| 2,468 | sync=1 | 1,443 | 1,815 | 16.75 | 280.0 | 2.24 / 2.61 / 2.78 | .729 / .783 / .771 | 5.46 | .931 |
| 2,468 | sync=1,000 | 1,050 | 2,472 | 21.12 | 295.2 | 2.39 / 2.84 / 2.96 | .667 / .754 / .752 | 6.05 | .927 |
| 5,000 | sync=1 | 3,257 | 1,560 | 32.73 | 403.2 | 2.92 / 3.44 / 3.59 | .521 / .567 / .562 | 10.00 | .744 |
| 5,000 | sync=1,000 | 2,330 | 2,192 | 34.03 | 444.3 | 2.99 / 3.42 / 3.50 | .583 / .542 / .552 | 9.96 | .773 |
| 10,000 | sync=1 | 8,620 | 1,182 | 64.28 | 582.3 | 3.74 / 5.88 / 8.04 | .375 / .438 / .394 | 16.41 | .519 |
| 10,000 | sync=1,000 | 5,417 | 1,865 | 66.34 | 638.4 | 3.18 / 3.89 / 6.40 | .354 / .388 / .377 | 16.67 | .535 |

Filtered row нь синтетик `segment=A` filter, N-ийн ойролцоогоор тал eligible байхаар тохируулсан. Энэ нь authorization test биш. `small_candidate_set` filter-т 3 eligible vector байхад Chroma бүх 3-ыг буцаасан; k=5 ба k=10-д eligible count хүрэхгүйг `requested_k_less_than_eligible` талбарт тэмдэглэсэн. Metric denominator нь `min(k, eligible_count)`.

### Search `ef_search` харьцуулалт

Одоогийн construction тохиргоо (sync=1, M=16, efConstruction=100) тогтмол; өөр collection-уудыг үүсгэхдээ efSearch-ийг өөрчилсөн.

| N | efSearch | Unfiltered P95 ms | Index R@10 | Filter P95 ms | Filter R@10 |
|---:|---:|---:|---:|---:|---:|
| 1,000 | 50 | 2.85 | .794 | 3.55 | .948 |
| 1,000 | 100 | 2.32 | .956 | 3.37 | .998 |
| 2,468 | 50 | 2.37 | .565 | 5.02 | .802 |
| 2,468 | 100 | 2.61 | .771 | 5.46 | .931 |
| 5,000 | 50 | 2.83 | .337 | 8.36 | .531 |
| 5,000 | 100 | 3.44 | .562 | 10.00 | .744 |
| 10,000 | 50 | 3.75 | .233 | 13.83 | .312 |
| 10,000 | 100 | 5.88 | .394 | 16.41 | .519 |

Энэ synthetic workload-д efSearch 100 нь recall-ийг сайжруулсан; latency ихэнх хэмжээ/фильтрт өссөн эсвэл ойролцоо байв. Энэ нь OpenAI embedding болон бодит query-д ижил үр дүн гарна гэсэн баталгаа биш.

### HNSW graph-ийн жижиг хувилбар

M=8, sync=1,000, efSearch=100-ийг мөн sync=1,000/M=16 хувилбартай харьцуулсан:

| N | M | Build ms | Disk MiB | Unfiltered P95 ms | Index R@10 | Filter R@10 |
|---:|---:|---:|---:|---:|---:|---:|
| 2,468 | 16 | 1,050 | 21.12 | 2.84 | .752 | .927 |
| 2,468 | 8 | 1,007 | 21.00 | 2.51 | .560 | .748 |
| 10,000 | 16 | 5,417 | 66.34 | 3.89 | .377 | .535 |
| 10,000 | 8 | 5,417 | 65.79 | 3.00 | .206 | .310 |

M=8 бага latency/storage авчирсан ч synthetic exact neighbor overlap буурсан. Disk-ийн зөрүү бага байсан; graph бууруулах нь энэ хэмжээнд storage-ийн том хэмнэлт өгөөгүй.

## Exact-vs-ANN correctness

- k=1/5/10 нь Chroma top-10 result-ийн prefix-аас тус тус тооцогдож, exact NumPy cosine top-k-тэй stable `(distance, ID)` дарааллаар тулгасан.
- Бүх 28 trial, 3 filter group дээр Chroma filter eligible ID set exact reference-тэй тэнцсэн; бүх query-д буцаасан result count `min(k, eligible_count)`-тэй таарсан.
- Cosine distance discrepancy нь Chroma ба exact давхцсан ID-ууд дээр тооцогдсон; бүх trial дахь absolute max **1.19×10⁻⁷**, float32 precision-тэй нийцсэн.
- Яг distance tie ажиглагдаагүй; exact comparator ID-гаар тогтвортой tie-break хийдэг.
- Index Recall нь ANN top-k-г яг ижил stored vectors/candidate set-ийн exhaustive cosine top-k-тэй харьцуулсан. Энэ нь human relevance Recall биш.
- N=10,000 дээр M=16/efSearch=100 `Index Recall@10` ~.38–.39; M=8 ~.21. Random 1536-d isotropic vectors дээр exact nearest нь маш ойр зайтай учир HNSW rank алдах боломжтой. Үүнийг бодит policy embedding-ийн чанарын үзүүлэлт гэж үзэхгүй.

## Cold/reopen ба variability

- Fresh Chroma client reopen startup average ~22–36 ms байв; fresh client-ийн эхний query N өсөхөд ерөнхийдөө ~19–203 ms range-д байсан. OS page cache-г цэвэрлээгүй тул үүнийг cold disk/машин boot latency гэж тайлбарлахгүй.
- Warm query latency нь per-filter нэг warm-up query-г хассан 24 query дээр хэмжигдсэн.
- Persistence test бүрт client хааж шинэ PersistentClient нээгээд ID/vector dimension parity, filtered query smoke шалгасан.
- Build repeat spread (`(max-min)/mean`) хамгийн их нь current 10k дээр **21.5%**, sync=1,000 1k дээр **65.2%** байв; хоёр run-ийн аль нэг startup outlier нөлөөлсөн. Иймээс build-time аль нэг тохиргооны жижиг давууг баттай гэж үзэхгүй. N=10k current throughput 1,182/s vs sync=1,000 1,865/s гэсэн чиглэл хоёр trial-д ижил боловч илүү олон давталт хэрэгтэй.
- Peak RSS нь Windows native WorkingSet sampler-аар хэмжсэн тухайн trial үеийн Python+Chroma process-ийн хэмжээ; vector-only memory биш. Дээд утга энэ run-д **648.7 MiB** байсан. OS cache болон production host-ийн memory budget энэ хэмжилтэд ороогүй.

## Real vector боломж

Одоо байгаа Embed smoke artifact-уудын plan нь `PLAN_ONLY_NO_API_OR_CHROMA`, usage=0; хадгалсан Chroma smoke result нь `local_mock`. Баталгаатай, API-аар үүсгэсэн real OpenAI vector snapshot олдоогүй. Production `instance/chroma`-г шалгаж нээгээгүй. Иймээс real-vector exact-vs-ANN харьцуулалт **хийсэнгүй**. Шинэ API call хийгдээгүй.

## S5 — архитектурын feasibility

- Phase 2 A0 corpus-ийн 2,468 chunk, d=1,536 float32 raw payload онолоор `2468×1536×4` = **15,163,392 bytes (~14.46 MiB)**. Энэ нь бодит Chroma disk/RSS биш.
- Synthetic N=2,468 дээр тусгаарласан Chroma directory ~16.75 MiB (sync=1) эсвэл ~21.12 MiB (sync=1,000); peak process RSS ~280–319 MiB хэмжигдсэн. Энэ нь тусгай benchmark runner, OS, config-ийн үр дүн.
- sync_threshold 1,000 synthetic upsert throughput/build-д сайжрал үзүүлсэн ч disk өссөн, high-level durability semantics өөр байж болно. Production config өөрчлөхийг зөвлөхөд crash-durability/flush semantics болон бодит vectors дээрх давтан шалгалт дутуу.
- pgvector нь state/filter-ийг PostgreSQL-д нэгтгэх боломжтой ч одоогийн lifecycle architecture-аас миграци, индексийн тохируулга, DB resource isolation шаардана. Одоогийн scale хэмжилтээр энэ complexity-г зөвтгөх асуудал илрээгүй.
- FAISS нь index parameter-ийн нарийн хяналттай байж болох ч metadata/version/ACL mapping, persistence ба reconciliation-г өөрөө шийдэхгүй. FAISS/pgvector суулгаагүй, туршилт хийгээгүй.
- Иймд одоогоор Chroma + PostgreSQL архитектурыг хэвээр хадгал; production тохиргоог өөрчлөхгүй. Хэрэв бодит workload дээр recall/latency асуудал илэрвэл эхлээд real-vector exact-vs-ANN, дараа нь representative query annotations дээр relevance evaluation хийнэ.

## Readiness decisions

### Operational Store Readiness — **READY WITH DOCUMENTED LIMITATIONS**

Phase 4-ийн PostgreSQL-backed relevant regression **23 passed**; benchmark-specific tests **4 passed**. Эцсийн бүрэн suite **173 passed, 1 skipped, 0 failed**, 78.97 секунд; 83 warnings (76 migration `get_engine()` deprecation, 7 Chroma legacy configuration deserialization). Critical lifecycle integrity blocker илрээгүй. Гэхдээ cross-store atomic transaction байхгүй, бүх crash/concurrency window туршигдаагүй, production data руу энэ ажлын хүрээнд хүрээгүй.

### Index Performance Evaluation — **COMPLETE WITH LIMITATIONS**

S3/S4 synthetic correctness/scale matrix дууссан; effective Chroma config, persistence, filters, exact fidelity, latency, disk, RSS хэмжсэн. Энэ хэмжилтүүд нь зөвхөн Chroma 1.5.9, тухайн Windows host, synthetic random vectors, N≤10,000-д хамаарна. Production config/engine winner зарлаагүй.

### Human-Relevance Evaluation — **NOT READY**

Одоогийн human-verified dataset 2 асуулт/6 variation бөгөөд intended query types-ийн representative sample биш. Synthetic nearest-neighbor Recall нь Mongolian retrieval accuracy, relevance Recall@k, MRR/nDCG, citation correctness хэмжээгүй. E3 model comparison болон relevance benchmark deferred.

## Chapter 6-ийн дараагийн нотолгоонд суурилсан алхам

1. `batch_size=1` public effective config-д харагдахгүй тул production comment/config-ийн утгыг тусдаа review хийж шийдэх; энэ удаа production код өөрчлөөгүй.
2. `sync_threshold=1` vs 1,000-ийн durability/visibility trade-off-г бодит restart/crash injection-ээр илүү олон давталтаар хэмжихээс өмнө production-д өөрчлөхгүй.
3. Reviewer-ээр representative ground truth-ийг нэмэгдүүлсний дараа real OpenAI vector snapshot-ыг хувийн isolation-д гаргаж, ижил Chroma/exact candidate set дээр дахин benchmark хийх. API хэрэглэх шаардлага гарвал cost estimate болон approval-г урьдчилан авах.
4. HNSW/Chroma хүрээнд requirement хангах боломжгүй гэдэг бодит нотолгоо гарах хүртэл IVF/PQ, pgvector, FAISS руу шилжихгүй.

## Дахин гүйцэтгэх командууд

```powershell
# Бүх workload, 2 давталт, 24 deterministic query; API/PG/production Chroma ашиглахгүй
.\.venv\Scripts\python.exe -m flask --app app benchmark-store run --sizes 1000,2468,5000,10000 --repeats 2 --queries 24

# Хөнгөн smoke, гэхдээ үргэлж 1536-d vector
.\.venv\Scripts\python.exe -m flask --app app benchmark-store run --sizes 1000 --repeats 1 --queries 8

# Isolated Chroma benchmark unit tests
.\.venv\Scripts\python.exe -m pytest tests\test_store_benchmark.py -q -p no:cacheprovider --basetemp=instance\pytest-store-unit

# PostgreSQL + lifecycle regression; dedicated Phase 3.5 test cluster only
.\scripts\start-phase35-test-postgres.ps1
$env:TEST_DATABASE_ADMIN_URL = 'postgresql+psycopg://rag_phase35_test@127.0.0.1:55439/postgres'
$base = Join-Path $PWD ('instance\pytest-phase4-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory $base | Out-Null
.\.venv\Scripts\python.exe -m pytest tests\test_integration.py tests\test_vector_restart.py -q -p no:cacheprovider --basetemp=$base
Remove-Item Env:TEST_DATABASE_ADMIN_URL
```

Дахин ажиллуулсан `result.json` нь шинэ run ID-тай private artifact болно; commit хийхэд public тайлан дахь үр дүнтэй ID/SHA-г санаатайгаар шинэчилнэ.
