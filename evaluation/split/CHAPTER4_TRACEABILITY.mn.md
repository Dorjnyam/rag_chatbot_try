# Chapter 4 — Split судалгаа ба хэрэгжилтийн traceability

## Хамрах хүрээ ба нотолгоо

Теорийн эх сурвалж: `Advanced_RAG_Chapter4_IEEE.docx`, Sections 4.1–4.10. Судалгааны жишиг тоо, санал болгосон архитектурыг энэ тайлангийн туршилтын хэмжилт гэж тооцоогүй. Кодын аудитын гол эх сурвалж нь `app/services/chunking.py`, `app/services/openai_provider.py`, `app/services/ingestion.py`, `app/evaluation.py`, `tests/test_core.py`, `tests/test_load_split_interface.py` болон Phase 1 тайлан.

Төлөвийн утга: **Implemented and verified**, **Implemented but untested**, **Partially implemented**, **Missing**, **Blocked by parser limitations**, **Deferred**.

| Судалгаа | Одоогийн код ба тестийн нотолгоо | Төлөв | Энд шалгаж болох зүйл | Split/Retrieval/citation-д үзүүлэх нөлөө |
|---|---|---|---|---|
| **4.1 Chunking-ийн шаардлага** | `chunking.chunk()` текстийг chunk болгож, `assign_ids()`-ээр config/ordinal дээр суурилсан deterministic ID өгнө. `tests/test_core.py::test_chunk_coverage_offsets_and_determinism` хамрах хүрээ, offset, ID-г шалгадаг. | Implemented and verified | Source interval coverage, chunk distribution, runtime, memory. | Retrieval чанар батлагдаагүй; chunk ID өөрчлөгдвөл downstream index дахин үүснэ. |
| **4.2 Fixed-size** | `chunking.py` Unicode character windows хэрэглэдэг; `CHUNK_SIZE=1800`, overlap 250 нь character units. Одоогийн тест deterministic character windows баталдаг. | Character fixed-window: Implemented and verified; token fixed-window: Missing | A0 хэмжилт хийв. Token хэмжээг энэ A0-той шууд адилтгахгүй. | Token target байхгүй үед хэлний/Unicode-гийн ялгаатай текстүүдийн бодит chunk size хэлбэлзэнэ. |
| **4.3 Recursive Character Splitting** | Одоогийн `chunk()` separator шатлалаар semantic boundary сонгодоггүй. `OpenAIProvider.fit_chunks()` доторх 7,900-token хамгаалалт хэт урт chunk-ийг character midpoint-аар хуваадаг; энэ нь recursive separator chunker биш. | Missing | Ижил source fence ба tokenizer дээр 512 fixed vs recursive; дараа нь size grid. | Том clause/paragraph тасрах эрсдэлийг зөвхөн boundary reference-тай хэмжинэ. |
| **4.4 Sentence-based** | SBD болон sentence packing хэрэгжээгүй; dedicated fixture/test алга. | Missing | Монгол дугаарласан clause, товчлол, initials, decimal/date, урт өгүүлбэрийн тусдаа fixture шаардлагатай. | Sentence boundary эвдрэл нь evidence chunk дотор тасрахад нөлөөлж болно; retrieval GT одоогоор алга. |
| **4.5 Semantic** | Embedding distance эсвэл semantic breakpoint splitter алга. | Deferred | Зөвхөн baseline-аас үлдсэн topic-boundary алдааг human annotation-аар тогтоосны дараа. | Нэмэлт embedding cost; одоогоор хэмжилт/зардлын үндэслэлгүй. |
| **4.6 Structure-aware / Hierarchical** | Байгаа `heading` string болон `page` талбарын өөрчлөлт chunk group/fence үүсгэдэг. DOCX parser heading hierarchy, automatic list number/level болон page provenance хадгалахгүйг `tests/test_load_split_interface.py` нотолдог. Table нь flat text; merged cell холбоо алдагддаг. | Одоогийн string/page grouping: Implemented and verified; hierarchy/cell structure: Blocked by parser limitations | Одоо байгаагаар paragraph/table kind, heading string, page fence-ийн feasibility л хэмжинэ. | Бодит heading tree, nested list, merged-cell контекст тасарвал structure-aware splitter сэргээж чадахгүй; source citation anchor хязгаарлагдана. |
| **4.7 Parent–Child retrieval** | Parent store, child-to-parent mapping, parent context expansion байхгүй. Ingestion хадгалдаг chunk ID, metadata-г; retrieval нь persisted chunks-д ажилладаг. | Missing; Deferred | Энэ baseline-д туршихгүй. Parser-ийн бодит hierarchy байхгүй тул зөвхөн size-based pseudo-parent болох бөгөөд Retrieval/context assembly өөрчлөлт шаардана. | Retrieval, context budget, citation/version authorization-д шууд нөлөөлнө. |
| **4.8 Overlap** | Character overlap `start=end-overlap` гэж хэрэгжсэн; `test_core.py` шалгадаг. Token overlap ablation байхгүй. | Character overlap: Implemented and verified; token ablation: Missing | A0 redundancy болон token inflation-ийг хэмжинэ; 400-token ablation тусдаа planned. | Overlap context тасралтыг багасгаж болох ч давхардсан embedding/storage/context cost нэмэгдэнэ. |
| **4.9 Chunk size optimization** | Runtime config character units; token target grid болон retrieval accuracy optimization байхгүй. | Missing | 256/512/1024 token, overlap 0 controlled grid. Human reference байхгүй үед зөвхөн structural/cost metrics. | Evidence Recall/MRR/nDCG хэмжих хүртэл “оновчтой” size гэж дүгнэх боломжгүй. |
| **4.10 Complexity/performance** | Load үеийн parse performance Phase 1-д бий. Split үеийн тусгаарласан хэмжүүр өмнө байгаагүй. `chunk()` chunk бүрт block span-уудыг давтан шүүж, `partial_table` тооцоололд nested membership шалгалттай. | Audit instrumentation: энэ run-д хэмжсэн; scaling complexity: partially characterized | Per-document runtime, tokenization, guard, Python allocation peak, throughput болон synthetic block-count scaling. | Өндөр block count үед Split latency нэмэгдэж болох ч эхлээд measured trend хэрэгтэй. |

