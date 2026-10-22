# Retrieval ground-truth readiness

## Одоогийн үнэлгээний багц

Private exported dataset `gt-v1`-д 2 explicitly human-confirmed question, 6 verified language variation байна. Six variations are correlated forms of two questions, not six independent relevance judgments. No retrieval metric is computed from the current set.

Existing 80 annotation candidates contain 70 draft, 8 rejected and 2 corrected records. Seven have a non-empty query type, but those do not make a representative approved set. Candidate question/evidence are proposed references; the reviewer must inspect original source and verify both question–evidence relation and answer. None is automatically approved here.

## Review queue бэлтгэл

Private queue: `instance/retrieval-evaluation/annotation-queue-round1.json` (16 draft candidates across 16 pseudo-document IDs). Selection is reproducible: first draft candidate per document in sorted document order. It is a review convenience sample only; it is **not** stratified by observed retrieval failures because no relevance benchmark exists and no failure categories are yet measured. All 16 query types remain explicitly unclassified for reviewer decision. The queue approves zero records.

Reviewer-ийн ажлын дараалал:

1. `flask annotation-review` local UI-д candidate ID-ээр нээж эх DOC/DOCX/PDF preview-г шалгана.
2. Асуултыг ойлгомжтой Монгол хэлээр өөрийн үгээр засаж болно; English/Latin variations-ийг зөв эсэхийг тус тус шалгана.
3. Эх дээр харагдаж байгаа evidence span ба page/section/clause anchor-ийг батална; мэдэгдэхгүй anchor-ийг null хэвээр үлдээнэ.
4. Expected answer нь evidence-ээс шууд хариулагдах эсэхийг батална; query type-ийг reviewer сонгож өгнө.
5. Reviewer ID, evidence match, question–evidence relation, хэлний variation confirmation-уудыг өөрөө тэмдэглэж approve хийнэ.
6. Readiness command-оор validation failure/coverage-г шалгаж, шинээр versioned export гаргана.

## Retrieval evaluation gate

Dataset-ийн SHA/version, document coverage, intended query-type төлөөлөл, хэлний variation, canonical evidence chunk холбоос, reviewer confirmation болон validation error бүгд шалгагдаж байж retrieval evaluation нээгдэнэ. Зөвхөн record count хүрсэн гэх шалтгаанаар READY гэж дүгнэхгүй. Approved evidence-г public report-д хуулж болохгүй.
