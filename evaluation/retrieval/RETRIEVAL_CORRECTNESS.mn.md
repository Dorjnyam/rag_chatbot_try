# Retrieval correctness — offline verification

## Хамрах хүрээ

Known-answer unit/integration tests нь BM25 томьёо, RRF, active version/provenance, exact query identifier хамгаалалтыг шалгав. Эдгээр нь ranking plumbing correctness; бодит хэрэглэгчийн relevance-ийн хэмжилт биш.

## Үр дүн

- BM25-ийн 2 баримттай controlled corpus дээр Robertson IDF, term frequency, document length-ийн score гараар бодсон утгатай таарсан.
- RRF-ийн `c={10,30,60,100}` grid-ийн score болон tie-break тогтвортой; нэг branch доторх давхардсан ID нэг л удаа rank contribution өгсөн.
- Cross-branch ижил ID-ийн текст/metadata зөрөхөд fail-closed болсон.
- Manifest-д байхгүй ID, буруу version/document ownership, incomplete manifest, metadata mismatch, content hash mismatch candidate-г хориглосон.
- Sparse candidate-ууд RRF-д орохоос өмнө active version болон source manifest-тай тулгагдана.
- Exact policy identifier, date, clause, Mongolian negation normalization fixture-ийг хадгалсан.
- Сүүлийн targeted regression: **52 passed, 0 failed**.

## Хязгаар

Mocked candidate болон жижиг synthetic fixtures нь DB concurrency, stale index бүх хэлбэр, Mongolian semantic relevance-г нотлохгүй. Relevance Recall@k, MRR, nDCG, citation accuracy одоогоор N/A.
