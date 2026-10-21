# Phase 2 — Split A0 үр дүн ба дараагийн алхам

## Хураангуй

**Шийдвэр: READY WITH DOCUMENTED LIMITATIONS.** Одоогийн production chunker-ийг өөрчлөлгүй бодит corpus дээр offline baseline, reproducible frozen snapshot, хэмжүүр, Chapter 4 matrix, дараагийн experiment plan гаргав. Retrieval болон human-verified accuracy хэмжээгүй тул chunk-size/algorithm quality сонгох шийдвэрийг одоогоор гаргахгүй.

## Baseline тохиргоо ба орчин

- Run `1dc80917-a602-4185-9c3a-f16cae209b21`; snapshot `real-corpus-15b24382-v1`.
- Dataset: бодит 154 DOC/DOCX, SHA-256 `15b24382aeba13e943382370f5a4bfb4ede50b4033805006bad692d23bf3d56a`; 151 raw extraction cache-ийг parser дахин ажиллуулахгүйгээр frozen snapshot-д хуулсан, 3 Load failure (`empty_document`) хэвээр.
- `ordered-blocks-v1`, `characters-v1`, 1800 Unicode character target, 250 character overlap. Tokenizer `cl100k_base` (`tiktoken 0.12.0`), model context `text-embedding-3-small`.
- Token-limit guard `OpenAIProvider.fit_chunks`, 7,900 token cap; production provider/client constructor, embedding API, PostgreSQL, ChromaDB дуудагдаагүй.
- Баримт бүр шинэ worker: нэг warm-up, таван timed pass. Run wall хугацаанд worker startup болон private input serialization багтана; per-document stage latency нь child доторх chunking/guard/token-counting ажиллагааг хэмжинэ.

## Бодит хэмжилт

| Хэмжүүр | Үр дүн | Тайлбар |
|---|---:|---|
| Бүх source input / өмнөх Load failure | 154 / 3 | Phase 1 бүртгэлтэй таарч байна |
| Split input / Split success / Split exception | 151 / 151 / 0 | Load success subset |
| Fitted chunk | 2,468 | Raw ба fitted chunk count 151 баримтад бүгд ижил; 7,900-token guard chunk нэмээгүй |
| Chunk token distribution | median 1,313; P95 1,459; max 1,730 | `cl100k_base`; max guard-аас доогуур |
| Estimated embedding input tokens | 2,667,761 | Ирээдүйн input estimate; billed usage биш |
| Document latency, chunk + guard + metric | P50 17.06 ms; P95 76.25 ms | Child process дотор; бүтэн wall throughput-той бүү андуур |
| Chunking P50/P95 | 1.00 / 4.12 ms | A0 benchmark worker дотор |
| Fitting guard P50/P95 | 5.19 / 21.04 ms | Local `fit_chunks`; API call биш |
| Tokenizer-only P50/P95 | 1.82 / 7.08 ms | Тусдаа probe; зөвхөн `cl100k_base.encode()` timed, 151 docs / 2,468 chunks |
| Tokenization + audit diagnostics P50/P95 | 10.81 / 50.82 ms | A0 тайлангийн combined stage; tokenizer-only дүнтэй андуурахгүй |
| Бүтэн run wall / throughput | 249.99 s / 0.604 document/s | 151 fresh worker, input дамжуулалт багтсан |
| Worker CPU | P50 28.13 ms; P95 106.25 ms | Нэг measured iteration-ийн CPU average; fresh worker тусгаарласан |
| Process peak working set | P50 166,600,704; P95 171,098,112; max 173,039,616 bytes | Windows `GetProcessMemoryInfo`; `tracemalloc` chunking allocation P95 286,705, max 389,448 bytes |
| Source-character coverage | Median 100%; non-whitespace loss 0 | Нэг баримтад 9 whitespace-only character skip болсон; text content loss биш |
| Sentence-boundary heuristic | 1,755 / 1,823 chunk boundary punctuation-д таараагүй | Punctuation-only diagnostic; Mongolian clauses/abbreviations болон paragraph boundary-г ялгахгүй тул accuracy биш |
| Emitted source-span overlap redundancy | Median 12.70% | Source char interval-аас тооцсон; BPE token inflation-оос тусдаа |
| Token inflation ratio | Median 1.145 | Давхардсан overlapping chunk token-уудын харьцаа; BPE контекстэд denominator нь group separator-тай source text; billed token биш |
| `partial_table` flag | 1,140 chunks / 134 documents | Flattened table block chunk boundary-г давсан heuristic; cell correctness баталгаа биш |
| Source page metadata | 0/151 баримтад байна | Page boundary accuracy N/A; zero crossing гэж батлахгүй |
| Clause count | Regex heuristic | Ground-truth recall/accuracy биш |
| Complete-clause, sentence, citation accuracy, retrieval Recall@k/MRR/nDCG | N/A | Human-verified reference/query annotations алга |
| API / DB / Chroma / actual index size | 0 / 0 / 0 / N/A | Offline run; index бүтээгээгүй |

