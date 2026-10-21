# Split token overlap ablation

Run `95a117e7-e306-43f1-bc34-94d0c9ee217b`; snapshot `real-corpus-15b24382-v1`; dataset SHA-256 `15b24382aeba13e943382370f5a4bfb4ede50b4033805006bad692d23bf3d56a`.
Zero-overlap A2/A3-г дахин ажиллуулаагүй; өмнөх A1–A3 report-оос холбоно. Human-verified annotation 0; accuracy болон retrieval N/A. API/DB/Chroma calls: 0.

| Variant | Strategy | Requested overlap tokens | Chunks | Token median/P95/max | Coverage | Loss chars | Inflation | Actual span overlap chars/tokens estimate | Clause heuristic | Sentence heuristic | Table cuts | Latency P50/P95 ms | Python peak P95 |
|---|---|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| OF-51 | fixed-token-v1 | 51 | 5454 | 512.0 / 512 / 512 | 1.0000 | 0 | 1.1075 | 339423 / 244896 | 647/7808 heuristic | 4696 heuristic | 2582 heuristic | 66.108/277.688 | 5838017 |
| OF-77 | fixed-token-v1 | 77 | 5758 | 512.0 / 512 / 512 | 1.0000 | 0 | 1.1721 | 542761 / 393297 | 517/7808 heuristic | 5037 heuristic | 2736 heuristic | 67.981/297.095 | 4894236 |
| OF-128 | fixed-token-v1 | 128 | 6452 | 512.0 / 512 / 512 | 1.0000 | 0 | 1.3233 | 1024509 / 742852 | 341/7808 heuristic | 5700 heuristic | 3089 heuristic | 82.776/362.306 | 4892641 |
| OR-51 | recursive-token-v1 | 51 | 6817 | 437 / 508 / 512 | 1.0000 | 0 | 1.1328 | 437433 / 313900 | 197/7808 heuristic | 3267 heuristic | 3076 heuristic | 70.378/301.125 | 1325390 |
| OR-77 | recursive-token-v1 | 77 | 7246 | 435.0 / 508 / 512 | 1.0000 | 0 | 1.2157 | 702715 / 506311 | 205/7808 heuristic | 3473 heuristic | 3298 heuristic | 75.845/322.175 | 1325390 |
| OR-128 | recursive-token-v1 | 128 | 8315 | 437 / 509 / 512 | 1.0000 | 0 | 1.4190 | 1347739 / 977107 | 178/7808 heuristic | 4043 heuristic | 3840 heuristic | 91.417/409.511 | 1325390 |

Requested overlap нь target; actual span overlap нь offset intersection. Actual overlap token estimate нь overlap characters хэсгийг тусад нь `cl100k_base`-ээр tokenize хийсэн; chunk token sum/inflation-тэй адил үзүүлэлт биш. Clause/sentence/table тоо heuristic бөгөөд human accuracy биш.

## Reused zero-overlap baseline

| Strategy | Chunks | Token median/P95/max | Token inflation | Clause/table heuristic | Chunk latency P50/P95 ms |
|---|---:|---|---:|---|---:|
| A2 | 4,946 | 512 / 512 / 512 | 1.000 | 1,158 / 2,308 | 30.117 / 150.051 |
| A3 | 6,116 | 438 / 508 / 512 | 1.000 | 213 / 2,138 | 35.788 / 145.182 |

Baseline source: `split-a1-a3-ecf27e9c.json`; zero-overlap configurations were not rerun.
