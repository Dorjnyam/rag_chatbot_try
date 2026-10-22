# Offline retrieval operational benchmark

## Туршилтын нөхцөл

- Run ID: `424b37001c92`; private artifact: `instance/retrieval-evaluation/runs/424b37001c92/result.json`.
- Private result SHA-256: `f177af523f28439399986bde63d8054815beffa36c40935e52f1655d31349c9c`.
- Frozen Load corpus hash: `15b24382aeba13e943382370f5a4bfb4ede50b4033805006bad692d23bf3d56a`; 151 successful document, 3 Load failure тусдаа хэвээр.
- A0 parsed output: 2,468 chunks; chunk IDs/text hashes өмнөх Phase 2 snapshot-тай тохирсон.
- Ground-truth smoke: 2 independent human-verified question, нийт 6 correlated language variation; нэг variation бүрээр 5 timed repetition; 1 warm-up/variation.
- BM25: бодит frozen extraction text. Dense: 1,536-d deterministic normalized random vector, text-тэй semantic холбоогүй. Chroma 1.5.9, NumPy 2.5.4, tiktoken 0.12.0 (`cl100k_base`).
- API 0; PostgreSQL access үгүй; production Chroma access үгүй; temporary private benchmark root ашиглав.

## Хэмжсэн үр дүн

| Stage | P50 ms | P95 ms | P99 ms | Тайлбар |
|---|---:|---:|---:|---|
| Query normalization | 0.054 | 0.064 | 0.065 | `query_candidates`, 30 sample |
| BM25 search | 649.30 | 705.74 | 705.89 | Frozen 151-doc corpus дээрх lexical search |
| Dense search | 18.65 | 22.51 | 23.21 | Mock vector-уудтай isolated Chroma; provider embedding ороогүй |
| RRF fusion | 0.107 | 0.162 | 0.163 | Candidate ranking merge |
| Source validation | 15.63 | 20.77 | 22.56 | Branch бүрийн manifest/content validation |
| Context assembly | 19.46 | 44.86 | 60.62 | Token count ба 8,000-token benchmark cap |
| Mock embedding creation | 0.142 | 0.158 | 0.172 | Deterministic NumPy vector, OpenAI биш |
| Total retrieval mechanics | 704.23 | 782.34 | 806.48 | Normalization, mock vectors, BM25, Chroma, fusion, validation, context нийлсэн |

Бусад хэмжүүр: BM25+manifest build **5,275 ms**; Chroma upsert **3,511 ms**; process peak working set **540,491,776 bytes (~515.3 MiB)**. Private BM25/manifest **21,950,455 bytes**, Chroma **79,738,700 bytes**. Mean candidate count: BM25 11.33, Dense 15.00, fused union 26.33, selected context 5.50. Context tokens P50 5,776, P95 6,167. Candidate duplicate rate 0.0 (энэ query set/run-д).

Environment: Windows 11 build 22631, Python 3.12.15, AMD64, 20 logical CPUs; installed versions Flask 3.1.3, Chroma 1.5.9, NumPy 2.5.4, tiktoken 0.12.0. Run manifest-д OS/CPU/package identity хадгалагдсан.

## Унших арга

BM25 P50 нь нийт latency-ийн гол хэсэг. Энэ хэмжилт нь одоогийн artifact hash/canonical checks орсон; profile хийхээс өмнө cache/optimization санал болгохгүй. Уг хугацаа host, Windows I/O cache, 151-version scan-аас хамаарна. 30 sample-ийн P95/P99 нь 6 language variation × 5 run дээрх тайлбарлах статистик болохоос production SLA биш.

Random vectors нь query ба document text-ийн семантик холбоогүй тул Dense timing л хэмжинэ; Dense quality, Hybrid superiority, Mongolian retrieval accuracy гэж үзэж болохгүй. Нэмэлт embedding provider latency, request retries, PostgreSQL source lookup, generation latency, concurrent load хэмжээгүй. Memory нь Windows process peak working set бөгөөд Chroma native allocation, startup-ийг багтаана.

## Давтагдсан harness засварын тэмдэглэл

Эхний хоёр benchmark attempt query variation dictionary-г language label-тай андуурч уншаад BM25 0 candidate үүсгэсэн тул invalid гэж private `instance/` run folders-д хадгалсан. Тэдгээрийг үр дүнгээс хасаж, query variation value-г ашиглах fix хийсний дараа `10d3cca198ee`-г хэмжив. Өмнөх `b11d79dd4c23` гүйлт нь parser loader-г шалгахад хэрэглэсэн боловч memory instrumentation эцэслээгүй тул энэ тайлангийн үндсэн run биш. Historical private artifact-уудыг устгаагүй.