Raw болон fitted output-ийн chunk text, ID hash хоёр A0 run-д бүх 151 баримтад тогтвортой таарсан. Эхний A0 run-д process peak RSS instrumentation байгаагүй тул timing/hash reproducibility check-д ашигласан ч final ресурс хэмжилтийн run биш; `split-a0-1dc80917` final гэж үзнэ.

Per-document stage хэмжүүр болон anonymous chunk summary: [baseline JSON](split-a0-1dc80917.json), [baseline CSV](split-a0-1dc80917.csv), [товч тайлан](split-a0-1dc80917.mn.md). Гурван Load failure болон parser warning/strategy холбоос JSON-д бий. Public output-д file нэр, source text, heading text, private mapping оруулаагүй.

Tokenizer-only timing ба per-document counts: [token-counting probe](token-counting-probe.json). Энэ нь API usage биш бөгөөд full baseline worker latency-д нэмээд тооцохгүй.

## Synthetic ба real Load baseline-ийн харьцуулалт

| Phase 1 dataset/stage | Тоо | Load latency | Throughput | Чанарын reference |
|---|---:|---:|---:|---|
| 16 synthetic DOCX — Load/parser | 16/16 success | P50 913.51 ms; P95 1,120.35 ms | 1.079 file/s | Ground truth байхгүй |
| 154 бодит DOC/DOCX — Load/parser | 151/154 success | P50 973.53 ms; P95 3,869.25 ms | 0.820 file/s | Ground truth байхгүй |
| 151 бодит баримт — Split A0 | 151/151 Split success | P50 17.06 ms; P95 76.25 ms stage latency | 0.604 file/s, process overhead багтсан | Human annotation байхгүй |

Synthetic ба real Load нь өөр corpus; file composition, size, feature-ээрээ ялгаатай учраас performance зөрөөг parser regression гэж дүгнэхгүй. Split A0 зөвхөн real corpus дээр хийгдсэн; synthetic Split comparison байхгүй. Load latency ба Split latency нь өөр үе шат, өөр process boundary-тай тул шууд speed-up харьцуулалт хийхгүй. Source: `evaluation/load/reports/load-20261021T040121Z.json`, `real-load-20261021T042112Z.json`.

## Synthetic block-scan scaling probe

Ижил 44,998-character canonical text, ижил 29 output chunk дээр зөвхөн input block count-ийг өөрчилсөн; chunk text hash дөрвөн хувилбарт таарсан.

| Block count | Chunking P50 |
|---:|---:|
| 1 | 0.304 ms |
| 10 | 0.313 ms |
| 100 | 0.485 ms |
| 500 | 2.881 ms |

Энэ нь synthetic microbenchmark. 500 block үед 1 block хувилбараас ойролцоогоор 9.5× удаан байсан нь одоогийн бүх block-span давтан шалгалтын зардал өсөж буйг харуулна; production corpus-ийн нийт удаашралын баталгаа биш. Деталь [probe JSON](complexity-probe.json)-д байна.

## Алдаа, хязгаарлалт ба эрсдэл

