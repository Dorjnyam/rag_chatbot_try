# Annotation review package readiness

Шалгасан private package: `instance/split-evaluation/annotation-review/`.

| Шалгалт | Үр дүн |
|---|---|
| Төлөөлөх эх DOC/DOCX | 20; source link 20/20 нь одоо байгаа файл руу зааж байна |
| Асуулт / query variants | 80 candidate; Cyrillic, English, Mongolian Latin draft тус бүрээр нийт 240 variation |
| Эх баримт, асуулт, proposed evidence/answer, parser-local location | `review.html` дээр харагдана |
| Page / verified clause / original source span / version | Боломжгүй бол null; parser block ID нь эх баримтын баталгаатай координат биш |
| Review state | Бүгд unreviewed; human-verified 0 |
| Review интерфейс | Read-only HTML, editable JSON нь `candidate-review.json`; form controls байхгүй |

## Review-ийн анхаарах зүйл

HTML нь эх файлыг `file:///` холбоосоор нээдэг. Ижил Windows machine дээр файл тус бүр нээгдэхийг шалгав; browser policy өөр орчинд `file://` navigation-г хориглож болох тул эх JSON дахь `source_file` абсолют path-ийг Explorer/Word-оор нээх fallback болгоно. `candidate-review.json`-г засах, draft query/evidence-г эхтэй тулгаж accept/rewrite/reject хийх, reviewer/date тэмдэглэх workflow README-д байна.

Candidate evidence/query нь автоматаар санал болгосон тул эх сурвалжтай таарч байгаа эсэх, expected answer зөв эсэх, асуулт хариулагдахуйц эсэхийг reviewer бүр шалгана. Үүнийг retrieval ground truth гэж одоо ашиглаж болохгүй. Зөвхөн шалгасан candidate-д `human_verified=true` тавина; metadata-г эхээс батлахгүй бол null хэвээр үлдээнэ.

Нууц баримтын нэр, текст, path, асуулт болон evidence-г энэ tracked report-д оруулаагүй. Review JSON/HTML болон frozen snapshot `instance/` доор private бөгөөд commit хийх зориулалтгүй.
