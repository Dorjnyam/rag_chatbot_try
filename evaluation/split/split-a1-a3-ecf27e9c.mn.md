# Phase 2 — Ground truth preparation ба controlled A1-A3 experiments

Бүх хэмжилт parser-ийн ижил frozen output дээр. Human-verified evidence байхгүй тул retrieval чанарын дүгнэлт хийгээгүй.

- Run ID: `ecf27e9c-7558-41b5-ae09-c145937dfa7c`
- Dataset SHA-256: `15b24382aeba13e943382370f5a4bfb4ede50b4033805006bad692d23bf3d56a`
- Parser: `ordered-blocks-v1`; tokenizer: `cl100k_base` / tiktoken `0.12.0`
- Оролт: 154; Split inputs: 151; өмнөх Load failures: 3
- A1+A2+A3 нийлсэн wall time / throughput: 231.347 s / 0.653 docs/s; 151 document worker тус бүрт нэг warm-up + 5 хэмжилт.
- API / PostgreSQL / ChromaDB: 0; retrieval ба verified accuracy хэмжүүрүүд: N/A.
- Ground-truth package: 20 representative document, 80 candidate questions, тус бүр 3 draft query variant; private `instance/split-evaluation/annotation-review/` дотор.

## Туршилтын зорилго ба controls

| Strategy | Hypothesis (tested offline only) | Config / experimental variable | Controlled variables | Cost/complexity |
|---|---|---|---|---|
| A1 | Overlap=0 бол A0-ийн 250-char overlap-аас source-span redundancy болон estimated embedding tokens буурах эсэх. | `characters-v1`, target 1800, overlap 0 | Ижил frozen blocks/input order, `ordered-blocks-v1`, tokenizer `cl100k_base` 0.12.0, heading/page fences, offline execution | Embedding API cost = 0; chunk count/token estimate нь ирээдүйн index/cost input. Recursive ба fixed chunking CPU complexity тус тус хэмжигдсэн. |
| A2 | 512-token fixed windows нь A1-ийн character size-ээс бодит token distribution-ийг илүү тогтмол болгох эсэх. | `fixed-token-v1`, target 512, overlap 0 | Ижил frozen blocks/input order, `ordered-blocks-v1`, tokenizer `cl100k_base` 0.12.0, heading/page fences, offline execution | Embedding API cost = 0; chunk count/token estimate нь ирээдүйн index/cost input. Recursive ба fixed chunking CPU complexity тус тус хэмжигдсэн. |
| A3 | Paragraph/newline/space separator packing нь fixed windows-оос boundary heuristics-ийг багасгах эсэх; chunking time/fragmentation tradeoff-ийг хамт хэмжинэ. | `recursive-token-v1`, target 512, overlap 0 | Ижил frozen blocks/input order, `ordered-blocks-v1`, tokenizer `cl100k_base` 0.12.0, heading/page fences, offline execution | Embedding API cost = 0; chunk count/token estimate нь ирээдүйн index/cost input. Recursive ба fixed chunking CPU complexity тус тус хэмжигдсэн. |

## Offline results

| Strategy | Баримт | Chunks | Token median/P95/max | Coverage median | Token inflation median | Duplicates / separator-only | Clause fragments heuristic | Table-cut heuristic | Chunk latency P50/P95 ms | CPU P50/P95 ms | Python allocation P95 bytes | Shared worker RSS max bytes |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A1 | 151 | 2244 | 1311.0 / 1461 / 1739 | 1.0 | 1.000165480721496 | 1 / 0 | 453 / 7808 | 994 | 0.913 / 3.681 | 0.000 / 6.250 | 266136 | 155242496 |
| A2 | 151 | 4946 | 512.0 / 512 / 512 | 1.0 | 1.0 | 1 / 0 | 1158 / 7808 | 2308 | 30.117 / 150.051 | 28.125 / 146.875 | 4896867 | 155242496 |
| A3 | 151 | 6116 | 438.0 / 508 / 512 | 1.0 | 1.0 | 73 / 15 | 213 / 7808 | 2138 | 35.788 / 145.182 | 34.375 / 140.625 | 1325358 | 155242496 |