1. **Human reference байхгүй:** accuracy хэлбэрийн ямар ч тоог зохиомлоор гаргаагүй. 80 real query file болон 4 synthetic multilingual query бүгд `draft`.
2. **Table boundary дохио:** 134 баримтад chunk table block хуваасан гэж heuristic тэмдэглэв. Parser table-ийг flat text болгодог, merged cells давтагдан хэвлэгддэгийг Phase 1 interface tests аль хэдийн баталсан; энд cell-level fidelity эсвэл retrieval алдаа гэж нотлоогүй.
3. **Provenance:** DOC/DOCX parser page metadata гаргаагүй; chunk offsets нь normalized block/group-local character offsets, эх DOCX/PDF байрлалын verified span биш.
4. **Split complexity:** synthetic probe дээр block count өсөхөд latency өсөв. Одоогийн real document дотор chunking stage P95 4.12 ms, харин tokenization + audit P95 50.82 ms; эхлээд accuracy annotation болон дараагийн controlled comparison, дараа нь bottleneck optimization шийднэ.
5. **Parser capabilities:** automatic list marker/level, heading hierarchy, DOCX page provenance, table row/cell topology алга. Split algorithm эдгээрийг зохиож нөхөхгүй.

## Эрэмбэлсэн зөвлөмж

| Эрэмбэ | Зөвлөмж | Нотолгоо ба хүлээгдэх ашиг |
|---|---|---|
| **P0** | Representative 20 баримтын эх сурвалжтай human-verified clause/evidence annotation бөглөх. | Accuracy хэмжүүрүүд одоогоор N/A. Ингэснээр clause preservation, heading, citation/source-span accuracy болон Retrieval Recall@k/MRR/nDCG-г баталгаатай хэмжинэ. Template: [ground truth](ground_truth_template.mn.json). |
| **P1** | Эхний algorithm experiment: fixed token vs recursive 512, overlap 0. | Chapter 4.2–4.3 hypothesis-ийг хамгийн бага хувилбарын тоогоор шалгаж, boundary benefit-ийг annotation-тай холбож болно. Шинэ алгоритмийг энэ ажлын хүрээнд хэрэгжүүлээгүй. |
| **P1** | Table-heavy representative docs-д verified row/cell evidence annotation хийх; дараа нь block-aware/table-aware variant feasibility. | `partial_table` 1,140 chunk / 134 document гэж flag болсон, харин parser flatten/missing merged-cell topology-той. Парсерын алдагдлыг splitter дангаараа засахгүй. |
| **P2** | Fixed ба recursive 256/512/1024 token size grid, overlap 0. | Size-ийн нөлөөг overlap-аас салгана; retrieval claims-д ижил reviewed query, vector space, reranker, top-k, context budget шаардана. |
| **P2** | 400-token target дээр 0/40/80/160 overlap ablation. | Source-span redundancy ба token/index/context зардлын бодит муруй гаргана. |
| **P2** | Synthetic scaling дээр block-span scanning дахин хэмжих; зөвхөн real-corpus latency budget-д нөлөөлвөл optimize хийх. | 500 vs 1 block synthetic P50 9.5×; харин бодит chunking P95 4.01 ms, тиймээс шууд optimization шаардлагатайг нотлоогүй. |
| **P3 / deferred** | Semantic болон Parent–Child retrieval. | Эхлээд simpler baseline-аас үлдсэн verified topic-boundary/multi-clause context алдааг хэмжих; Semantic нэмэлт embedding cost, Parent–Child retrieval/context assembly/version authorization өөрчлөлт шаардана. |

Төлөвлөсөн experiment бүрийн hypothesis, baseline, variables, dataset, metric, cost/complexity, failure analysis загвар: [experiment plan](EXPERIMENT_PLAN.json). Одоогоор бүгд `planned_not_executed`.

## Chapter 4 traceability ба test verification

- Нарийвчилсан 4.1–4.10 mapping, code/test evidence, interface алдагдал: [traceability report](CHAPTER4_TRACEABILITY.mn.md).
- Frozen snapshot: `instance/split-evaluation/real-corpus-15b24382-v1/`; Git-д үл хамаарна. Freeze command hash болон artifact integrity-г шалгаж, дахин freeze хийхэд immutable snapshot-ийг reuse хийнэ.
- Команд: `flask --app app benchmark-split freeze`; `flask --app app benchmark-split run`; `flask --app app benchmark-split complexity-probe`; `flask --app app benchmark-split token-count-probe`.
- Scoped tests: `tests/test_split_benchmark.py`, `tests/test_load_split_interface.py` — **5 passed**. pytest global cache-д ACL warning гарсан ч test-ийн үр дүн амжилттай; project доторх basetemp ашигласан.
- `app/services/chunking.py`, parser implementation, production PostgreSQL болон ChromaDB өөрчлөгдөөгүй.
