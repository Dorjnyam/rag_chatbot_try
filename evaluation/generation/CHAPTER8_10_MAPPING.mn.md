# Phase 6 — Chapter 8/10 Supporting Research Mapping

Энэ баримт Chapter 9 traceability-ийг орлохгүй. Chapter 8-ийн context-тай шууд холбоотой 8.2, 8.3, 8.11-ийг шалгаж, бусад advanced strategy-г зөвхөн хэмжигдэхүйц шаардлага гарвал дахин авч үзнэ. Chapter 10-ийг generation quality/performance evaluation-д хэрэглэв.

## Chapter 8 — Context боловсруулах ба retrieval extension

| Хэсэг | Судалгааны санаа | Одоогийн код/нотолгоо | Төлөв |
|---|---|---|---|
| **8.2 Context refinement** | Dedup/overlap нь source text, clause boundary, negation/qualifier-ийг хадгалсан үед л зөвшөөрнө | `rag._context_excerpt` ижил document/version, source span ба exact text overlap шаардана; source reconstruction test | **IMPLEMENTED_VERIFIED** — exact overlap хүрээнд. Semantic/abstractive compression байхгүй. |
| **8.3 Parent–Child context** | Parent fetch нь canonical hierarchy, versioned IDs, validated joins шаарддаг | Parser-ийн nested hierarchy, merged cells, parent relation бүх corpus дээр баталгаатай биш; production retrieval data model өөрчлөгдөөгүй | **BLOCKED_BY_PARSER_LIMITATIONS / DEFERRED** — hierarchy зохиохгүй. |
| **8.11 Lost-in-the-middle/order** | Context байрлалын нөлөө hypothesis; ranked order өөрчлөлтийг ижил evidence-тэй paired comparison-оор шалгах | `build_context` retrieval/fusion ranked order-ийг хадгална; offline generation mock дэс дарааллын чанарыг хэмжээгүй | **BASELINE_PRESERVED / DEFERRED** — human-reviewed order comparison байхгүй. |
| **8.1, 8.4–8.10, 8.12** | Reranker, multi-stage/router, HyDE, Self-RAG/CRAG, confidence threshold, RAPTOR, GraphRAG, ablation | Зарим retrieval capability-ийн код Phase 5-д байгаа ч Phase 6 answer quality дээр шалгагдаагүй | **DEFERRED** — доорх trigger хүснэгтээр эргэн үнэлнэ. |

### Deferred strategy-г эргэн авч үзэх нөхцөл

