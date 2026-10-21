# Ground-truth annotation review багц

## Хамрах хүрээ

- Frozen Load corpus SHA-256: `15b24382aeba13e943382370f5a4bfb4ede50b4033805006bad692d23bf3d56a`.
- Одоо байгаа representative template-ээс 20 баримт: 4 DOC, 16 DOCX; parser-ийн clause marker, table block, урт block, heading/block complexity-ийн эвристик дохиогоор төлөөлүүлсэн.
- 80 candidate question, нэг асуултад Mongolian Cyrillic, English, Mongolian Latin-transliterated 3 draft variation (нийт 240 query text).
- Бүх асуулт, evidence, expected-answer draft **unreviewed**. Ground-truth гэж ашиглаж болохгүй.

## Private review package

`instance/split-evaluation/annotation-review/` нь Git-ээс үл хамаарах private folder.

- `review.html`: асуулт, хэлний variant, proposed evidence/answer, parser-local source location болон original file холбоостой read-only review page.
- `candidate-review.json`: бүх draft candidate болон review талбарууд; editable source.
- `ground_truth_review_template.json`: хүн баталгаажуулсан reference-ийг тусад нь тэмдэглэх template.
- `README.mn.md`: алхамчилсан workflow.

Эх файлын path/hash болон extracted passage нь confidential учир public report-д хуулбарлаагүй. `page`, verified `clause_id`, original source span болон document version байхгүй үед null хэвээр үлдсэн. Парсерын block index нь зөвхөн parser-local reference; original-page location биш. Query хувилбарууд автоматаар үүссэн тул Монгол Latin transliteration болон English wording-ийг reviewer эхлээд засаж/батална.

Nested-list эвристик detection нь corpus даяар нэг баримтад л тодорхой nested marker илрүүлсэн; энэ нь бодит nested list байхгүй гэсэн баталгаа биш, учир нь parser list nesting metadata хадгалдаггүй. Сонгосон 20-д илэрсэн nested-list баримт багтсан. Exception/үл хамаарах нөхцөлийн keyword эвристик сонгосон 20-ын 9-д, corpus-ийн 151 parsed document-ийн 66-д дохио олсон; энэ нь semantically complete exception coverage гэсэн үг биш. Хүн review хийхдээ энэ хязгаарлалтыг харгалзана.

## Review-ийн шалгуур

1. Эх DOC/DOCX-ийг HTML холбоосоор нээж, candidate evidence-г визуал эхтэй үг, дараалал, хүснэгтийн контекст хүртэл тулгана.
2. Асуулт эх сурвалжаас хариулагдах эсэхийг шалгаж, Cyrillic/English/Latin хувилбар бүрийг засах эсвэл хасна.
3. Exact evidence text болон богино expected answer-ийг reviewer өөрөө батална; одоогийн expected answer нь parser block-ийн copy тул verified answer биш.
4. Эх дээр шууд батлагдсан page/clause/section/source location-ийг л бичнэ. Батлагдаагүй талбарыг null байлгана.
5. Reviewer, огноо, шалгалтын тэмдэглэл бөглөсний дараа `human_verified=true` болгоно. Зөвхөн fully reviewed candidate-уудыг retrieval eval-д оруулна.

## Retrieval gate

Одоогоор `human_verified=0`; A0–A3 offline chunk output-уудыг retrieval quality-гаар эрэмбэлэхгүй. 80 candidate-ийг хүн шалгасны дараа ижил query split, embedding model/config, isolated index болон retrieval тохиргоогоор evidence Recall@k, MRR, nDCG, evidence-span coverage, latency ба token cost хэмжинэ. Эхлэхээс өмнө embedding input-token usage/cost estimate гаргана.

Recreate:

```powershell
.\.venv\Scripts\flask.exe --app app benchmark-split prepare-annotations --documents 20 --questions-per-document 4
```
