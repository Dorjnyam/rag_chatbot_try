# Phase 6 — Context Augmentation ба Answer Generation: Үр дүн

## Товч дүгнэлт

Context selection, source provenance, input budget, OpenAI structured response ба citation validator-ийг deterministic шалгалтаар чангалав. `rag.ask()`-ийн production flow, A0 chunker, embedding model, PostgreSQL schema, Chroma collection өөрчлөөгүй. Real OpenAI generation болон paid API call хийгээгүй.

Эцсийн төлөв:

- **Operational Generation:** READY WITH DOCUMENTED LIMITATIONS
- **Context/Citation Integrity:** VERIFIED WITH LIMITATIONS
- **Human Answer Quality:** NOT READY
- **Production Readiness:** NOT READY

Эхний хоёр нь зөвхөн шалгасан deterministic operational contract-д хамаарна. 2 human-verified question болон 6 холбоотой language variation нь бодит answer quality-ийн өргөн төлөөлөх үнэлгээ хийхэд хүрэлцэхгүй. Fake response-ийн 90/90 validation нь хүний зөв хариулт, claim support, Монгол хэлний чанар биш.

## G1–G4: баталгаажсан өөрчлөлт

- **Canonical evidence mapping:** context-д орсон `S#` бүр `chunk_id`, `document_id`, `version_id`, олдсон source location-той immutable mapping авна. Log citation зөвхөн энэ selected map-аас үүснэ. Retrieval pool-ийн mutable label-аас citation сэргээхгүй.
- **Identity/provenance:** selected source-д canonical chunk/document/version ID байхгүй бол fail closed. Нэг canonical chunk ID зөрчилтэй text эсвэл source identity-д заагдвал context assembly зогсоно. `page`, `clause_id`, `section` байхгүй бол зохиохгүй.
- **Dedup/overlap:** зөвхөн ижил ID эсвэл ижил document/version/source character span дээрх ижил текстийг deduplicate хийнэ. Давтагдсан текст өөр source span-д байвал тус тусын provenance хадгална. Overlap trimming нь ижил document/version, anchor ба текстийн яг давхцлаар хязгаарлагдана; source-н нийлмэл текстийг negation/exception-той нь сэргээх test бий.
- **Budget:** serialized OpenAI Responses input болон JSON schema-г `tiktoken` ашиглан тооцож 32-token margin нэмнэ. Хэтэрсэн history-г хуучнаас нь, evidence-г ranked жагсаалтын төгсгөлөөс бүтнээр хасна; exclusions болон estimate-ийг query trace-д бүртгэнэ. Initial эсвэл repair input таарахгүй бол provider-ийг дуудахгүй.
- **Output/citation:** exact top-level JSON fields, duplicate JSON key, duplicate source, unknown label, inline citation/list mismatch-ийг reject хийнэ. `answered` нь citation шаарддаг; partial `insufficient_evidence` хариулт зөвхөн батлагдсан хэсгээ citation-тай өгөх боломжтой.
- **Provider configuration:** schema revision болон tokenizer identity/fallback log-д орно. Provider-т илгээгдээгүй `temperature=0` утгыг config snapshot-аас авч, `null` буюу илгээгээгүй гэж тэмдэглэв. Provider usage-ийн nested counters олон attempt-д нэмэгдэнэ; usage ирээгүй бол `generation_usage_status` unknown/unavailable гэж ялгана. Non-`completed` generation response reject хийнэ.
- **Энэ нь claim entailment биш:** citation ID хүчинтэй байх нь тухайн claim-ийг ишилсэн хэсэг дэмждэгийг батлахгүй. Repair дахь protected number/ID parity ч semantic утга, negation, нөхцөл өөрчлөгдөөгүйг бүрэн батлахгүй.

Бүрэн research-to-code traceability: [CHAPTER9_TRACEABILITY.mn.md](CHAPTER9_TRACEABILITY.mn.md). Supporting research: [CHAPTER8_10_MAPPING.mn.md](CHAPTER8_10_MAPPING.mn.md). Хүний review-д зориулсан хоосон rubric: [HUMAN_ANSWER_REVIEW_TEMPLATE.mn.json](HUMAN_ANSWER_REVIEW_TEMPLATE.mn.json). Баталгаагүй template item `human_confirmed=false`, `annotation_status=unreviewed` хэвээр.

## G7: offline benchmark