| Strategy | Хэмжсэн алдаа шаардлагатай | Эхлэхэд шаардагдах өгөгдөл/metadata | Cost, risk | Хэрэгжүүлэх trigger |
|---|---|---|---|---|
| Extractive/semantic compression, coverage selection (8.2) | Token budget-д оруулах боломжгүй relevant evidence эсвэл дутуу context-оос answer omission гарсан нь human review-ээр батлагдах | Gold evidence units, source spans, negation/exception/table boundary labels | Compression нь qualifier тасалж болно; semantic compression бол provider/token cost нэмнэ | Энгийн бүтэн chunk сонголт дахин давтагдсан missing-evidence алдааны шалтгаан болбол; эхлээд extractive хувилбар |
| Parent–Child (8.3) | Нэг chunk-д багтаагүй ойролцоох заалт шаардлагатай алдаа хэд хэдэн verified case-д давтагдах | Parser-ээр баталгаатай parent/child structure, document/version-safe join, stale-version test | Хадгалалт, cross-store lifecycle болон version update төвөгтэй | Load parser hierarchy-г source-тай харьцуулж баталсны дараа |
| Context reordering (8.11) | Ижил retrieved evidence өөр байрлалд ороход хариу/citation өөрчлөгдөх paired human result | Ижил query/evidence, анхдагч ranked order болон control order, answer rubric | Generation sampling ба prompt өөрчлөлт дүгнэлтийг төөрөгдүүлж болно | Human-reviewed paired set дээр байрлалын нөлөө давтагдвал; baseline эрэмбийг control болгоно |
| Reranking/multistage (8.1, 8.4) | Relevant chunk candidate pool-д байгаа ч rank-аас шалтгаалан context-д орохгүй байгааг qrels харуулах | Human relevance qrels, тогтмол candidate pool/top-k | Нэмэлт compute/latency; relevance quality баталгаагүй | Retrieval recall хангалттай боловч context inclusion/rank алдаа гол failure болоход |
| Query router (8.5) | Query class бүрд hybrid baseline давтагдсан, ялгаатай алдаатай байх | Query type/answerability labels болон intent-held-out split | Misroute бол retrieval-ийг дордуулна; routing policy нэмнэ | Routing-specific human error олдож, paired comparison benefit харуулахад |
| HyDE (8.6) | Paraphrase/terminology хэлбэрт retrieval miss давтагдах; гипотетик текст баримт болж харагдах эрсдэлийг хянах | Language/type-stratified qrels, generated-query provenance | Нэмэлт model call, token cost; зохиомол текстийг evidence гэж андуурах эрсдэл | Query representation gap нь retrieval-ийн хэмжсэн үндсэн bottleneck болоход |
| Self-RAG/CRAG (8.7) | Output gate-ийн дараа ч unsupported answer эсвэл буруу abstention human review-д үлдэх | Human-adjudicated claims, answerability болон false-accept/false-refuse labels | Judge/loop latency ба cost; автомат judge алдаатай байж болно | Deterministic gate-ийн дараах чанарын алдаа тодорхой хэмжээгээр давтагдаж, judge-ийг human set дээр баталсны дараа |
| Confidence threshold (8.8) | Хариулах эсэх шийдвэрийн calibration алдаатай байх | Representativeness-тэй answerability/error set, calibration/test тусгаарлалт | Threshold drift/false abstention | Calibration set ба held-out review үр дүн авах хүртэл threshold зохиохгүй |
| RAPTOR/GraphRAG (8.9–8.10) | Олон заалт/олон баримтын synthesis эсвэл баталгаажсан relation query retrieval miss-ийн тогтвортой ангилал болох | Versioned summary lineage эсвэл human-verified graph edges/entities | Index/build, update, explainability, storage-ийн өндөр complexity | Сэдэв/relationship дагуух алдаа qrels-аар хэмжигдэж, metadata эх сурвалжаар баталгаажсаны дараа |
| Ablation/statistical comparison (8.12; Chapter 10.10) | Одоогийн системд солих тодорхой component/hypothesis байх | Хангалттай independent reviewed questions; language/type group, same corpus/context/model | N=2 үед interval/significance утгагүй; хэт олон metric false-positive өсгөнө | Human set өргөжиж, primary endpoint урьдчилан тогтоосны дараа paired comparison |

## Chapter 10 — Evaluation methodology

| Хэсэг | Хэмжих ойлголт | Одоогийн нотолгоо | Status |
|---|---|---|---|
| **10.6 Answer quality** | Correctness, faithfulness, citation support, critical error тус тусдаа | 2 retrieval evidence question; generation output-д хүний label алга | Quality metrics **N/A**; mock response-оос accuracy тооцоогүй. |
| **10.7 Latency** | Stage timing, P50/P95/P99; failure ба success тусдаа | Local context/budget/serialization/mock validation 3 final runs, each 90 timed samples | Offline plumbing only; provider latency, TTFT, concurrency **N/A**. |
| **10.8 Tokens/cost** | Provider usage, cached/reasoning token; estimates-аас ялгах | `tiktoken` serialized request estimate; nested provider usage accumulator tests | Actual usage/cost **N/A**, API calls 0. |
| **10.9 Annotation** | Answerability, representative query set, audit trail, reviewer | `gt-v1`: 2 independent question, 6 correlated variation; 16 draft | Generation review 0; IAA/adjudication **N/A**. |
| **10.10 Paired/ablation** | Ижил cases, independent sampling unit, effect size, interval | Generation strategy comparison хийгдээгүй | Model/strategy superiority claim **хийхгүй**; N=2 хангалтгүй. |
| **10.11 Scalability** | Concurrency/throughput | Нэг process-ийн serial offline test | Production scale **untested**. |

Chapter 8/10-ын жишээ тоо, санал болгосон архитектур болон бусад corpus-ийн benchmark-ийг project measurement болгон тайлагнаагүй.
