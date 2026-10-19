# Локал RAG — найдвартай байдал ба evaluation шинэчлэл

## Хэрэгжүүлсэн өөрчлөлт

- Test fixture нь explicit `TEST_DATABASE_ADMIN_URL`-аас шинэ UUID database үүсгэж, ownership comment таарсан үед зөвхөн өөрийн DB-г устгана. Хуучин `rag_test*` database-г дахин ашиглахгүй, дурын DB-д `drop_all()` хийхгүй. API key болон dotenv тусгаарлагдсан.
- Query rewrite хамгийн ихдээ нэг HTTP оролдлоготой; exception, invalid text болон identifier өөрчлөлтөд original candidate fallback. Adapter-ийн бусад үйлдэл bounded retry/deadline хэвээр.
- Citation/schema/language repair хоёр logical generation invocation-д багтана. Invalid repair нь Монгол safe abstention, хоосон citations өгнө. Client cleanup DB commit failure үед ч ажиллана.
- Flat RRF нь dense/sparse candidate ranking бүрт нэг удаа үйлчилнэ. Branch доторх duplicate ID нэмэлт contribution авахгүй. Local reranker score count/type/finiteness-ийг баталсны дараа дараалал өөрчлөгдөнө.
- Sparse artifact digest, type, canonical IDs/metadata болон active manifest шалгалттай. Cache нь data root, content fingerprint болон eligible corpus-аар тусгаарлагдсан, bounded. Active manifest байхгүй эсвэл artifact corrupt үед explicit dense fallback; үүнийг hybrid acceptance гэж тооцохгүй.
- Context нь reranked pool-оос сонгогдож, final K-ээр хязгаарлагдахгүй. Зөвхөн ижил version/source boundary дахь баталгаатай overlap trim хийнэ. Каноник chunk text/ID өөрчлөгдөхгүй; supplied substring bounds нь `context_location`-д хадгалагдана.
- Chat/evaluation history pagination, context-only evidence trace болон query-detail batch source loading нэмэгдсэн.
- `/ready` нь non-secret PostgreSQL/storage/provider configuration/index compatibility checks; paid provider call болон index initialization хийхгүй. Cold Chroma catalog probe нь full vector integrity check биш.

## Үзүүлэлтийн contract

`evidence-recall-binary-chunk-v2` revision ашиглана. Evidence Recall нь gold unit-ийг давхцаагүй тоолно; Precision/MRR/Hit Rate нь canonical chunk relevance ашиглана. nDCG denominator нь бүх eligible corpus дахь relevant chunk-уудын тоонд тулгуурлана. Бүрэн mapping байхгүй үед nDCG null байна. Нэг chunk хоёр unit хамарвал recall хоёр unit, precision/DCG нэг chunk болно. Өмнөх report-уудыг дахин нэрлэхгүй, metric revision зөрсөн run-уудыг compare хийхгүй.

Language policy `pass/fail/uncertain` гэсэн conservative heuristic; calibrated Монгол хэлний classifier биш. Latin prose үлдэж шалгагдана; зөвхөн мэдэгдэж буй source title/filename/identifier-уудыг exempt хийнэ. First model response, repair, application fallback, delivered answer гэсэн үр дүнг тус тусад нь trace-д бичнэ. Citation-ID validity бол syntactic contract; faithfulness, citation support болон normalization meaning нь хүний review шаарддаг.

## Benchmark бэлтгэл ба pending хэсэг

`flask benchmark-plan DATASET --split dev --top-k 5` нь dense → hybrid → hybrid+reranker → hybrid+reranker+rewrite matrix, logical request estimates болон prerequisite-ийг харуулна. DB/provider client үүсгэхгүй, live budget 0. Хэрэгжүүлэлтийн явцад paid API, model download болон company-document reindex хийгээгүй.

Metadata-enriched embedding, structure-aware chunking, conversation-aware rewriting нь дараагийн gated туршилтууд. Шинэ embedding/chunk strategy-д тусдаа staged version, fingerprint, controlled activation/rollback хэрэгтэй; одоогийн active source/index-ийг автоматаар солихгүй. Одоогоор эдгээрийг default pipeline-д нэмээгүй.

Reviewed company gold labels, тохирох local reranker path болон live benchmark budget байхгүй тул бодит Монгол/English/romanized/mixed retrieval, answer quality ба latency/cost acceptance **хүлээгдэж байгаа**. Synthetic/mock тест нь зөвхөн engineering behavior батална. Browser visual шалгалт saved permission denial-оос шалтгаалан хүлээгдэж байгаа; түүнийг тойроогүй.

## Rollout ба rollback

Dependency/schema migration нэмээгүй. Deployment хийхдээ хэвийн Flask process-оо restart хийж шинэ код ачаална. `/health` дараа `/ready` шалгаж, эхлээд retrieval-only synthetic smoke хэрэглэнэ. Reranking болон rewriting false хэвээр. Шаардлагатай rollback нь өмнөх code revision; dense-only `RETRIEVAL_MODE=dense` нь sparse fallback зам. Хуучин index/citation/evaluation history-г устгахгүй.

## Шинэ баталгаажуулалт — 2026-10-19

Бүтэн suite: **120 passed, 1 skipped**, 61.82 секунд. Дараа нэмсэн бодит disposable PostgreSQL + synthetic Chroma `/ready` integration: **1 passed**, 2.62 секунд. Нийт 122 давхцаагүй case-аас 121 PASS, 1 SKIPPED (Tesseract байхгүй). Full-suite lifecycle, migration, CSRF/session, navigation, multilingual/metrics болон шинэ failure regressions хамрагдсан. JUnit output external temporary directory-д бичигдсэн; үр дүнгийн хураангуй `documentation/validation/reliability-2026-10-19.json`-д байна.

`node --check app/static/app.js` болон `uv pip check` PASS (120 package compatible). Code whitespace check PASS. Independent reviewer flat fusion, integrity fallback, context/citation provenance, metric revision, client cleanup болон disposable database ownership-ийг давтан шалгасан. Тестүүд live provider ашиглаагүй; энэ дүнг бодит Монгол model quality гэж тайлагнахгүй.
