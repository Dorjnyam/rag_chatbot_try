# Phase 1 — RAG баримт ачаалах үе шатны аудит

**Хамрах хүрээ:** Load буюу upload → validation → parse/conversion/OCR → text/block extraction. Split, embedding, vector store, retrieval болон production өгөгдөлд өөрчлөлт оруулаагүй. Кодын дүгнэлтийг энэ repository дээрх бодит Flask route, service, config, parser болон тестээс гаргав.

## Архитектурын зам

`app/routes/documents.py:169-180` дахь `POST /documents` болон `POST /documents/<document_id>/versions` нь multipart `file` авч `ingestion.upload()` руу дамжуулна. Flask request cap `app/config.py`-д 20 MiB. `ingestion.upload()` extension болон supplied MIME-ийг шалгаж, content-ийг 1 MiB хэсгээр уншиж SHA-256 тооцон spool хийнэ, signature шалгаад content-addressed файл хадгалж `Document`/`DocumentVersion` үүсгэнэ. Энэ шатны `accepted_pending_parse` нь бүрэн parse болсны баталгаа биш.

Хэрэглэгч **Process** хийхэд `app/routes/documents.py` process route → `ingestion.process()` → `parse_bounded()` → тусгаар subprocess дахь `parse_direct()` явна. Parser амжилттай бол extraction cache болон хувилбарын төлөв хадгалагдаж review/indexing үргэлжилнэ. Энэ үнэлгээний runner нь зөвхөн `parse_bounded()`-ийг дуудна; DB, Chroma, embedding болон indexing дуудахгүй.

## Олдвор ба ангилал