Final run `6e04a8856e0b` нь result-ээ Git-ignored `instance/generation-evaluation/offline/6e04a8856e0b/result.json` дотор хадгалсан. Файлын бодит байтын SHA-256: `f31d40f35e21da089db59d3436a128238ffde656ff23f5ef9e1e92ca0f949377`. Энэ run хамгийн сүүлийн app код дээр хийгдсэн; өмнөх давталтуудаас ялгаатай нь benchmark-ийн scoped worktree hash тооцоолол app/tests файлуудаар хязгаарлагдсан.

Нэг benchmark run: 6 private ground-truth query variation + 12 synthetic fixture; нэг cold pass, нэг warm-up, **90 timed sample**. Private source text, question, expected answer-ийг result файлд хадгалаагүй. Fake provider-ын response нэг тогтмол JSON байсан.

| Stage | P50 ms | P95 ms | P99 ms | Sample |
|---|---:|---:|---:|---:|
| Context assembly + token budget | 1.631 | 7.992 | 12.715 | 90 |
| Prompt JSON serialization | 0.026 | 0.046 | 0.055 | 90 |
| Fake generation orchestration + validation | 0.544 | 1.846 | 2.519 | 90 |
| Output validation | 0.038 | 0.049 | 0.102 | 90 |
| Offline total | 2.245 | 9.978 | 14.899 | 90 |

`Offline total` нь дээрх stage-уудыг дотроо агуулсан тусдаа хэмжилт; stage percentiles-ийг нийлүүлээгүй. Энэ latency-д retrieval, PostgreSQL, Chroma, сүлжээ, OpenAI inference, TTFT ороогүй.

Тусгаарласан cold first pass-ийн 18 sample-д P50/P95/P99 `2.384 / 103.597 / 103.597 ms` байв. Ижил functional code/config-той өмнөх давтан process-д cold P95 `90.246`, `93.629 ms`. Slowest sample бүр context/token-budget stage-д, эхний private variation-ын 1,819-token evidence-т таарсан; дараагийн ижил хэмжээний case-үүд ~7–8 ms context stage байсан. Энэ нь cold token-count path тогтмол давтагдаж буйг харуулж байна. Үүнийг provider latency гэж үзээгүй; startup дээр tokenizer warm хийх өөрчлөлт хийгээгүй.

Өмнөх гурван ижил functional code/config warm run болон final run бүр 90 sample-тэй. Final run-ийн scoped hash тооцооллыг тусад нь өөрчилсөн боловч timed stage-д нөлөөлөхгүй. Ижил функциональ benchmark-ийн run хоорондын хүрээ:

| Warm offline total | P50 хүрээ ms | P95 хүрээ ms | P99 хүрээ ms |
|---|---:|---:|---:|
| 3 same-revision runs | 2.168–2.253 | 9.358–9.820 | 9.515–15.030 |

P99 бүр run тус бүр 90 sample-аас гарсан тул tail/SLA баталгаа биш. Нэг run-ийн P99 өсөлт нь context stage-ийн нэг 15 ms sample байв; tail SLA гэж тайлбарлахгүй. Token estimate P50: request 607, evidence 30. `gpt-6-luna`-д `tiktoken.encoding_for_model` mapping олдоогүй тул local estimate `o200k_base` fallback ашигласан; энэ estimate provider-ийн usage/billing биш. Python allocation peak `1,217,066 bytes`; process RSS хэмжүүр энэ орчинд боломжгүй байсан. Нэг run-ийн process CPU time 0.469 секунд.

Бүх timed synthetic/private mechanics sample: **90 validated, 0 invalid, 0 repair**. Process дотор fake `generate` 126 удаа дуудагдсан (cold 18 + warm-up 18 + timed 90). Энэ нь нээлттэй API call биш: `network_api_calls=0`, PostgreSQL=false, Chroma=false.

## G8: human quality readiness

Ground-truth export `gt-v1` нь 2 independent question, 6 language variation-тай. Нэг асуултын language variation-ууд хамааралтай тул зургаан бие даасан test case гэж тооцоогүй. 16 draft question review хийгдээгүй; review хийхийг шаардаагүй. Generation output-д зориулсан хүний annotation одоогоор байхгүй.

Review rubric нь correctness, claim faithfulness, citation support, completeness, abstention, Монгол хэлний ойлгомж, critical number/date/unit/negation/version алдааг тусад нь үнэлнэ. Answerability reference байхгүй эсвэл эх сурвалж хүрэлцэхгүй үед оноо **N/A** байна. Одоогийн хэмжилтээр quality score, confidence interval, model superiority тооцоогүй.

