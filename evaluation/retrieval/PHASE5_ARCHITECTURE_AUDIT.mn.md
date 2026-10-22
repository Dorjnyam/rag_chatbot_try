# Phase 5 — Retrieval architecture audit

## Өгөгдлийн урсгал

1. `active_versions()` зөвхөн active document-ийн ready version-уудыг сонгоно.
2. `retrieve()` embedding fingerprint compatibility-г шалгана.
3. Dense query variants Chroma-д version filter-тэй очно; BM25 version-scoped JSON artifact-уудыг шалгана.
4. Хоёр branch-ийн candidate бүр canonical manifest болон content hash-тай тулгагдана.
5. Хүчинтэй ranking-ууд `RRF_K=60`-аар нийлнэ; `rag.build_context()` token/context limit-ээр эцсийн evidence-г сонгоно.
6. `rag.ask()` generation өмнө active-version status-ийг дахин баталгаажуулна.

## Баталгаажсан хамгаалалт

- Ижил canonical chunk ID нь branch хооронд өөр text эсвэл metadata-тай ирвэл fail closed.
- Version/document ownership, expected chunk count, canonical metadata, text content hash алдаатай бол candidate-г context руу оруулахгүй.
- BM25 missing/corrupt үед degraded төлөв бүртгэж dense-only үргэлжлүүлэх одоогийн fallback тесттэй.
- Embedding dimensions ижил байсан ч embedding space/config identity зөрвөл collection-уудыг холихгүй (Phase 3 compatibility contract).
- Query normalization нь original query-г хадгалж, exact identifier/date/clause, `bish`/`ugui` зэрэг negation token-ийг хадгалах тесттэй.
- RRF-ийн scoring нь rank-only; BM25 `sparse_score` ба cosine `distance` нь нийлбэрт орохгүй.

## Зассан defect

RRF өмнө duplicate chunk-уудын dict-ийг дарааллаар `update()` хийж, дараагийн branch-ийн `distance` эсвэл `sparse_score`-г өмнөх утгаар дарж байв. Hybrid UI нь RRF нийлмэл жагсаалтад нэг raw cosine `similarity` байгаа мэт харуулж болох байлаа. Одоо branch тус бүрийн `branch_scores` ба `branch_distances` хадгалж, fused record-ийн global `distance`, `similarity`, `sparse_score`-г `None` болгож байна. Энэ нь онооны утгыг буруу тайлбарлахаас сэргийлнэ; RRF эрэмбэ өөрчлөхгүй.

## Баримтжуулсан үлдэгдэл

- Route ба service top-k whitelist ижил биш. Одоогийн route-н бүх утга service-д хүчинтэй; нийтэд зарласан API contract гэж үзэх нотолгоо алга.
- Sparse search нь version artifact/content hash-г шалгадаг тул cache miss/том корпус дээр I/O ба hash хийх зардал өсөж болно. Энэ offline run-д хэмжигдсэн хугацааг хар; optimization нь тусдаа profile ба regression-ийн дараа.
- 2 verified question нь query category coverage, confidence threshold, retrieval superiority сонгоход хүрэлцэхгүй.
- LLM query rewrite, decomposition, шинэ reranker, SPLADE болон шинэ embedding model энэ ажлын хүрээнд deferred хэвээр.