| Шалгах зүйл | Олдвор | Ангилал |
|---|---|---|
| Upload endpoint / validation | `routes/documents.py:169-180`; `ingestion.py:69-167`. Өргөтгөл, multipart MIME hint, non-empty, хэмжээ, PDF/ZIP/OLE signature шалгана. MIME нь client-ийн claim тул дангаараа найдвартай нотолгоо биш. | Хэсэгчлэн хэрэгжсэн; шинэ checks-ийн verification тестээр хүлээгдэнэ |
| Streaming / хэмжээг хязгаарлах | 20 MiB Flask cap; service дээд хязгаараас 1 byte илүү уншиж хэтрэлтийг илрүүлдэг байсан. Одоо 1 MiB chunk болон 1 MiB memory threshold-той spool ашиглана. Multipart parser өөрөө route ажиллахаас өмнө request data-г parse хийж болно. | Хэрэгжсэн, runtime benchmark хүлээгдэнэ |
| Storage / cleanup / duplicate | `ingestion.upload()`, `new_version()`, `delete_document()`, `reconcile()`: SHA-256 content-addressed эх файл, config hash-тай duplicate lookup, DB бичилт унахад шинэ эх файлыг зөвхөн reference байхгүйг шалгаж цэвэрлэнэ. Extraction cache нь source hash/parser/OCR/native tool fingerprint-тэй. | Кодод хэрэгжсэн; тусгай duplicate шалгалт нэмэгдсэн |
| PDF validation | `parsing.py:39-48`: PDF signature, `pypdf` уншилт, encrypted PDF татгалзалт, page cap. Invalid xref/эвдэрхий файлын алдааг parser subprocess буцаана. | Одоо байгаа parser тестүүдтэй; өргөн real-world корпус дээр бүрэн бус |
| PDF extraction / scanned / mixed | `parsing.py:126-165`: page бүрийн native text; бага тэмдэгт, replacement character эсвэл forced page-д OCR; 25 MP render cap, OCR timeout. Text layer ба scan холилдсон хуудсыг page түвшинд боловсруулна. Бүх хоосон/бага текст хуудсыг OCR оролдож болно; Tesseract/language data байхгүй эсвэл OCR timeout бол parse бүтэлгүйтэнэ. | Кодод хэрэгжсэн, environment болон чанарын хэмжилттэй хараахан батлагдаагүй |
| DOCX extraction / tables | `parsing.py:50-65, 104-123`: ZIP expanded-size guard, `word/document.xml`, VBA project шалгалт, `python-docx`; paragraphs/headings/table row-г ordered block болгоно. Text box болон зураг доторх text-ийг OCR хийхгүй; merged cell, зураг, layout-д review warning өгдөг. | Хэсэгчлэн: бүтэц гаргана, image/layout completeness байхгүй |
| Legacy DOC | `parsing.py:68-93`: тусгаар LibreOffice profile, macro security өндөр, timeout, DOCX болгон хувиргаад DOCX validation. Local LibreOffice байхгүй бол conversion ажиллахгүй. | Хэрэгжсэн; native LibreOffice тест environment-ээс хамаарна |
| Unicode / Mongolian | `normalize()` нь NFC normalization, NUL устгал, whitespace цэгцэлнэ. OCR тохиргооны default `mon+eng`; хэлийг auto-detect хийж өөр OCR хэлээр дахин ажиллуулахгүй. | Хэрэгжсэн; Монгол хэлний CER хэмжих ground truth одоогоор байхгүй |
| Metadata / source provenance | `DocumentVersion`: filename, media type, SHA-256, config, file path, state. PDF block нь physical page; DOCX block нь heading/block/kind; DOC page number үүсгэхгүй. Clause number нь тусдаа parsed metadata биш, эх текстийн дотор хэвээр. | Хэсэгчлэн хэрэгжсэн |
| Logging / observability | Өмнө upload/parse structured event болон stage timings байгаагүй. Одоо `rag_upload` ба `rag_upload_parse` event нь document/version/run ID, format/size, parser stack/version, parser config, page/block/char/table/OCR/warning count, status, duration/error category бүртгэнэ. Текст болон бүтэн hash логдохгүй. | Хэрэгжсэн; runtime log aggregation хараахан хэмжээгүй |
| Optional debug artifacts | `LOAD_DEBUG_ARTIFACTS=false` default; enabled үед DATA_DIR дотор version/run нэртэй JSON artifact үүсгэж Unix дээр 0700/0600 эрх оролдоно. Windows дээр DATA_DIR-ийн inherited ACL хамгаалалт болно. | Хэрэгжсэн, тохиргоогоор шалгах шаардлагатай |
| Benchmark / quality ground truth | Шинэ `benchmark-load` manifest-driven runner нь `parse_bounded()` ашиглан JSON/CSV/Markdown гаргана. Бэлэн evaluation corpus 16 синтетик DOCX боловч extraction reference байхгүй; CER/accuracy одоогоор null. | Runner хэрэгжсэн; гүйцэтгэсэн baseline тайлан доор |
| Security / reliability gaps | Antivirus/malware scan байхгүй; parser validation нь content safety scanner биш. PDF attachment/JS идэвхтэй ажиллуулахгүй ч бүх төрлийн exploit-ийг үгүйсгэх баталгаа өгөхгүй. Upload MIME/extension нь client-controlled. | Байхгүй / энэ шатанд хэмжээгүй |

## Benchmark аргачлал ба давтагдах команд

Тогтмол manifest: `evaluation/load/manifest.json` — evaluation corpus-ийн 16 синтетик Mongolian/English DOCX. `evaluation/load/README.mn.md` нь ground-truth template болон гараар review хийх заавартай. Үзүүлэлт: parse амжилт, error class, файл хэмжээ, parser elapsed (subprocess overhead орсон), char/block/heading/table/OCR/warning тоо, wall throughput, median ба nearest-rank P95. Peak RSS нь одоогоор хэмжигдэхгүй: bounded parser child process-ийн peak memory-г хөндлөн платформоор хэмжихгүй.

```powershell
.venv\Scripts\python.exe -m flask --app app benchmark-load
.venv\Scripts\python.exe -m pytest -q tests/test_load_benchmark.py tests/test_native.py tests/test_core.py tests/test_safety.py tests/test_integration.py
```