## Split интерфейс ба мэдээлэл хадгалалт

`ingestion.process()` нь `artifact["blocks"]`-ийг `chunk()` руу дамжуулж, дараа нь provider guard-аар оруулаад document/version/language/config_hash-ийг metadata-д нэмдэг. Chunker нь block start/end, heading string, group-local `char_start/end`, page start/end (байвал), extraction method, partial-table flag болон source region JSON хадгалдаг. Source original span, heading hierarchy, clause ID, row/cell/merged-cell graph хадгалахгүй. Тиймээс source region-ээс бусад offset нь parser-ийн normalized block text доторх local character offset болохоос original DOCX/PDF byte/page offset биш.

**Чухал interface хязгаар:** extraction-ийн эх block lineage одоо metadata-аар block дугаар болон span-оор хэсэгчлэн сэргээгдэнэ, гэхдээ normalization болон chunk group separator-уудын эх сурвалжийг тусад нь тэмдэглэдэггүй. Table row/cell болон автомат list marker parser дээр алдагдсан. Эдгээр нь одоогийн citation accuracy-г хэмжихэд хориг болдог ч Split baseline structural audit хийхэд саад болохгүй. `document_id`, `version_id`, `language`, `config_hash` production path дээр нэмэгддэг; benchmark дээр үйлдвэрлэлийн ID-г зохиохгүй.

## Phase 1 evidence

154 бодит DOC/DOCX input, 151 Load success, гурван `empty_document` failure. Dataset SHA-256 нь `15b24382aeba13e943382370f5a4bfb4ede50b4033805006bad692d23bf3d56a`. 151 амжилттай баримтад parser revision таарсан raw cache олдсон; 154 бүгдийн source corpus hash Phase 1 manifest-тай тулсан. Parser output character/block count өмнөх Load report-той match болсон. Load report нь CER, heading/clause/table correctness-ийг хэмжээгүй. Dataset query files draft тул retrieval accuracy-г энэ offline run-д N/A гэж үзнэ.

## Initial baseline method

A0 нь production `chunk()`-ийг өөрчлөлтгүй, `characters-v1` 1800 character / 250 character overlap-аар хэрэглэнэ. Raw болон `OpenAIProvider.fit_chunks()` guard-ын дараах fitted output тусдаа хэш/тоо авна. Tokenizer нь `text-embedding-3-small`-д зориулсан `cl100k_base` (`tiktoken 0.12.0`). Guard max 7,900 tokens; API/provider constructor дуудлагагүй. Нэг шинэ Python worker/document, нэг warm-up, таван timed iteration.

Coverage нь block window interval union-оор хэмжигдэнэ. Chunker-ийн `\n\n` separator нэмэлт тэмдэгтээр тусдаа тоологдоно. Repeated content-ийг substring хайж coverage тооцохгүй. Source span overlap character redundancy ба BPE token inflation тусдаа metric. Regex clause count зөвхөн heuristic. Бүрэн clause preservation, sentence accuracy, citation span accuracy болон Recall@k/MRR/nDCG нь human reference/query annotation байхгүй тул N/A. API usage, production DB connection, Chroma call энэ туршилтад 0; vector index size бодитоор үүсгээгүй тул N/A.

## Хязгаарлалт ба эрсдэл

- Peak process RSS-ийг Windows process-ийн богино хэмжилтийн snapshot-аар барина; OS-ийн high-water mark гэж тайлбарлахгүй. `tracemalloc` нь зөвхөн Python allocation бөгөөд native memory-г бүрэн төлөөлөхгүй.
- Fixed-window chunker-ийн complexity-г зөвхөн A0 workload болон бага хэмжээний synthetic scaling check-ээр дүгнэнэ; нарийн том corpus extrapolation хийхгүй.
- Load parser-ийн normalization/source mapping-ийг өөрчлөөгүй. Нэг chunk доторх local offsets нь original-source span verified гэсэн үг биш.
- `fit_chunks` 7,900-token guard нь embedding model API-ийн бодит usage/cost биш; token estimate л гаргана.

## Эцсийн шийдвэр

**READY WITH DOCUMENTED LIMITATIONS** — A0 offline Split behavior, coverage, size, redundancy, runtime, token estimate, memory instrumentation болон reproducibility-г benchmark хийхэд бэлэн. Retrieval/chunk-size чанарын шийдвэрт READY гэж үзэхгүй: human-verified source evidence болон approved query split дутуу; parser heading/list/table provenance хязгаарлагдмал.

Дараагийн хамгийн үнэ цэнтэй алхам: selected representative documents дээр original-source location-той human-verified clause/evidence references бөглөөд, дараа нь ижил tokenizer/fences дээр fixed-size vs recursive 512-token, overlap 0 туршилт хийх. Энэ нь Chapter 4.3 recursive boundary-ийн үнэ цэнийг structure-aware/Semantic/Parent–Child complexity-оос өмнө хамгийн бага зардлаар шалгана.
