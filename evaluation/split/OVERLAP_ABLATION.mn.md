# Phase 2 — Token overlap ablation ба semantic feasibility

## Хураангуй

Frozen real corpus-ийн 151 Split input дээр fixed-token болон recursive-token стратегийн 512-token target, 51/77/128-token overlap хувилбаруудыг offline хэмжив. Zero-overlap A2/A3 үр дүнг өмнөх ижил snapshot run-оос reuse хийсэн; тэдгээрийг дахин ажиллуулаагүй. Coverage median бүх зургаан overlap хувилбарт 1.0, алдагдсан non-whitespace character 0.

**Шийдвэр:** retrieval quality эсвэл human accuracy-гаар стратеги эрэмбэлэх боломжгүй. Annotation 80 асуулттай боловч 0 нь human-verified. Overlap нэмэхэд chunk/token cost болон table-cut heuristic өссөн. Recursive 51 нь zero-overlap A3-аас clause-fragment heuristic-ийг 213-аас 197 болгосон ч sentence/table boundary дохио сайжраагүй. Retrieval benchmarking одоогоор **NOT READY**.

## Experimental design

- **Chapter 4 холбоос:** overlap ablation нь §4.8; semantic feasibility нь §4.5, sentence segmentation component нь §4.4. Chapter-ийн жишээ нь hypothesis/algorithm framing болохоос бидний measured result биш.
- **Hypothesis:** 512-token overlap нь evidence boundary тасралтыг багасгаж болох боловч duplicated context/token overhead нэмнэ; recursive boundary packing fixed хувилбараас бүтэц эвдэх дохиог бага байлгах боломжтой.
- **Baseline:** A2 fixed-token 512/0, A3 recursive-token 512/0. Run `ecf27e9c-7558-41b5-ae09-c145937dfa7c`; ижил `ordered-blocks-v1` frozen snapshot.
- **Experimental variables:** algorithm (fixed/recursive), overlap 51/77/128 (10/15/25%); target 512.
- **Controlled:** snapshot `real-corpus-15b24382-v1`, dataset SHA-256 `15b24382aeba13e943382370f5a4bfb4ede50b4033805006bad692d23bf3d56a`, frozen manifest input order, `cl100k_base`/tiktoken 0.12.0, heading/page grouping fences, 151 parsed input. 3 Load failures unchanged.
- **Run:** `95a117e7-e306-43f1-bc34-94d0c9ee217b`; 151 documents, 750.524 seconds, 0 API calls. Fresh worker per document; one warm-up and five timed iterations. Python allocation peak is per-worker tracemalloc P95; process RSS per config unavailable in this run and omitted.
- **A0–A3 preservation check:** production `chunking.py` өөрчлөгдөөгүй. A2/A3 no-overlap algorithm output болон deterministic chunk IDs `DOC0001` дээр frozen baseline report-ийн hashes-тай match хийв.
- **Costs:** estimated embedding input tokens are prospective tokenizer counts, not API billing or actual index size. PostgreSQL/ChromaDB/retrieval not invoked.
- **Annotation:** 80 candidate questions × 3 draft variants; human-verified references 0.

## Measured comparison

| Strategy | Overlap | Chunks | Token median/P95/max | Token inflation median | Estimated embedding tokens | Source-span overlap chars / token estimate | Clause fragmentation heuristic / 7,808 | Sentence-boundary violations heuristic | Table cuts heuristic | Chunk latency P50/P95 ms | Python allocation P95 bytes |
|---|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A2 fixed baseline | 0 | 4,946 | 512 / 512 / 512 | 1.000 | 2,340,036 | 0 / 0 | 1,158 | 4,226 | 2,308 | 30.117 / 150.051 | 4,896,867 |
| OF-51 fixed | 51 (9.96%) | 5,454 | 512 / 512 / 512 | 1.107 | 2,584,919 | 339,423 / 244,896 | 647 | 4,696 | 2,582 | 66.108 / 277.688 | 5,838,017 |
| OF-77 fixed | 77 (15.04%) | 5,758 | 512 / 512 / 512 | 1.172 | 2,733,320 | 542,761 / 393,297 | 517 | 5,037 | 2,736 | 67.981 / 297.095 | 4,894,236 |
| OF-128 fixed | 128 (25%) | 6,452 | 512 / 512 / 512 | 1.323 | 3,082,875 | 1,024,509 / 742,852 | 341 | 5,700 | 3,089 | 82.776 / 362.306 | 4,892,641 |
| A3 recursive baseline | 0 | 6,116 | 438 / 508 / 512 | 1.000 | 2,340,099 | 0 / 0 | 213 | 2,910 | 2,138 | 35.788 / 145.182 | 1,325,358 |
| OR-51 recursive | 51 (9.96%) | 6,817 | 437 / 508 / 512 | 1.133 | 2,653,923 | 437,433 / 313,900 | 197 | 3,267 | 3,076 | 70.378 / 301.125 | 1,325,390 |
| OR-77 recursive | 77 (15.04%) | 7,246 | 435 / 508 / 512 | 1.216 | 2,846,334 | 702,715 / 506,311 | 205 | 3,473 | 3,298 | 75.845 / 322.175 | 1,325,390 |
| OR-128 recursive | 128 (25%) | 8,315 | 437 / 509 / 512 | 1.419 | 3,317,130 | 1,347,739 / 977,107 | 178 | 4,043 | 3,840 | 91.417 / 409.511 | 1,325,390 |

