# 02 — Backend / Flask кодын аудит

## App initialization ба request boundary

`app/__init__.py:create_app` dotenv/settings уншиж, secret үүсгэх, config validation, extension болон route/CLI registration хийдэг. Flask signed session нь cookie дээр; local app зориулалтын `SECRET_KEY` нь `.env`-ээс эсвэл `instance/session.key`-ээс авна. `local_csrf` loopback бус remote address-ийг хааж, POST/PUT/PATCH/DELETE дээр Origin болон CSRF token шалгана. Энэ нь local-only хэрэглээнд тохирсон boundary; олон хэрэглэгчийн identity биш.

Route-ууд ерөнхийдөө үйлчилгээ рүү delegation хийдэг. `documents.py` upload, review, version, delete; `chat.py` chat, query history, evaluation болон review route-уудтай. `safe_return_to` local path-аар хязгаарлах тесттэй. HTTP алдаанууд Монгол template/message буцаана. `rag.ask` алдааг QueryLog-д бичдэг ч `str(exc)`-ийг `log.error` талбарт хадгалдаг: provider SDK exception дотор URL, request metadata зэрэг мэдээлэл орох боломжийг production log disclosure threat model-д шалгах шаардлагатай.

## Батлагдсан асуудлууд

- **AUDIT-001:** `tests/conftest.py:43-63` `rag_test`-ээр эхэлсэн дурын DB нэрийг зөвшөөрч, fixture setup/teardown-д `drop_all()` ажиллуулдаг. Өндөр эрсдэлийн test guard; integration тест энэ удаа ажиллаагүй.
- **AUDIT-003:** `services/rag.py:117-123` optional rewrite adapter exception-ийг local fallback-гүй орхидог; гаднах catch query-г error болгоно.
- **AUDIT-009:** `services/ingestion.py:90-98` эх файлыг filesystem-д `new_version()` DB commit-ээс өмнө бичдэг. Commit failure нь DB reference-гүй файл үлдээх нөхцөл үүсгэнэ.

Мөн `chat.py` query detail-ийн hit бүрт DB source болон Chroma chunk-ийг тус тус татах execution path байна (`chat.py:84-97`): N+1/serial lookup нь олон citation-тэй trace дээр latency өсгөх боломжтой (**AUDIT-010**).

## Функц ба feature inventory-ийн хураангуй

Route decorators, Flask CLI registration, `app/services/providers.py:provider`, `retrieval.py`-ийн lazy reranker loader, migration/backfill command-уудыг caller/reachability гэж тооцсон. Тэдгээрийг `UNUSED` гэж буруу ангилаагүй. `requirements-reranker.txt` optional dependency; `sentence-transformers` үндсэн environment-д алга, flag default false. Browser harness нь production route биш test-only тул одоо ажиллуулаагүй.

Функц бүрийн дэлгэрэнгүй inventory [10](10-feature-and-dead-code-inventory.md)-д. Python style/lint/dead-code scanners суусангүй; AST/import/reference шалгалтаар бүх dynamic runtime usage-г батлах боломжгүй.
