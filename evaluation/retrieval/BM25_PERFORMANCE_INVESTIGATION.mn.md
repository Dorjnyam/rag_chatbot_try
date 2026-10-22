# Phase 5.1 — BM25 Latency Investigation and Retrieval Performance Gate

**Төлөв:** investigation, evidence-based optimization, correctness gate дууссан. Хэмжилт offline, private frozen corpus дээр; relevance accuracy биш.

## Дүгнэлт

BM25 scoring өөрөө гол bottleneck байгаагүй. Гол root cause нь 151 active document-version-д 32 entry-ээр хязгаарлагдсан index LRU cache таарахгүй байсан явдал: corpus scan-аар өмнөх version cache-ээс хөөгдөж, дараагийн query бүрт index/manifest-ийг дахин уншиж шалган, cold validation ба term tokenization-г давтаж байв. Үүн дээр compiled corpus statistics cache key нь бүх eligible chunk-ийг query бүрт дахин JSON serialize/hash хийж байлаа.

Хязгаарлагдсан cache-ийг 256 болгож, compiled statistics key-г баталгаажсан per-version index/manifest content digest ба eligibility filter-ээр байгуулдаг болгов. `load_index()` файл болон manifest-ийн raw bytes-ийг query бүрт SHA-256-аар шалгах хэвээр; timestamp/size-д найдаагүй. Иймээс integrity/freshness хамгаалалтыг latency-ийн төлөө сулруулаагүй. Өгөгдлийн сан, Chroma, parser, splitter, embedding settings өөрчлөгдөөгүй.

`storage.path()`-д containment шалгалтын base directory-г дахин дахин `resolve()` хийж байсныг нэг удаа resolve болгож цөөлөв. Path traversal шалгалт хэвээр бөгөөд regression test-тэй.

## Хэмжилтийн нөхцөл

- Frozen A0: **151 document, 2,468 chunk**; Load snapshot SHA-256 `15b24382aeba13e943382370f5a4bfb4ede50b4033805006bad692d23bf3d56a`.
- Ижил query set: `gt-v1`-ийн **2 independent verified question, 6 correlated language variations**; текст тайлан болон private metrics-ээс бусад газар хадгалаагүй.
- `SPARSE_TOP_K=15`, BM25 `k1=1.5`, `b=0.75`, бүтэн eligible corpus. Warm: 1 warm-up/query, 6 variation × 5 repeat = **30 timed samples**.
- Үндсэн focused run: `a50bcc9413da`; private artifact `instance/retrieval-evaluation/bm25/a50bcc9413da/result.json`, SHA-256 `65dd6d75a378f3e013f82f1cf7653c7ddd779d1433940bcfd7d12af9921ab4ba`.
- Environment: Windows 11 build 22631, Python 3.12.15, AMD64, 20 logical CPU; Flask 3.1.3, Chroma 1.5.9, NumPy 2.5.4, tiktoken 0.12.0. BM25 profile-д Chroma/API огт ашиглаагүй.
- Харьцуулах өмнөх run (`424b37001c92`) ба final R6 run (`670e5417c260`) ижил snapshot, query variation, `top_k`, model/config-тэй. Тайлангийн өмнөх baseline тоо R6-ийн `PERFORMANCE_RESULTS.mn.md`-тэй таарна.

## Baseline ба final latency

| Хэмжилт | Өмнөх (unoptimized) P50 / P95 / P99 | Final P50 / P95 / P99 | Өөрчлөлт, P50/P95 |
|---|---:|---:|---:|
| BM25 search stage (R6 end-to-end harness) | 649.30 / 705.74 / 712.61 ms | 214.60 / 243.39 / 244.25 ms | −67.0% / −65.5% |
| Нийт offline retrieval mechanics | 704.23 / 782.34 / 793.95 ms | 270.96 / 322.05 / 340.42 ms | −61.5% / −58.8% |
| Focused warm BM25 search (instrumented, 30 samples) | — | 197.53 / 214.62 / 222.44 ms | CV 3.28%; min 194.78, max 222.44 ms |

R6 total хэмжилтэнд query normalization, mock-vector creation, synthetic Chroma query, BM25, RRF, provenance check, context assembly орно. Эдгээрийн давтагдах өртөг (API/DB биш) хэвээр. Cold vs warm focused latency-г дараагийн хүснэгтээр салгасан.

Final isolated R6 run (`670e5417c260`) дээр source metadata/provenance validation **13.07/16.43/16.58 ms P50/P95/P99**, query normalization `0.053/0.069/0.104 ms`, mock embedding `0.141/0.202/0.218 ms`, synthetic Dense `16.53/22.83/23.75 ms`, RRF `0.102/0.172/0.323 ms`, context assembly `23.66/55.04/63.25 ms` хэмжигдсэн. Эдгээр нь BM25 focused profile-н `index_load_validation_ms`-ээс тусдаа validation stage юм.