Token-overlap estimate нь source-span intersection хэсгийг `cl100k_base`-ээр tokenize хийсэн баримжаа; chunk-token sum болон inflation-аас тусдаа хэмжүүр. Token-inflation нь баримт бүрээр тооцсон median. Coverage median 1.0 болон loss 0 нь exact parser-local block offset-ийн coverage болохоос эх DOCX-ийн visual fidelity биш.

## Failure analysis ба хязгаарлалт

- Overlap бүр chunk count, estimated embedding tokens, table-cut heuristic-ийг нэмэгдүүлсэн. Жишээ нь recursive 51 нь A3-аас +701 chunk (+11.5%), estimated tokens +313,824 (+13.4%), table-cut heuristic +938 болсон.
- Clause fragmentation heuristic recursive overlap-ийн гурван хэмжээст 197/205/178 болж A3-ийн 213-аас буурсан. Харин sentence punctuation violation 3,267/3,473/4,043 болж A3-ийн 2,910-оос өссөн; table heuristic ч өссөн. Эдгээр нь regex/punctuation дохио, human-verified accuracy биш.
- Fixed overlap-д clause heuristic 1,158-аас 341 хүртэл буурсан ч sentence/table heuristics нэмэгдэж, token inflation 1.107–1.323 болсон.
- Partial-table flag нь flattened parser block boundary heuristic; table cell/header/merged-cell correctness хэмжих ground truth байхгүй. Page metadata parser output-д байхгүй тул page/citation accuracy N/A.
- Retrieval Recall@k, MRR, nDCG, evidence-span coverage, latency/context tokens, actual embedding cost/index bytes бүгд N/A; retrieval болон embedding API дуудагдаагүй.

## Semantic chunking feasibility S1/S2

`app/split_semantic.py` нь isolated prototype: Mongolian/English conservative sentence segmentation, adjacent cosine-distance breakpoints (80/90/95 percentile), 512-token maximum, oversized fixed-token fallback, dependency-injected provider болон content SHA/model/config keyed local vector cache. `DeterministicMockEmbeddings` unit test-д ашиглагдсан бөгөөд чанарын embedding биш.

Бодит S1 inference **blocked**: local model folder дахь Qwen3-Reranker нь CrossEncoder reranker, embedding model биш; local embedding model/cache байхгүй. Paid API хэрэглээгүй, cost/call/token бодит хэмжилт 0/N/A. S2 мөн **blocked**: verified structural boundary reference алга; function ямар ч boundary ирэхгүй бол exception өгч S1 рүү fallback хийхгүй. Дэлгэрэнгүй [semantic readiness](SEMANTIC_READINESS.mn.md).

## Дараагийн туршилтын дараалал

1. Reviewer эх document-той 80 draft question/evidence/answer-ийг тулгаж засах; verified metadata-г л бөглөх. [Review package readiness](ANNOTATION_REVIEW_READINESS.mn.md).
2. Human-verified evidence-ийн дараа ижил embedding model/retrieval/top-k/context budget-аар A0–A3 болон overlap-ын зөвхөн төлөөлөх 0/51/77 хувилбаруудын retrieval evaluation хийх. 128 overlap-ийг өндөр cost, өссөн table-cut heuristic-ийн улмаас эхний retrieval grid-д оруулахгүй.
3. S1 semantic corpus run-оос өмнө local embedding model баталгаажуулах эсвэл API model, token/call estimate, cost/rate-limit-ийг тооцож хэрэглэгчийн зөвшөөрөл авах. S2-г verified structure annotation үүссэний дараа л турших.

## Reproduction

```powershell
flask benchmark-split overlap-ablation --snapshot real-corpus-15b24382-v1
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider --basetemp=instance\split-evaluation\test-tmp tests/test_split_benchmark.py tests/test_load_split_interface.py -q
```

Overlap run-ийн anonymous JSON/CSV artifacts: `split-overlap-95a117e7.json` болон тухайн run-ийн CSV/report. Эдгээр public artifact-д confidential source text, document name/path ороогүй.
