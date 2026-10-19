Энэ баримт нь одоогийн Flask хэрэгжилтийн зан төлөвийг тайлбарлана. Mermaid зураг нийт 15 бөгөөд зураг бүрийн дараа холбогдох эх кодын entry point-ийг нэрлэв.

## 1. Архитектур ба индексийн амьдралын мөчлөг

```mermaid
flowchart LR
  Web[Flask routes] --> RAG[services/rag.py]
  RAG --> Ret[services/retrieval.py]
  Ret --> Chroma[(Chroma dense vectors)]
  Ret --> BM25[services/sparse.py]
  BM25 --> FS[(version JSON artifacts)]
  Ingest[services/ingestion.py] --> Chroma
  Ingest --> BM25
  Ingest --> PG[(PostgreSQL state and manifest)]
  Ret --> Gemini[services/gemini.py]
  Gemini --> RAG
  RAG --> Logs[(query logs and citations)]
```
Factory нь `app/__init__.py:create_app`; route нь `app/routes/chat.py:message`; гол оркестрац нь `app/services/rag.py:ask`.

```mermaid
flowchart TD
  Upload --> Extract[parse_bounded]
  Extract --> Review[approve]
  Review --> Chunks[chunking]
  Chunks --> Embeddings[Gemini embeddings]
  Embeddings --> Stage[Chroma upsert]
  Stage --> DenseCheck[manifest ID verification]
  DenseCheck --> Sparse[build_index]
  Sparse --> SparseCheck[exact ID verification]
  SparseCheck --> Activate[activate version]
  DenseCheck -->|failure| Fail[keep old active]
  SparseCheck -->|failure| Fail
```
`app/services/ingestion.py:process` нь хоёр индекс баталгаажсаны дараа хувилбарыг идэвхжүүлдэг.

Sparse JSON artifact бүр `version_id` болон canonical chunk ID-г агуулна. `app/services/sparse.py:load_index` mtime fingerprint-ээр process cache хийж, `build_index` атомик бичилт хийнэ. Chunk текст PostgreSQL-д давхар хадгалагдахгүй. `backfill-sparse` CLI нь бэлэн Chroma бичлэгээс embedding хийхгүйгээр sparse artifact сэргээнэ.

## 2. Хайлт ба эрэмбэлэлт

```mermaid
flowchart TD
  Q[Original query] --> L[query_candidates]
  L --> V[active_versions filters]
  V --> D[dense_search top 15]
  V --> S[sparse BM25 top 15]
  D --> Fuse[RRF k=60]
  S --> Fuse
  Fuse --> Optional{reranking enabled?}
  Optional -->|yes| CE[local CrossEncoder batch]
  Optional -->|no| TopK[final top-k]
  CE --> TopK
```
Код: `app/services/retrieval.py:retrieve`, `dense_search`; `app/services/sparse.py:search`, `rrf`.

```mermaid
flowchart LR
  Query --> Terms[Unicode NFC tokenization]
  Terms --> BM25Score[IDF + term frequency + length norm]
  Corpus[eligible chunks] --> BM25Score
  BM25Score --> Sort[score descending, ID ascending]
  Sort --> SparseTop[15 results]
```
Sparse tokenizer кирилл болон латин үсгийн цувралыг хадгалж, Unicode-aware токен үүсгэнэ. Баримтын гарчиг, файлын нэр, байгаа section мэдээлэл chunk index-ийн текстэнд орно. Байгууллагын corpus дээр BM25 чанарыг үнэлэх шаардлагатай.

```mermaid
flowchart TD
  DenseRankings --> RankMap["canonical chunk ID to ranks"]
  SparseRankings --> RankMap
  RankMap --> Score["sum 1 / (60 + rank)"]
  Score --> Stable["score descending, ID ascending"]
```
`sparse.rrf` ID-гаар давхардлыг нэгтгэнэ; хоосон branch хүчинтэй. Hybrid-д нэг active version-ийн sparse индекс дутуу эсвэл эвдэрсэн бол dense-only fallback хийнэ, `sparse_degraded` болон хувилбарын ID-г trace-д бүртгэнэ. Энэ нь hybrid acceptance-д амжилт гэж тооцогдохгүй.