## A0 comparison (different overlap only)

A0 run `1dc80917-a602-4185-9c3a-f16cae209b21` ба A1 ижил dataset/parser/current character splitter ашигласан; гол өөрчлөлт overlap 250 → 0. Chunk 2468 → 2244; estimated embedding tokens 2,667,761 → 2,340,519; A0 overlap ratio median 0.127, A1 0.000.
A0-ийн end-to-end P50/P95 latency нь guard/tokenization/diagnostics агуулдаг; A1 latency нь зөвхөн chunk-generation тул шууд latency comparison хийхгүй.

## Structural signals ба failure examples

Chunk таслалт, clause boundary болон sentence punctuation тоо нь parser block дээрх heuristic; correctness гэж тайлбарлахгүй. Эх текст тайланд ороогүй.

- Structural flag мөрийн тоо: 390; JSON дотор doc ID + strategy + count байна.
- A2-ийн хамгийн их chunk token: 512; A3: 512. Target 512.
- Table-cut heuristic нь flattened table blocks-ийн chunk boundary давсан эсэх; cell/header/merged-cell correctness хэмжихгүй.
- Process RSS нь A1/A2/A3-г хамтад нь ажиллуулсан нэг fresh worker-ийн before/after RSS sample-ийн max; per-strategy high-water mark гэж тайлбарлахгүй. Python allocation нь strategy тус бүрийн tracemalloc peak.
- Complete-clause preservation, sentence accuracy, source-span accuracy, Recall@k, MRR, nDCG: N/A — human verification хийгдээгүй.
- Inference: recursive strategy-ийн punctuation/clause heuristics A2-оос бага гарсан ч table cuts болон chunk count хэвээр өндөр, chunking latency илүү. Энэ нь зөвхөн next-step hypothesis-г дэмжих heuristic signal; strategy quality winner биш.

## Per-experiment дүгнэлт ба хязгаарлалт

- **A1 (1800 char / 0 overlap):** A0-оос chunk болон token estimate цөөрсөн; source-span overlap 0. Coverage 100% median, source non-whitespace loss 0. A0 дээр overlap-оор давтагдах context алга болсон байж болох тул retrieval evidence recall-г annotation-гүйгээр дүгнэхгүй.
- **A2 (512 token / 0 overlap):** token distribution target орчимд тогтвортой; fixed boundaries дээр clause/sentence heuristics олон. Mongolian UTF-8 byte boundary нь token window-оос ялгаатай; output token max-ийг тайлангаас шалгана. Алгоритмын reference нь benchmark-only.
- **A3 (recursive separator / 512 / 0):** A2-оос жижиг/олон chunk, punctuation/clause fragmentation heuristic бага байж болно; paragraph/space packing нь more expensive token counting ба empty separator chunks үүсгэж болох эрсдэлтэй. Semantic clause understanding биш.

## Retrieval gate ба reproduction

Retrieval benchmark readiness: **NOT READY**. Эхлээд 80 draft candidate-аас evidence болон query-г эх документтэй харьцуулж хүн батална. Үүний дараа ижил embedding model/config, retrieval algorithm, top-k, evaluation queries-ээр isolated index хийхийн өмнө input-token ашиглалт/cost тооцно. Одоо paid embedding run хийгдээгүй.

Командууд:

```powershell
.\.venv\Scripts\flask.exe --app app benchmark-split prepare-annotations --documents 20 --questions-per-document 4
.\.venv\Scripts\flask.exe --app app benchmark-split run-a1-a3
```

A0-г өөрчлөлгүй baseline болгон `split-a0-1dc80917` JSON/CSV тайланд хадгалсан. A1 нь production splitter-ийг өөрчлөлгүй 1,800-char/0-overlap тохиргоотой дуудсан; A2/A3 нь benchmark-only. Public JSON/CSV/Markdown-д confidential document text, нэр, heading ороогүй. Private evidence package зөвхөн `instance/` дотор.