Benchmark JSON/CSV/Markdown тайлан `evaluation/load/reports/`-д үүснэ. DB/vector call 0; production хүснэгт/collection-д өөрчлөлт оруулахгүй. Accuracy хэмжүүр зөвхөн manually verified reference өгсөн кейсэд тооцогдоно. P95, throughput нь энэ нэг дараалсан local run-д хамаарах ба production concurrency/scalability-г батлахгүй.

## Суурь дүгнэлт

2026-10-21-ний documented run: 16/16 synthetic DOCX parse амжилттай (100%); parser revision `ordered-blocks-v1`; median 913.510 ms, nearest-rank P95 1120.350 ms, throughput 1.079 file/sec / 0.038 MiB/sec. Хоёр нэмэлт давталтын үр дүнгээс P95 мэдэгдэхүйц хэлбэлзсэн тул тайланд хадгалсан энэ нэг run-ийг production performance estimate гэж үзэхгүй. Машин унших тайлан `evaluation/load/reports/`-д байна.

Энэ хэмжилт DOCX parser subprocess overhead-ийг багтаана; upload HTTP multipart болон storage/DB биш. Dataset зөвхөн ~37 KiB орчим synthetic DOCX-уудтай. PDF болон DOC benchmark case байхгүй; CER, clause/table accuracy ground truth байхгүй; peak memory болон concurrency хэмжээгүй. 16 файлтай жижиг sample дээр P95-ийн хэлбэлзэл өндөр байна. Тиймээс 100% гэдэг нь энэ synthetic багц дээр parse алдаа гараагүй гэсэн утгатай бөгөөд extraction correctness эсвэл Load stage бүрэн найдвартай гэсэн баталгаа биш.

Relevant tests: **49 passed, 22 skipped**. Нэг native OCR test Tesseract байхгүйгээс; 21 lifecycle integration test `TEST_DATABASE_ADMIN_URL` тохиргоо байхгүйгээс skip болсон. Edge-case parsing/benchmark ба duplicate upload шалгалтууд pass. `git diff --check` whitespace алдаагүй; зөвхөн Windows line-ending анхааруулга гарсан.

## Сайжруулах боломжууд — одоогоор хэрэгжүүлэхгүй

| Санал | Хүлээгдэж буй ашиг | Complexity | Гүйцэтгэлийн нөлөө | Эрсдэл |
|---|---|---|---|---|
| Гараар баталгаажуулсан PDF/DOCX/DOC corpus ба text/table/layout ground truth | Extraction completeness болон CER-ийг бодитоор хэмжинэ | Бага–дунд | Benchmark нэмэлт зардал; runtime өөрчлөлтгүй | Эх баримт бэлтгэх/нууцлал |
| PDF layout/table parser-ийн харьцуулалт | Хүснэгт, багана, Mongolian reading order дээр сайжрах боломж | Дунд–өндөр | CPU/RAM нэмэгдэж магадгүй | Citation page/block provenance өөрчлөгдөх |
| Image-only DOCX-д OCR | Одоо extraction-д орхигдох зурагт текст | Дунд | Хугацаа болон CPU өснө | OCR алдаа/хувийн мэдээлэл |
| Legacy DOC хөрвүүлэгчийн тусгаар worker/container, resource quota | Converter fault isolation сайжруулна | Дунд | Start-up болон disk нэмэгдэнэ | OS/LibreOffice operations |
| Load concurrency benchmark ба queue | Throughput, P95, saturation харагдана | Дунд | Туршилтын орчинд өндөр ачаалал | Одоогийн синхрон request worker-ийг дүүргэх |

**Энэ үеийн дараагийн ганц туршилт:** бодит байгууллагын бус, гараар баталгаажуулсан Mongolian PDF/DOCX жижиг corpus (native text, scan, mixed page, table, heading/clause)-ийг manifest-д оруулаад reference text/structure-тай benchmark ажиллуулах. Одоогийн synthetic DOCX baseline амжилт, latency-г хэмжиж болох ч accuracy/CER нотолж чадахгүй; тиймээс parser солих эсвэл OCR/layout тохируулахын өмнө энэ нь хамгийн их үнэ цэнтэй.
