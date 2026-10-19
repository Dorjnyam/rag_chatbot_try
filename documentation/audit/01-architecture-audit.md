# 01 — Архитектурын аудит

## Бодит бүтэц

Flask app factory нь тохиргоо уншиж, SQLAlchemy/Migrate, route болон CLI-г бүртгэнэ. PostgreSQL нь баримтын metadata, version, query log, evaluation хадгалдаг; Chroma нь chunk текст/вектор хадгалдаг; filesystem нь эх файл, extraction болон manifest/artifact хадгална. Provider factory OpenAI эсвэл Gemini adapter сонгоно. Retrieval нь active version-уудаар шүүж dense болон sparse салбарыг ажиллуулж, RRF нэгтгэл, optional reranker, context/generation руу дамжуулдаг.

```mermaid
flowchart LR
  User[Browser user] --> Flask[Flask app factory and routes]
  Flask --> PG[(PostgreSQL metadata, query, evaluation)]
  Flask --> Files[Filesystem source, extraction, manifest]
  Flask --> Chroma[(Chroma chunks and vectors)]
  Flask --> Sparse[Versioned BM25 JSON]
  Flask --> Factory[Provider factory]
  Factory --> OpenAI[OpenAI adapter]
  Factory --> Gemini[Gemini adapter]
  Retrieval[Active-version retrieval] --> Chroma
  Retrieval --> Sparse
  Retrieval --> RRF[RRF and optional reranker]
  RRF --> Context[Context and citations]
  Context --> Factory
```

```mermaid
flowchart TD
  Start[Request begins with deadline] --> Try[Route or service call]
  Try --> Success[Structured result and trace]
  Try --> Error{Exception?}
  Error -->|yes| Record[Rollback session and record error]
  Record --> Mongolian[Localized error response]
  Success --> Persist[Persist query or evaluation record]
```

```mermaid
flowchart TD
  Request[HTTP request] --> Before[Host and CSRF before_request]
  Before -->|valid| Route[Blueprint route]
  Before -->|invalid| Error[HTTP error handler]
  Route --> Service[Ingestion, RAG, or evaluation service]
  Service --> Store[(PostgreSQL / Chroma / filesystem)]
  Service --> Provider[Selected provider]
  Service --> Template[Jinja response or JSON]
  Template --> Browser[HTML and app.js]
```

```mermaid
flowchart LR
  subgraph Current[Одоогийн execution path]
    Q[question] --> Detect[query_candidates]
    Detect --> Dense[query embedding + Chroma]
    Detect --> BM25[BM25 per active version]
    Dense --> Fuse[RRF]
    BM25 --> Fuse
    Fuse --> Rank{reranking enabled?}
    Rank -->|yes| Cross[local CrossEncoder]
    Rank -->|no| Ctx[context + citation validation]
    Cross --> Ctx
    Ctx --> Gen[OpenAI or Gemini]
  end
```

## Call graph ба ownership

Entrypoint: `app:create_app` → `app/routes/*` and `app/cli.py`. Upload/process route → `services/ingestion.py` → `parsing.py` / `chunking.py` / `providers.py` → `vectorstore.py` + `sparse.py` → PostgreSQL active pointer. Chat route → `services/rag.py:ask` → `language.query_candidates` → `retrieval.retrieve` → provider embedding + Chroma/BM25 → `sparse.rrf` → optional CrossEncoder → `build_context` → adapter generation → `validate_answer` → `QueryLog`. Evaluation reuses retrieval/chat services and stores snapshots/results through `evaluation.py`.

`storage.py` owns filesystem atomic writes and cross-process mutation lock. `index_migration.py` owns provider index prepare/activate/rollback. `cli.py` exposes doctor, process/reconcile, sparse backfill, migration and evaluation commands. This division is reasonably understandable, though `rag.py` still coordinates detection, retrieval, generation, validation, repair and query logging.

## Dependency болон ирээдүйн боломж

Import graph-аас `app` → `services` → `models/extensions` чиглэлийн ерөнхий урсгал харагдана; obvious circular import статик шалгалтаар нотлогдоогүй. Provider interface нь embedding/generation/rewrite/counting-ийг багтаадаг. OCR/parsing provenance болон metadata нь multimodal өргөтгөлийн эхлэл боловч зураг/аудио/видео ingestion, modality-aware chunk, multimodal embedding/search одоогоор хэрэгжсэн гэж үзэх нотолгоо алга. Иймд multimodal readiness-ийг **NOT IMPLEMENTED / not scored as broken** гэж ангилна.

**Архитектурын дүгнэлт:** нэг локал хэрэглэгчийн сургалтын хэрэгсэлд ойлгомжтой modular монолит. Хамтын үйлчилгээний scale/security boundary, worker queue болон shared vector service шийдээгүй.