```mermaid
flowchart LR
  RRF --> Enabled{RERANKING_ENABLED}
  Enabled -->|false| Keep[RRF order]
  Enabled -->|true| Local[local_files_only model]
  Local --> Batch[predict batches]
  Batch --> Sort[rerank score, stable ID tie]
  Local -->|error| Keep
```
`_reranker()` model-ийг app process дотор нэг удаа cache хийнэ. `requirements-reranker.txt` optional; model weight татахгүй, local model path заана. Ачаалах болон scoring алдаа RRF дарааллыг үлдээнэ. Монгол хэлний benchmark нотолгоо одоогоор байхгүй.

## 3. Context, generation, telemetry

```mermaid
flowchart TD
  Ranked --> Dedup[exact and overlap text dedup]
  Dedup --> Limit[at most six chunks]
  Limit --> Budget[context token budget]
  Budget --> Labels[S1..Sn labels + source location]
  Labels --> Evidence[untrusted evidence payload]
```
`app/services/rag.py:build_context` эх текст, title, version, metadata location, citation label-ийг хадгалж, түүх болон input token budget-ийг хянадаг.

```mermaid
sequenceDiagram
  participant R as rag.ask
  participant P as Gemini provider
  participant M as Model
  R->>P: original question + evidence + Mongolian Cyrillic policy
  P->>M: structured generation
  M-->>P: JSON status, answer, sources
  P-->>R: answer JSON
  R->>R: validate_answer citations and output language
```
Код: `app/services/rag.py:SYSTEM`, `validate_answer`; `app/services/gemini.py:generate`.

```mermaid
flowchart TD
  Raw[model JSON] --> Schema[status / answer / sources]
  Schema --> Cites[inline labels equal source list]
  Cites --> Lang[heuristic Mongolian Cyrillic check]
  Lang -->|pass| Return[return validated answer]
  Lang -->|fail| Repair[one repair generation]
  Repair --> Compare[validate citations, numbers, protected tokens]
  Compare -->|pass| Return
  Compare -->|fail| Abstain[Mongolian fallback]
```
Нийт generation хүсэлт дээд тал нь хоёр: эх хариулт болон нэг citation/JSON retry эсвэл language repair. Засвар эх citation list, хамгаалсан тоо, acronym, URL, citation token-ийг өөрчилбөл татгалзана. Энэ шалгалт нь source faithfulness-ийг бүрэн батлахгүй.

```mermaid
sequenceDiagram
  participant U as User
  participant F as Flask route
  participant A as rag.ask
  participant R as retrieve
  participant G as Gemini
  participant DB as PostgreSQL
  U->>F: question
  F->>A: validated request
  A->>DB: create original-query log
  A->>R: query candidates + filters
  R-->>A: ranked evidence + stage timings
  A->>G: evidence prompt
  G-->>A: structured answer
  A->>DB: answer, citations, trace, timings
  A-->>F: stable JSON response
  F-->>U: Mongolian Cyrillic UI
```
Одоогийн endpoint болон JSON key хадгалагдсан. Retrieval hit-д `distance` nullable; sparse/RRF score, branch ranks болон optional rerank score нэмэгдэнэ. UI null distance дээр алдаа гаргахгүй.

## 4. Хэл илрүүлэлт ба query хувилбар

```mermaid
flowchart TD
  Input[original query] --> NFC[Unicode NFC + whitespace normalization]
  NFC --> Script[Unicode script cues]
  Script --> Lexicon[Mongolian / English lexical cues]
  Lexicon --> Classify[mn_cyrl / mn_latn / en / mixed / unknown]
  Classify --> Confidence[confidence + original retained]
```
`app/services/language.py:detect_language` хэлийг heuristic-ээр ангилдаг. Query language нь document-language filter биш.

