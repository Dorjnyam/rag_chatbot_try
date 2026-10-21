# Ground-truth annotation review багц

## Хамрах хүрээ

- Frozen Load corpus SHA-256: `15b24382aeba13e943382370f5a4bfb4ede50b4033805006bad692d23bf3d56a`.
- 20 representative documents (4 DOC, 16 DOCX), 80 draft candidate questions, 240 Cyrillic/English/Mongolian Latin query variations.
- Candidate evidence болон expected answer автоматаар санал болгосон; verified ground truth биш.

## Private review workflow

Private package: `instance/split-evaluation/annotation-review/`. Start:

```powershell
.\.venv\Scripts\flask.exe --app app annotation-review serve --port 8765
```

Browser дээр `http://127.0.0.1:8765/annotation-review/` нээнэ. DOC/DOCX эхийг app дотор PDF preview хэлбэрээр харна; PDF шууд preview болно. Word preview-д LibreOffice тохиргоо шаардлагатай. `Татаж авах` нь fallback хэвээр. Эх файл болон frozen snapshot өөрчлөгдөхгүй; хөрвүүлсэн preview зөвхөн private cache-д хадгалагдана.

## Review form-ийг бөглөх жишээ

Жишээ эхийн өгүүлбэр: “Ажилтан ээлжийн амралт авах хүсэлтээ ажлын 5 өдрийн өмнө гаргана.”

- Монгол Cyrillic асуулт: “Амралтын хүсэлтийг хэд хоногийн өмнө гаргах вэ?”; English/Latin талбарууд нь ижил асуултын өөр хэлний хувилбар.
- 3 хэлний checkbox тус бүр тухайн хувилбар зөв, ижил утгатайг reviewer батлахад хэрэглэнэ. Evidence checkbox-той холбоогүй. Approve-д Cyrillic заавал, English/Latin-аас дор хаяж нэг нь заавал verified байх ёстой.
- Query type нь асуултын зорилго: `requirement`=шаардлага, `exception`=үл хамаарах нөхцөл, `procedure`=алхам, `table_fact`=хүснэгтийн мэдээлэл, `definition`=нэр томьёоны утга. Жишээ нь дээрх асуулт `requirement`.
- Evidence text-д эхэд байгаа нотлох хэсгийг яг хуулна. Evidence-ийн эхний checkbox нь эхтэй яг тулгасныг, хоёр дахь нь асуултад энэ хэсгээс хариулж болохыг тус тус батална.
- Expected answer-д богино хариу бичнэ: “Ажлын 5 өдрийн өмнө.”
- Source reference menu-гээс `Section heading` зэрэг эх дээр бодитоор харагдах anchor сонгож, detail-д жишээ нь `Ээлжийн амралт` гэж бичнэ. Page, clause ID, version талбаруудыг эхэд харагдахгүй бол хоосон орхино. Байршил тогтоох боломжгүй бол menu/detail ч хоосон байж болно.
- Reviewer ID бол review хийсэн хүнийг үргэлж ижил таних код, жишээ `reviewer-01` эсвэл initials. Нууц үг болон имэйл шаардлагагүй. Сүүлийн confirmation нь өөрөө эхийг нягталж, тухайн approve/reject үйлдлийг баталж байгааг бүртгэнэ.

`Save progress` хадгална; `Skip for later` төлөвийг skip болгоно; `Reject candidate` шалтгаантайгаар татгалзана; `Approve verified annotation` бүх шалгалт, explicit confirmation амжилттай үед л батална.

Approve хийхэд reviewer ID, үндсэн Cyrillic variation ба сонгосон query variations-ийн human check, evidence-г эхтэй тулгасан баталгаа, question–evidence холбоог бие даан баталсан check, болон тусдаа explicit confirmation заавал шаардлагатай. Буруу candidate-г reject хийхэд reviewer ID, шалтгаан, confirmation шаардлагатай. Draft, corrected, approved, rejected, skipped төлөв ялгаатай хадгалагдана.

- `candidate-review.json`: review эхлэх үеийн draft package; application үүнийг өөрчлөхгүй.
- `review-state.json`: засвар, status, reviewer confirmation хадгалдаг Git-ignored resumable state.
- `ground_truth_review_template.json` болон `review.html`: өмнөх read-only/static reference; одоогийн ажлын source of truth биш.
- `/source/<candidate_id>` эх файлыг upload directory дотор байгааг болон source SHA-256-г шалгаад өгнө. Path, evidence текст export-оос бусад public report-д орохгүй.

## Readiness шалгуур

Run:

```powershell
.\.venv\Scripts\flask.exe --app app annotation-review status
```

Retrieval gate нь зөвхөн approved тоонд тулгуурлахгүй. Source identity/hash хүчинтэй, evidence frozen extraction-д байгаа эсвэл human-confirmed source anchor-тай, question/evidence relationship батлагдсан, explicit human confirmation бүрэн, мөн Cyrillic/English/Latin хэл тус бүр ба `requirement`, `exception`, `procedure`, `table_fact`, `definition` query type бүрт төлөөлөх verified жишээ шаарддаг. Page, clause ID, version-ийг зөвхөн эхээс reviewer баталсан үед бөглөнө; бусдыг null үлдээнэ.

## Export

```powershell
.\.venv\Scripts\flask.exe --app app annotation-review export --version gt-v1
```

Export `instance/split-evaluation/datasets/ground-truth-gt-v1.json`-д хадгалагдана. Version давхардахгүй, хуучин export overwrite хийхгүй. Export hash, reviewer-confirmed status, approved question болон verified query variation counts, source reference, validation failures, retrieval readiness-г агуулна. Export нь зөвхөн `review-state.json` дэх human-confirmed approved/corrected бичлэгүүдийг авна.

Эх package үүсгэх комманд дахин ажиллуулах бол одоо байгаа annotation package эсвэл review state-ийг overwrite хийхээс хамгаалсан алдаа буцаана. Энэ нь хүний progress болон source identity-г хадгална.
