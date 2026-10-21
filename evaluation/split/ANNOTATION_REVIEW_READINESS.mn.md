# Annotation review package readiness

Шалгасан private package: `instance/split-evaluation/annotation-review/`.

| Шалгалт | Үр дүн |
|---|---|
| Төлөөлөх эх DOC/DOCX | 20; source link 20/20 нь одоо байгаа файл руу зааж байна |
| Асуулт / query variants | 80 candidate; Cyrillic, English, Mongolian Latin draft тус бүрээр нийт 240 variation |
| Эх баримт, асуулт, proposed evidence/answer, parser-local location | `review.html` дээр харагдана |
| Page / verified clause / original source span / version | Боломжгүй бол null; parser block ID нь эх баримтын баталгаатай координат биш |
| Review state | Бүгд unreviewed; human-verified 0 |
| Өмнөх `review.html` | Read-only static preview; form controls байхгүй |
| Шинэ review UI | Flask `/annotation-review/`, form save/resume, per-question status, PDF inline preview, DOC/DOCX→PDF private preview cache, download fallback; зөвхөн `127.0.0.1` дээр serve хийнэ |

## Review-ийн анхаарах зүйл

Өмнөх static HTML нь `file:///` холбоос ашигладаг. Шинэ local UI нь document холбоосыг server-side allowlist ба SHA-256 шалгалтаар өгнө. PDF browser дотор inline нээгдэнэ; DOC/DOCX LibreOffice боломжтой үед PDF болгон хувиргаж private cache-д үзүүлнэ. Preview бэлтгэгдэхгүй бол эх файлыг татаж авах fallback холбоос байна. Энэ хөрвүүлэлт эх баримтыг өөрчлөхгүй.

Form-ийн тайлбар ба жишээ `/annotation-review/` хуудсанд бий. Гурван хэлний checkbox нь тус бүрийн query variation зөвийг шалгана; evidence-ийн хоёр checkbox нь эхтэй таарсан эсэх, асуулт тухайн evidence-ээс хариулагдах эсэхийг тусад нь батална. Query type нь асуултын зорилго. Source reference-д anchor-ийн төрлийг menu-гээс сонгож, эхэд харагдах label/detail-ийг бичнэ; байхгүй page/clause/version-ийг хоосон үлдээнэ. Reviewer ID нь reviewer-ийн тогтвортой нэр/код бөгөөд confirmation checkbox нь тухайн approve/reject үйлдлийг өөрөө баталсныг тэмдэглэнэ.

Candidate evidence/query нь автоматаар санал болгосон тул эх сурвалжтай таарч байгаа эсэх, expected answer зөв эсэх, асуулт хариулагдахуйц эсэхийг reviewer бүр шалгана. Шинэ UI save-progress, approve, correct, reject, skip болон resume-г дэмжинэ; approve хийхэд per-language confirmation, source/evidence check, query–evidence relationship check, reviewer ID болон explicit human confirmation шаарддаг. `candidate-review.json` immutable starting package, харин `review-state.json` private resumable state. Үүнийг retrieval ground truth гэж ашиглахын өмнө readiness CLI нь source hash, extraction evidence, query-type representativeness болон variation coverage шалгана.

Retrieval readiness зөвхөн тоон count-оор нээгдэхгүй: human-verified Cyrillic/English/Latin жишээ болон requirement, exception, procedure, table fact, definition гэсэн query type бүрийн approved жишээ шаарддаг. Page/clause/version талбарыг null хэвээр үлдээж, зөвхөн source дээр reviewer баталсан үед л бүртгэнэ.

Нууц баримтын нэр, текст, path, асуулт болон evidence-г энэ tracked report-д оруулаагүй. Review JSON/HTML болон frozen snapshot `instance/` доор private бөгөөд commit хийх зориулалтгүй.
