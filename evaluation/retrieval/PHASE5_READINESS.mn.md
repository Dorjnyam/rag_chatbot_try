# Phase 5 — Retrieval хаалтын тайлан

## Executive summary

R1–R4 correctness gate-ийн хүрээнд BM25 known-answer, RRF, active version/source provenance, exact identifier ба negation хамгаалалтыг шалгав. Шалгалтаар RRF-ийн raw score field-үүд дарагдаж болох бодит presentation/data-contract defect илэрсэн; засвар нь branch score/distance-ийг салгаж хадгална. Дараа нь R5 exact-query normalization fixture, R6 isolated offline mechanics benchmark гүйцэтгэсэн. Production parser, splitter, embedding config, PostgreSQL, ChromaDB өөрчлөгдөөгүй; embedding API дуудлага 0.

## Шийдвэрүүд

| Хаалга | Шийдвэр | Үндэслэл |
|---|---|---|
| **Operational Retrieval Readiness** | **READY WITH DOCUMENTED LIMITATIONS** | Кодын correctness tests, fail-closed source validation, dedicated isolated Phase 3.5 PostgreSQL дээрх full regression **194 passed, 1 skipped, 0 failed**. Concurrency, production-scale load болон бүх DB/vector failure mode баталгаажаагүй. |
| **Human Relevance Evaluation** | **NOT READY** | 2 independent approved questions / 6 correlated variations нь intended Mongolian/English/Latin, policy ID/date/clause, exception, table, unanswerable query төрлүүдийг төлөөлөхгүй. |
| **Advanced Retrieval Optimization** | **DEFERRED** | BM25/Dense/Hybrid-ийн relevance comparison байхгүй; шинэ reranker, LLM rewrite/decomposition, HyDE/SPLADE, шинэ embedding хэрэгжүүлэх үндэслэлгүй. |
| **Phase 6 — Generation Entry** | **READY WITH LIMITATIONS for offline generation integration only; quality evaluation not ready** | Generation pipeline-ийн isolated plumbing-ийг source validation, trace, abstention contract-тай хөгжүүлж болно. Retrieval quality батлагдсан мэт production-quality/answer quality дүгнэлт хийх боломжгүй. |

## Benchmark evidence

`PERFORMANCE_RESULTS.mn.md` дахь 30 timed mock/offline sample: BM25 P50/P95 **649.30/705.74 ms**, dense Chroma synthetic-vector **18.65/22.51 ms**, RRF **0.107/0.162 ms**, source validation **15.63/20.77 ms**, context assembly **19.46/44.86 ms**, total mechanics P50/P95 **704.23/782.34 ms**. BM25 нь энэ run-ийн latency-д давамгайлав. Энэ нь сайжруулахын өмнөх profile signal; халаалт, хэш шалгалт, version scan холилдсон тул algorithm defect эсвэл тогтвортой SLA гэж үзэхгүй.

Чухал: dense vectors санамсаргүй mock; тиймээс Recall@k, MRR, nDCG, citation accuracy, Mongolian semantic relevance **N/A**. Two-question smoke нь зөвхөн code path ба контекстийн pipeline-г шалгана. Phase 4 synthetic ANN fidelity ч мөн relevance evidence биш.

## Хязгаар ба дараагийн ажил

- 16 draft annotation queue `instance/retrieval-evaluation/annotation-queue-round1.json` бэлэн; queue нь reviewer-д зориулсан convenience sample бөгөөд retrieval failure-ээр stratify хийгдээгүй.
- Одоогоор 80 candidate-ийн query type-ийн ихэнх нь хоосон; 16 сонгосон candidate бүгд ангилагдаагүй. Reviewer эх сурвалжийг нягталж өөрөө query type/class, question, evidence, answer-ийг батална.
- API provider latency, retries, PostgreSQL source query, concurrent-user load болон generation хэмжээгүй.
- Full test suite: **194 passed, 1 skipped, 0 failed**, 87 warnings, 87.14 секунд. Skip: `tests/test_native.py:52`, Tesseract missing. Warnings: SQLAlchemy `get_engine()` deprecation болон Chroma legacy embedding configuration deprecation; test failure биш. JUnit `instance/test-results/phase5-retrieval.xml`.
- `instance/` дахь JUnit/private result-ийг public report-д evidence text хэлбэрээр гаргахгүй.

## Давтагдах командууд (PowerShell)

```powershell
# Targeted correctness + benchmark harness tests
.\.venv\Scripts\python.exe -m pytest tests\test_retrieval_reliability.py tests\test_retrieval_benchmark.py tests\test_core.py -q -p no:cacheprovider --basetemp=instance\pytest-phase5-targeted-final

# Private offline R6 benchmark; synthetic Chroma vectors, no API/DB
.\.venv\Scripts\python.exe -m flask --app app benchmark-retrieval offline --iterations 5 --seed 72026

# Full regression using isolated Phase 3.5 PostgreSQL
.\scripts\start-phase35-test-postgres.ps1
$env:TEST_DATABASE_ADMIN_URL = 'postgresql+psycopg://rag_phase35_test@127.0.0.1:55439/postgres'
$base = Join-Path $PWD ('instance\pytest-phase5-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory $base | Out-Null
.\.venv\Scripts\python.exe -m pytest -q -rs -p no:cacheprovider --basetemp=$base --junitxml=instance\test-results\phase5-retrieval.xml
Remove-Item Env:TEST_DATABASE_ADMIN_URL
```

Private benchmark results and review data remain under Git-ignored `instance/`.