```mermaid
flowchart LR
  Latin[Romanized Mongolian] --> WholeWord[whole-word lexicon lookup]
  WholeWord --> Known[known spelling variants]
  Known --> Cyrillic[Mongolian Cyrillic candidate]
  WholeWord --> Unknown[unknown token retained]
  English[English words] --> Retain[original segment retained]
  Unknown --> Retain
  Cyrillic --> Candidate[bounded query candidates]
  Retain --> Candidate
```
`normalize_latin_mongolian` хамгаалсан identifier болон танигдаагүй үгийг өөрчлөхгүй; бүх роман бичвэрийг автоматаар хөрвүүлэхгүй.

```mermaid
flowchart TD
  Original --> Always[original candidate always first]
  Original --> Class[language and normalization confidence]
  Class -->|strong Cyrillic or English| Keep[keep original form]
  Class -->|known Latin Mongolian| Normalize[add normalized candidate]
  Class -->|mixed / uncertain + rewrite enabled| Rewrite[one structured rewrite request]
  Rewrite --> Validate[validate length and protected tokens]
  Validate -->|valid| Add[add candidate]
  Validate -->|invalid| Keep
  Keep --> Cap[dedupe and cap at three]
  Normalize --> Cap
  Add --> Cap
```
`query_candidates` болон `rag.ask` candidate limit-ийг барина. Query rewrite default off.

## 5. Бодит call graph ба үнэлгээ

```mermaid
flowchart LR
  message["chat.message"] --> ask["rag.ask"]
  ask --> candidates["language.query_candidates"]
  ask --> versions["retrieval.active_versions"]
  ask --> retrieve["retrieval.retrieve"]
  retrieve --> dense["dense_search then gemini.embed then Chroma.query"]
  retrieve --> sparse["sparse.search then load_index"]
  dense --> rrf["sparse.rrf"]
  sparse --> rrf
  rrf --> rerank["optional _reranker"]
  ask --> context["rag.build_context"]
  context --> generate["gemini.generate"]
  generate --> validate["rag.validate_answer"]
  validate --> log["QueryLog commit"]
```
Энэ нь кодын бодит гол дуудлагын зам; `app/services` модуль дотор хадгалагдана.

```mermaid
flowchart TD
  Dataset[legacy or multilingual JSONL] --> Split[intent grouped dev/test]
  Split --> Retrieve[run_evaluation]
  Retrieve --> Recall[Recall / Hit Rate / Precision / MRR / nDCG]
  Retrieve --> Lang[language + normalization breakdown]
  Retrieve --> Latency[per stage latency]
  Generate[optional answer generation] --> Compliance[answer language compliance]
  Recall --> Snapshot[immutable corpus/config snapshot]
  Lang --> Snapshot
  Latency --> Snapshot
  Compliance --> Snapshot
```
`app/evaluation.py:run_evaluation`, `metrics`, `compare_runs` нь legacy `mn`/`en` dataset уншина. Нэг intent-ийн хувилбарууд ижил split-д байна. Synthetic fixture нь зөвхөн хөгжүүлэлтэд; approved бодит баримт, reviewed evidence label байхгүй үед эцсийн чанарын acceptance pending.

## 6. Үйл ажиллагаа ба хязгаарлалт

Hybrid default. Буцаахын тулд `RETRIEVAL_MODE=dense`; индекст өөрчлөлт хийхгүйгээр query branch-ийг солино. Одоо байгаа бэлэн хувилбаруудын sparse artifact-ыг `python -m flask --app app backfill-sparse` командаар үүсгэнэ. Reconcile болон deletion sparse artifact-ыг хамруулдаг.

Монгол латин normalization нь хувилбарын толь ашигладаг хамгаалалттай candidate generation; бүх диалект, typo, romanization-ийг хамрахгүй. Англи асуултыг эх хувилбараар multilingual embedding-д өгнө. Query rewrite default off, зөвхөн mixed/unknown дээр нэг хүсэлт хийнэ. Хэл илрүүлэлт болон output checker heuristic тул хүний үнэлгээг орлохгүй. Corpus, reviewed label, бодит latency/quality benchmark-гүй тул чанарын босго батлагдаагүй.