## Хязгаарлалт ба дараагийн шийдвэр

1. Chapter 1 Load-ийн PDF reading order, OCR, table/heading hierarchy хязгаарлалтыг Phase 6 сэргээгээгүй. Citation нь parser-аас ирсэн боломжит anchor-ийг л харуулна.
2. `o200k_base` fallback нь model тусгай encoding mapping-ийг орлох approximation; provider token usage-аар cross-check хийгдээгүй.
3. Regex/language guard нь Монгол утга, ажлын өдөр/хуанлийн өдөр, negation, exception хадгалагдсаныг нотлохгүй.
4. Adversarial tests source label spoof болон untrusted-data instruction boundary-г шалгасан. Ерөнхий prompt injection хамгаалалт батлагдаагүй.
5. Reranking, semantic compression, Parent–Child, context reordering, router, HyDE, Self-RAG/CRAG, RAPTOR, GraphRAG хэмжээгүй, хэрэгжүүлээгүй.
6. 2 verified question дээрх бодит OpenAI generation туршилт, cost estimate, human answer review нь дараагийн тусдаа зөвшөөрөлтэй gate хэвээр. Энэ Phase 6 run-д paid call хийгээгүй.

### Эцсийн gates

| Gate | Шийдвэр |
|---|---|
| **Operational Generation** | **READY WITH DOCUMENTED LIMITATIONS** — schema, citation consistency, budget, failure contracts deterministic regression-ээр шалгагдсан. |
| **Context/Citation Integrity** | **VERIFIED WITH LIMITATIONS** — selected source identity/span mapping болон exact overlap шалгагдсан; semantic support хүний үнэлгээгүй. |
| **Human Answer Quality** | **NOT READY** — generation output-д human-reviewed correctness/faithfulness labels алга; 2 retrieval reference хангалтгүй. |
| **Production Readiness** | **NOT READY** — authorization ба бодит organization answer quality энэ ажилд үнэлэгдээгүй. |

## Reproduction commands

PowerShell, workspace root дээр:

```powershell
# Offline generation mechanics; no API, PostgreSQL, or Chroma access
.\.venv\Scripts\python.exe -m flask --app app benchmark-generation offline --iterations 5

# Targeted Phase 6 reliability/SDK/context tests
.\.venv\Scripts\python.exe -m pytest tests\test_generation_benchmark.py tests\test_rag_reliability.py tests\test_sdk.py tests\test_core.py -q -p no:cacheprovider --basetemp=instance\pytest-phase6-final-targeted4

# Start/check the dedicated Phase 3.5 test cluster (identity guard)
.\scripts\start-phase35-test-postgres.ps1

# Full regression: use the verified isolated PostgreSQL cluster only
$previousTestUrl = $env:TEST_DATABASE_ADMIN_URL
try {
  $env:TEST_DATABASE_ADMIN_URL = 'postgresql+psycopg://rag_phase35_test@127.0.0.1:55439/postgres'
  $base = Join-Path $PWD ('instance\pytest-phase6-' + [guid]::NewGuid().ToString('N'))
  $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
  $junit = Join-Path $PWD ("instance\test-results\phase6-$stamp.xml")
  New-Item -ItemType Directory $base | Out-Null
  New-Item -ItemType Directory (Split-Path -Parent $junit) -Force | Out-Null
  .\.venv\Scripts\python.exe -m pytest -q -rs -p no:cacheprovider "--basetemp=$base" "--junitxml=$junit"
} finally {
  if ($null -eq $previousTestUrl) { Remove-Item Env:TEST_DATABASE_ADMIN_URL -ErrorAction SilentlyContinue }
  else { $env:TEST_DATABASE_ADMIN_URL = $previousTestUrl }
}
```

Сүүлийн бүрэн regression: **213 passed, 1 skipped, 0 failed**, 87 warnings, 81.48 s. Skip: `tests/test_native.py:52`, Tesseract байхгүй. Warning-ууд Flask-Migrate-ийн deprecated `get_engine()` болон Chroma legacy embedding configuration байна. JUnit: `instance/test-results/phase6-final-20261022-115000.xml`. Өмнөх environment variable байсан бол командыг ажиллуулахаас өмнө хадгалж, дараа нь буцаан сэргээнэ.
