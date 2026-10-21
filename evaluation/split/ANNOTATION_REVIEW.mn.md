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

Browser дээр `http://127.0.0.1:8765/annotation-review/` нээнэ. Candidate бүр дээр DOC/DOCX эхийг татаж харах, асуултын хувилбаруудыг засах/батлах, exact evidence болон expected answer засах, query type ангилах, source reference/page/section/clause/version бөглөх боломжтой. `Save progress`, `Skip for later`, `Reject candidate`, `Approve verified annotation` үйлдлүүд progress-г шууд private state-д хадгалж, дараа үргэлжлүүлнэ.

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
