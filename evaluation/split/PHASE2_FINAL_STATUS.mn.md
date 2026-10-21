# Phase 2 — Split final status

**Статус: OFFLINE EVALUATION COMPLETE — RETRIEVAL OPTIMIZATION DEFERRED**

Энэ нь offline Split хэмжилтийн milestone хаагдсан гэсэн утгатай. Split fully optimized гэсэн дүгнэлт биш. Human-verified dataset одоогоор 2 асуулттай тул чанк стратегийн retrieval superiority тогтоох өгөгдөл хүрэлцэхгүй.

## Хамрах хүрээ ба reproducibility

- Frozen input: 154 source файл, 151 parser-success баримт, 3 өмнөх Load failure unchanged.
- Parser: `ordered-blocks-v1`; corpus SHA-256: `15b24382aeba13e943382370f5a4bfb4ede50b4033805006bad692d23bf3d56a`.
- `tiktoken 0.12.0`; offline chunking baseline-д `cl100k_base`.
- Benchmark run болон raw private extraction cache хадгалагдсан. API / PostgreSQL / ChromaDB хэрэглээгүй.
- Private ground-truth export `gt-v1`: 2 human-confirmed асуулт, 6 query variation. Энэ нь corpus-level retrieval benchmark биш.

## Бодит үр дүн

| Хувилбар | Тохиргоо | Чанк | Estimated embedding tokens | Чанарын баталгаа |
|---|---|---:|---:|---|
| A0 | Production character splitter, 1,800 char / 250 overlap | 2,468 | 2,667,761 | Retrieval accuracy N/A |
| A1 | Одоогийн splitter, 1,800 char / 0 overlap | 2,244 | 2,340,519 | Retrieval accuracy N/A |
| A2 | Fixed token, 512 / 0 | 4,946 | 2,340,036 | Retrieval accuracy N/A |
| A3 | Recursive separator/token budget, 512 / 0 | 6,116 | 2,340,099 | Retrieval accuracy N/A |

A1–A3 ижил frozen parser output дээр хэмжигдсэн. Coverage median 1.0, non-whitespace source loss 0 байсан ч эдгээр нь normalized parser text доторх interval metrics; original DOC/DOCX visual fidelity биш. A3-ийн clause fragmentation heuristic A2-оос бага (213/7,808 ба 1,158/7,808), харин chunk тоо их. Sentence punctuation болон table-cut flags нь heuristic тул correctness биш. Run-ийн latency, memory, бусад structural metrics: [A1–A3 тайлан](split-a1-a3-ecf27e9c.mn.md).

### Overlap ablation

512-token target, 51/77/128 overlap-аар 151 баримтад offline хэмжсэн; API call 0, total run 750.524 секунд.

| Стратеги | Overlap | Чанк | Estimated embedding tokens | Token inflation median | Clause heuristic | Table-cut heuristic |
|---|---:|---:|---:|---:|---:|---:|
| Fixed | 0 | 4,946 | 2,340,036 | 1.000 | 1,158 | 2,308 |
| Fixed | 51 | 5,454 | 2,584,919 | 1.107 | 647 | 2,582 |
| Fixed | 77 | 5,758 | 2,733,320 | 1.172 | 517 | 2,736 |
| Fixed | 128 | 6,452 | 3,082,875 | 1.323 | 341 | 3,089 |
| Recursive | 0 | 6,116 | 2,340,099 | 1.000 | 213 | 2,138 |
| Recursive | 51 | 6,817 | 2,653,923 | 1.133 | 197 | 3,076 |
| Recursive | 77 | 7,246 | 2,846,334 | 1.216 | 205 | 3,298 |
| Recursive | 128 | 8,315 | 3,317,130 | 1.419 | 178 | 4,043 |

Overlap нь estimated input-token зардал болон table-cut heuristic-ийг өсгөсөн; зарим clause heuristic буурсан ч retrieval evidence Recall хэмжигдээгүй. Иймд overlap хувилбарын winner байхгүй. Detail: [overlap ablation](OVERLAP_ABLATION.mn.md).

## Semantic Chunking ба parser-ийн хязгаарлалт

`app/split_semantic.py` нь dependency-injected S1 primitive, segmentation/breakpoint, mock cache test-тэй. Mock нь semantic embedding чанар шалгахгүй; real corpus inference хийгдээгүй. S2 structure-constrained хувилбарт verified boundary reference дутуу.

`ordered-blocks-v1` нь paragraph/table төрлийг хэсэгчлэн өгдөг ч page provenance, heading hierarchy, automatic list levels, clause identity, table cell/merged-cell graph алдагддаг. Source span/citation anchor бүрэн баталгаатай биш. Ийм мэдээллийг Split-д зохиож нэмэхгүй.

## Ground-truth байхгүй тул N/A хэмжүүрүүд

Complete-clause accuracy, sentence-boundary accuracy, heading recall, table extraction accuracy, citation/source-span accuracy, retrieval Evidence Recall@k, MRR, nDCG, context-token quality нь corpus-scale хэмжээнд **N/A**. 2 асуултын `gt-v1` нь smoke-test-д хүрэлцэх ч стратеги сонгоход representative биш. Хоёр баримтын smoke үр дүнг quality metric гэж тайлбарлахгүй.

## Provisional chunker ба шийдвэр

Дараагийн Embed smoke болон одоогийн production-д **A0 — 1,800 character, 250-character overlap**-ийг хэвээр хэрэглэнэ. Шалтгаан нь production implementation тогтвортой, integration нь одоо байгаа ingestion-тэй баталгаатай; энэ тохиргоо retrieval дээр бусдаас илүү гэдэг нь хэмжигдээгүй. Production splitter өөрчлөгдөхгүй.

Үлдсэн Split судалгааны асуулт: verified queries дээр 512 fixed/recursive болон overlap нь evidence recall-д ямар нөлөөтэй; clause/table boundary эвдрэл retrieval-ийг хэр өөрчлөх; semantic boundary-н зардал бодит ашиг авчрах эсэх. Үүнийг Phase 3 Embed-ийн минимал plumbing smoke-ийн дараа нэмэлт human evidence бүрдсэн хэмжээнд тусад нь шийднэ.

## Reproduction

```powershell
.\.venv\Scripts\python.exe -m flask --app app benchmark-split run --snapshot real-corpus-15b24382-v1
.\.venv\Scripts\python.exe -m flask --app app benchmark-split run-a1-a3 --snapshot real-corpus-15b24382-v1
.\.venv\Scripts\python.exe -m flask --app app benchmark-split overlap-ablation --snapshot real-corpus-15b24382-v1
```

Ижил run-уудыг дахин ажиллуулах нь их хэмжээний CPU ажлыг давтана. Өмнөх run artifacts-ийг эхлээд үзэхэд хангалттай; Phase 2-г дахин benchmark хийх шаардлагагүй.