## BM25 stage breakdown

| Stage | Cold first query | Warm P50 / P95 / P99 | Утга |
|---|---:|---:|---|
| Index read + digest + cache/manifest validation | 499.16 ms | 192.38 / 209.67 / 218.11 ms | Warm үед 151 index болон manifest-ийг integrity-д зориулж уншиж/hash хийнэ |
| Eligibility filtering | 0.108 ms | 0.118 / 0.123 / 0.128 ms | Тус benchmark-д filter байхгүй; 2,468 eligible chunk цуглуулах үеийн зардал |
| Query tokenize | 0.0029 ms | 0.0094 / 0.0115 / 0.0118 ms | Монгол Unicode NFC + casefold tokenizer |
| Corpus-stat cache key | 1.002 ms | 0.955 / 0.996 / 1.003 ms | Index signature-үүд болон eligibility set-ээр |
| Corpus statistics build | 51.20 ms | 0 / 0 / 0 ms | Cold-д нэг удаа; warm 30/30 cache hit |
| Candidate scoring | 3.77 ms | 3.334 / 4.030 / 4.070 ms | Full scan: бүх 2,468 eligible chunk; нэг query-д дунджаар 684 score > 0 candidate |
| Final ranking + top-k | 0.152 ms | 0.0105 / 0.1866 / 0.1933 ms | Match болсон candidate-уудыг stable ID tie-break-тай sort, эхний 15-ыг авах |
| Нийт `sparse.search()` | 555.76 ms | 197.53 / 214.62 / 222.44 ms | Instrumented focused run |

Warm P50/P95 нийт хугацааны ~97% нь index/manifest integrity read + digest validation хэвээр байна. Үүнийг арилгах нь same-size/timestamp corruption илрүүлэх одоогийн баталгааг сулруулж болзошгүй; энэ gate-д ийм trade-off зөвшөөрөөгүй. Path-resolution хэмнэлтээс өмнөх/дараах focused run-ууд P50 ~231.11-аас ~197.53 ms болж сайжирсан.

## Index lifecycle ба алгоритмын шалгалт

- **Index construction query latency-д ороогүй.** Тусдаа build хэмжилт final run-д manifest + BM25 JSON index бичихэд **5,229 ms wall time** (өмнөх R6 build хэмжилт 5,275 ms); энэ нь initialization/build cost, per-query cost биш.
- Эхний application-cache miss query `555.76 ms`; үүнд бүх 151 index анх validate/tokenize хийх, corpus statistics-ийг 51.20 ms build хийх зардал орно. Энэ нь empty Python cache бөгөөд OS file cache flush хийгдээгүй, “cold disk” гэсэн хэмжилт биш.
- Warm 30 query-д **4,530/4,530 index cache hit, 0 miss**; **30/30 corpus-stat cache hit, 0 rebuild**.
- Өмнөх cProfile-д `sparse.search` 151 `load_index` дуудалттай, corpus record бүрт `tokenize` (2,469 удаа), 302 JSON artifact read, олон мянган fingerprint/serialize ажиглагдсан. `CACHE_LIMIT=32 < 151` тул LRU-гийн 32-entry cache бүтэн corpus scan-ыг барих боломжгүй байв.
- Scoring нь inverted index биш; eligible records дээр full scan хийдэг. Энэ corpus дээр scoring ~3.3 ms тул үүнийг өөрчлөх нотолгоо алга.
- `eligible_ids` шүүлтүүр нь `documents` үүсгэх үед, corpus stats ба scoring-оос **өмнө** хэрэгжинэ. `retrieve()` түүнээс ч өмнө DB-ээс active/ready version сонгодог; sparse search өөрөө DB-д ханддаггүй. Энэ offline timing-д active-version SQL query ороогүй, DB latency-г хэмжээгүй.
- `write_index()` болон `delete_index()` нь version cache entry-г хүчингүй болгож, compiled corpus-statistics cache-г цэвэрлэдэг. Шинэ cache keys raw index SHA-256, manifest SHA-256, data-root, eligibility set-ийг агуулна. Шинэ version идэвхжихэд `active_versions()` шинэ version-ийг л BM25-д өгнө; хуучин version cache-д түр байж болох ч final candidate/context-д нэвтрэхгүй. `rag.ask()` мөн generation өмнө идэвхийг дахин шалгана.

## Correctness ба санах ой

- Одоо ажиллаж буй search ба uncached reference implementation-ийн BM25 томьёог **30 query comparison** дээр шалгав: stable chunk ID-ийн эрэмбэ **30/30 яг таарсан**, score absolute error `≤1e-12`.
- Existing BM25 known-answer (`k1`, `b`, IDF, term frequency, length normalization), Unicode, canonical manifest/hash, corrupt index, active-version provenance, RRF ordering/score-provenance tests бүгд үлдсэн.
- Шинэ туршилт 40 version (анхны 32-entry хязгаараас их) дээр warm давталтад stored corpus term-үүд дахин tokenize болохгүйг баталсан; query нэг удаа tokenize хийнэ. Cache hit-үүд болон stats cache hit-ийг шалгасан.
- Process peak working set focused run-д **132,755,456 → 203,034,624 bytes**; build ба in-memory cache бэлэн болсны дараах peak delta ~**67.0 MiB**. Үүнийг зөвхөн cache-д хамааруулж хэмжээгүй; corpus object, index build зэрэг process allocation-ууд багтана. Full Chroma retrieval run-ийн process peak ~**516 MiB** байсан.
- CPU wallтай ойролцоо процессын CPU time warm P50 **187.5 ms**; Windows process timer-ийн квантаас болж 15.625 ms алхамтай, index build CPU уншилт зарим run-д 0 гарсан тул CPU үр дүнг indicative гэж үзэж, нийт хэрэглэгчийн CPU utilization гэж тайлбарлахгүй.

## Бүрэн regression ба хязгаарлалт

Dedicated Phase 3.5 PostgreSQL cluster (`127.0.0.1:55439`, `rag_phase35_test`) дээр бүрэн suite: **196 passed, 1 skipped, 0 failed**, 87 warning, 82.23 секунд. JUnit `instance/test-results/phase51-bm25.xml`. Production DB/Chroma ашиглаагүй. Skip: `tests/test_native.py:52`, Tesseract байхгүй. Warnings нь SQLAlchemy `get_engine()` ба Chroma legacy embedding configuration deprecation.

Targeted retrieval tests: **54 passed, 0 failed**. `git diff --check` цэвэр.

Performance хэмжилт нэг Windows host, 151 баримт, 2 independent verified questions-ийн зургаан хэлний variation дээр; concurrency/load SLA биш.

Бодит retrieval relevance (`Recall@k`, MRR, nDCG, citation correctness) энэ ажлаар хэмжигдээгүй; mock dense vectors нь текстийн утгатай холбоогүй. Full-scan BM25 хэмжээнд 2,468 chunk дээр хангалттай хурдан боловч corpus ихсэхэд масштаблах дүгнэлт хийхгүй. Warm integrity read/hash нь үлдсэн гол bottleneck; түүнийг өөрчлөхөөс өмнө илүү том corpus буюу шаардлага/SLA нотолгоо хэрэгтэй.

## Шийдвэр

**PERFORMANCE VERIFIED WITH LIMITATIONS — READY FOR PHASE 6**

Энэ шийдвэр нь одоогийн 151-document frozen corpus дээр BM25 operational latency-г root cause-оор нь бууруулж, formula/ranking correctness-ийг баталсан гэсэн утгатай. Generation/retrieval relevance quality, production-scale concurrency, DB latency, real embedding provider latency-г баталсан гэсэн үг биш.

## Reproduction (PowerShell)

```powershell
# Focused cache/cold/warm BM25 profile, private result under instance/
.\.venv\Scripts\python.exe -m flask --app app benchmark-retrieval bm25-profile --iterations 5

# Full offline retrieval mechanics, synthetic dense vectors only
.\.venv\Scripts\python.exe -m flask --app app benchmark-retrieval offline --iterations 5 --seed 72026

# Focused reliability/correctness tests
.\.venv\Scripts\python.exe -m pytest tests\test_retrieval_reliability.py tests\test_retrieval_benchmark.py tests\test_core.py -q -p no:cacheprovider --basetemp=instance\pytest-phase51-targeted4

# Full regression suite on the dedicated isolated test PostgreSQL
.\scripts\start-phase35-test-postgres.ps1
$env:TEST_DATABASE_ADMIN_URL = 'postgresql+psycopg://rag_phase35_test@127.0.0.1:55439/postgres'
$base = Join-Path $PWD ('instance\pytest-phase51-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory $base | Out-Null
.\.venv\Scripts\python.exe -m pytest -q -rs -p no:cacheprovider --basetemp=$base --junitxml=instance\test-results\phase51-bm25.xml
Remove-Item Env:TEST_DATABASE_ADMIN_URL
```
