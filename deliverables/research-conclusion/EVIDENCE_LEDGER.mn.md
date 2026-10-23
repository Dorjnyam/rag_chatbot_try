# RAG судалгааны нотолгооны бүртгэл

Огноо: 2026-10-22. HEAD: `88150218bc18c2ddaae5ad880eb79af8b27c2558`.

HEAD нь ганцаараа эх сурвалжийн бүрэн snapshot биш. Phase 6 application/tests болон evaluation/generation нь эхлэх үед modified/untracked байсан. Доорх source hashes нь уншсан working tree хувилбарыг тогтооно. Шинэ benchmark, API, indexing ажиллуулаагүй.

Scope: зөвхөн aggregate technical measurements, sanitized run ID болон repository-relative report/code paths. Private source filename, text, chat, vector payload, credential агуулаагүй.

## Ангилал

MEASURED — бодит туршилтын хэмжилт. VERIFIED — test/explicit check. PROVISIONAL — quality evidence дутуу baseline. DEFERRED — хойшлуулсан. NOT VERIFIED — хангалттай баталгаа байхгүй.

## E01 MEASURED

- Report: `evaluation/load/reports/real-load-20261021T042112Z.md`
- Experiment: `e948b4c9-c855-42fe-b939-d635364b9a2c`
- Evidence: 154 input, 151 success, 3 failed; P50 973.52 ms, P95 3869.25 ms; accuracy N/A
- Source SHA-256: `8c4697d29a973c87e4bfe2998b17730c9cd601354854cc04fa4905c2152e0953`

## E02 VERIFIED

- Report: `evaluation/load/PHASE1_RESEARCH_GAP_ANALYSIS.mn.md`
- Experiment: `Phase 1 interface audit`
- Evidence: Automatic numbering, heading level and merged-cell graph missing; real PDF/OCR quality unverified
- Source SHA-256: `bf26c5b716300bf6788a77730b2e7ace894a1e03a312c9c729260e8b5d1b7fb6`

## E03 MEASURED

- Report: `evaluation/split/PHASE2_FINAL_STATUS.mn.md`
- Experiment: `A0 1dc80917-a602-4185-9c3a-f16cae209b21; A1–A3 ecf27e9c-7558-41b5-ae09-c145937dfa7c`
- Evidence: A0/A1/A2/A3 chunks 2468/2244/4946/6116; A0 provisional
- Source SHA-256: `4732e08b31ecad5e905fb8ba5e53ad7c60d7b4acb6e048964bb0077e8e49e6a7`

## E04 MEASURED

- Report: `evaluation/split/OVERLAP_ABLATION.mn.md`
- Experiment: `95a117e7-e306-43f1-bc34-94d0c9ee217b`
- Evidence: 512-token fixed/recursive overlap 0/51/77/128; estimated input tokens, not billed usage
- Source SHA-256: `dd6808731b7df862d4ea6ffdee08f57ed718d135ea9c75282f36676269105756`

## E05 MEASURED

- Report: `evaluation/embed/PHASE3_RESULTS.mn.md`
- Experiment: `d2031800b3ec; 5cad4b7d3288`
- Evidence: 2667761 tokens; max 1730; mock 8/16/32 batch results; no model-quality winner
- Source SHA-256: `4cc80965612f784a5c5e097db128f1da40e6e0f1bed5962fa6c8f68dfa1bfe5e`

## E06 VERIFIED

- Report: `evaluation/integration/PHASE35_INTEGRATION_GATE.mn.md`
- Experiment: `Phase 3.5 final regression`
- Evidence: 169 passed, 1 skipped; activation failure/orphan recovery tested in isolated PostgreSQL
- Source SHA-256: `a2ffdd3b9e38f0b346a209c2aa2819e951bb6981d78da25fa3da07475d5df2db`

## E07 MEASURED

- Report: `evaluation/store/RESULTS.mn.md`
- Experiment: `d11ef3d2a1e0; closure 77c67916119e`
- Evidence: Synthetic scale; historical R@10 .394 at N=10000, ef=100; RSS outlier unresolved
- Source SHA-256: `667fd7979cbbf8e294088874f10f14ff237fe7a6b73894b03754803c191d7e5f`

## E08 MEASURED

- Report: `evaluation/store/ANN_FIDELITY_INVESTIGATION.mn.md`
- Experiment: `99525c3dbb9a`
- Evidence: N=10000 d=1536, 3 builds x 32 queries; ef=50/100/200/400; unfiltered R@10 .260/.401/.556/.786
- Source SHA-256: `45061654c4aa0dfa9df7f32517c89e2f4bda156757535ec535caadf3b9ded3ce`

## E09 MEASURED

- Report: `evaluation/retrieval/PERFORMANCE_RESULTS.mn.md`
- Experiment: `424b37001c92`
- Evidence: Historical BM25 P50/P95 649.30/705.74 ms; total 704.23/782.34 ms
- Source SHA-256: `21cf70bfb9f074ea07c102191ac2dc617114299cfb3a9aa0cbb56455b8c7aa06`

## E10 MEASURED

- Report: `evaluation/retrieval/BM25_PERFORMANCE_INVESTIGATION.mn.md`
- Experiment: `a50bcc9413da; final R6 670e5417c260`
- Evidence: Final R6 BM25 P50/P95 214.60/243.39 ms; total 270.96/322.05 ms; rank parity 30/30
- Source SHA-256: `16afd124808b62bb4e6e58f3ba2f0c3d95d51f4f03e7c72c2cd6794a50617b28`

## E11 VERIFIED

- Report: `evaluation/retrieval/PHASE5_ARCHITECTURE_AUDIT.mn.md`
- Experiment: `Phase 5 RRF/provenance audit`
- Evidence: Branch scores retained; fused global similarity/distance cleared; degraded dense fallback
- Source SHA-256: `085d351ca5b7e1d58cb6c9ca0918a965fd8756063ee7ccf1449ff195afe79e7d`

## E12 MEASURED

- Report: `evaluation/generation/RESULTS.mn.md`
- Experiment: `6e04a8856e0b`
- Evidence: 90 fake timed samples validated; offline total P50/P95 2.245/9.978 ms; full historical suite 213 passed, 1 skipped
- Source SHA-256: `a97fed341f2f4730c394c3af4e52a9fab8ca09f7306f385fcdb536512e1e7a3b`

## E13 VERIFIED

- Report: `evaluation/generation/CHAPTER9_TRACEABILITY.mn.md`
- Experiment: `G1–G8 / Chapter 9`
- Evidence: Immutable selected references; schema/citation/budget guards; claim entailment unverified
- Source SHA-256: `9275bb1e9186773ed92e56659b1493bda3d12daca2403ffc042919215cd906be`

## E14 DEFERRED

- Report: `evaluation/generation/CHAPTER8_10_MAPPING.mn.md`
- Experiment: `Chapter 8/10 supporting mapping`
- Evidence: Advanced context strategies and human answer evaluation remain deferred
- Source SHA-256: `62fdea1f7cffc659614a9607d34829b6c9426348925d288ddada88c73db2d613`

## E15 NOT VERIFIED

- Report: `evaluation/embed/OPERATIONAL_READINESS.mn.md`
- Experiment: `Phase 3 readiness historical snapshot`
- Evidence: Earlier PostgreSQL skips later addressed by E06; internal 135 versus 136 pass discrepancy
- Source SHA-256: `340ba944b6a40d44bc76a01c183fac956935788e9e6708eda811dcc424ba5374`

## Зөрүү ба хэрэглэсэн шийдвэр

- E03 recursive-128 table-cut=4043; E04 detailed table-cut=3840, sentence=4043. Энэ metric-ийг chart/decision-д ашиглаагүй.
- E09 historical BM25/total P99=705.89/806.48; E10 baseline P99=712.61/793.95. P50/P95 тохирсон тул chart зөвхөн тэдгээрийг ашигласан.
- E15 135/136 pass дотоод зөрүүтэй; E06 130/136 өмнөх revision ялгааг тайлбарласан. Suite count-ийг paired benchmark болгоогүй.
- Early overlap snapshot 0 verified; final 2 verified independent question. Өөр хугацааны snapshot гэж ялгасан.
- E12 advanced methods хэрэгжүүлээгүй гэсэн wording-ийг current optional reranker hook/prototype-оос ялгасан. Quality gain батлагдаагүй.
- E07 R@10=.394 ба E08 .401 нь өөр query/repeat run; нэгтгээгүй. RSS outlier unresolved.

## Artifact provenance

### Private result integrity шалгалт

- ANN run `99525c3dbb9a`: report дахь `76660df0d1f4283aed17777f1247b9d0abea29a4a2bf019146ebe220f0bdae4c` нь `report_sha256` талбаргүй payload-ийг sorted-key compact JSON болгон SHA-256 тооцсон утга. `app/store_benchmark.py::_sha_json` ба report writer-ийн дүрмээр дахин тооцоход таарсан. Энэ нь бүх file bytes-ийн hash биш.
- Тухайн ANN result файлын raw-byte SHA-256: `5c68e0a75ded623b6a24977c481eeb14a7df1662d4b222d8394920acf6d94e80`. Chart-ийн Recall@10 болон P95-ийн 16 утга JSON aggregate-тай displayed precision-д таарсан.
- BM25 focused run `a50bcc9413da` ба Generation run `6e04a8856e0b`-ийн raw result hash нь тайланд нийтэлсэн hash-тай таарсан. Шинэ хэмжилт ажиллуулаагүй.
- DOCX: 14 rendered page. PPTX: 15 slide, 4 native chart, 2 native table, 4 embedded workbook. Chart cache/workbook values болон report numbers тулгалт pass. Private source content/filename, absolute source path, credential pattern илрээгүй. Энэ pattern check нь харааны болон эх сурвалжийн review-г орлохгүй.

Aggregate snapshot SHA-256: `7b4d24a6a8585ceaed886ed30a477ed4249f8532170c3b6e6e81606ad20e4090`

## Уншсан evidence болон тулгасан кодын hashes

- `evaluation/embed/CHAPTER5_TRACEABILITY.mn.md` — `f51afb9c5d06370f08b17e82863725b542c1bfc3df85c2b6a26d5ff221f1e059`
- `evaluation/embed/OPERATIONAL_READINESS.mn.md` — `340ba944b6a40d44bc76a01c183fac956935788e9e6708eda811dcc424ba5374`
- `evaluation/embed/PHASE3_INTEGRATION_AUDIT.mn.md` — `30e781921a32ca168798251619fe721955cf0b53144503b4cf60f241849661b8`
- `evaluation/embed/PHASE3_READINESS.mn.md` — `c2691ba2fb1b0fa8b6a6fa0de146e1cab1244e93b390a9ad3579cb7ca3106ebd`
- `evaluation/embed/PHASE3_RESULTS.mn.md` — `4cc80965612f784a5c5e097db128f1da40e6e0f1bed5962fa6c8f68dfa1bfe5e`
- `evaluation/generation/BASELINE.mn.md` — `286e16418adbe1c5d609ee1fd237a3dc6c9d9860c951841720ab599ca31a601a`
- `evaluation/generation/CHAPTER8_10_MAPPING.mn.md` — `62fdea1f7cffc659614a9607d34829b6c9426348925d288ddada88c73db2d613`
- `evaluation/generation/CHAPTER9_TRACEABILITY.mn.md` — `9275bb1e9186773ed92e56659b1493bda3d12daca2403ffc042919215cd906be`
- `evaluation/generation/RESULTS.mn.md` — `a97fed341f2f4730c394c3af4e52a9fab8ca09f7306f385fcdb536512e1e7a3b`
- `evaluation/integration/PHASE35_INTEGRATION_GATE.mn.md` — `a2ffdd3b9e38f0b346a209c2aa2819e951bb6981d78da25fa3da07475d5df2db`
- `evaluation/load/AUDIT.mn.md` — `acf7c4f66a84617678a77fc527d6fd07858866a56aebaf0f54ca034e3fd8756d`
- `evaluation/load/DEBUG_ARTIFACTS.md` — `55d5f994a92bccd5d1cf9a9eae672dbfa0772c0059e9340185656b54a366870e`
- `evaluation/load/PHASE1_RESEARCH_GAP_ANALYSIS.mn.md` — `bf26c5b716300bf6788a77730b2e7ace894a1e03a312c9c729260e8b5d1b7fb6`
- `evaluation/load/README.mn.md` — `315afed9c30661e33f16fb791bda58b1dc4eb2b0a5fadc23590eb5d100108114`
- `evaluation/load/reports/load-20261021T040121Z.md` — `f5016a4feb9af96bf65972959aff47e28736b8cc0a8a443ff5fdbff3850c431b`
- `evaluation/load/reports/real-load-20261021T042112Z.md` — `8c4697d29a973c87e4bfe2998b17730c9cd601354854cc04fa4905c2152e0953`
- `evaluation/retrieval/BM25_PERFORMANCE_INVESTIGATION.mn.md` — `16afd124808b62bb4e6e58f3ba2f0c3d95d51f4f03e7c72c2cd6794a50617b28`
- `evaluation/retrieval/CHAPTER7_TRACEABILITY.mn.md` — `4cfd988e7677dd48c8321898376cddf2ef3eb86547bfc7e2ea5f64ea4b71e940`
- `evaluation/retrieval/GROUND_TRUTH_READINESS.mn.md` — `f3571d1507c8a6a4ad4cb8f5ebd93e9f7ed86c1a79218ccfb583ce2ed53db7d1`
- `evaluation/retrieval/PERFORMANCE_RESULTS.mn.md` — `21cf70bfb9f074ea07c102191ac2dc617114299cfb3a9aa0cbb56455b8c7aa06`
- `evaluation/retrieval/PHASE5_ARCHITECTURE_AUDIT.mn.md` — `085d351ca5b7e1d58cb6c9ca0918a965fd8756063ee7ccf1449ff195afe79e7d`
- `evaluation/retrieval/PHASE5_READINESS.mn.md` — `6c02f38b588735095c15ff8d7e27834c832055539d6e3111517738f96f132ee4`
- `evaluation/retrieval/RETRIEVAL_CORRECTNESS.mn.md` — `f058276688a0f01d1630a1591f5d0bf196e6c5d6e1dd5d1b99e220632b50ffcf`
- `evaluation/split/.pytest-tmp/test_offline_report_does_not_i0/privacy-check.mn.md` — `0f7d92733dedb2506c739247d86be27caef444409f525637515a448422ed2df1`
- `evaluation/split/ANNOTATION_REVIEW.mn.md` — `ff5d638b2073e0448ddf8f8af7cf6d14e3b0976a268f2542148a6edba2599bac`
- `evaluation/split/ANNOTATION_REVIEW_READINESS.mn.md` — `6a04bfb2e478c995f19e27c7fd8ee5ea752976ec42c3b49e40a3f93f1c2aa9be`
- `evaluation/split/CHAPTER4_TRACEABILITY.mn.md` — `0b0036c158a32d2d12b9ecc514cc0528a37714ef55e27d3d835816ceabda0182`
- `evaluation/split/OVERLAP_ABLATION.mn.md` — `dd6808731b7df862d4ea6ffdee08f57ed718d135ea9c75282f36676269105756`
- `evaluation/split/PHASE2_FINAL_STATUS.mn.md` — `4732e08b31ecad5e905fb8ba5e53ad7c60d7b4acb6e048964bb0077e8e49e6a7`
- `evaluation/split/README.mn.md` — `a7821b985cabeb83cfbb9fcc8d30d2f25fb8ee4c92c264351b93a9eb4cf3964f`
- `evaluation/split/RESULTS.mn.md` — `c0326f42a792b7a0fa50a80ea85f3a0116974ab43b37855d7d5ae6cdd486e5db`
- `evaluation/split/SEMANTIC_READINESS.mn.md` — `b361d05cc8aa0ff018dd2574153916bd8c7c610acdd92392e5ab9d61ed6821a2`
- `evaluation/split/split-a0-1dc80917.mn.md` — `bd6a0cae835095a16baa5d80b47272a6ce2e3cfc69fc6941cdc36d85ccfde0f4`
- `evaluation/split/split-a0-39422d96.mn.md` — `25b571ad6c476c20882a01b590c79471747e85e6bf03b8a246499e485e203abf`
- `evaluation/split/split-a1-a3-ecf27e9c.mn.md` — `46dc6aa1116f2bbbc6cbe865d3434915315720f9a5b8bb8db44610cd6278e3e3`
- `evaluation/split/split-overlap-95a117e7.mn.md` — `557df3957b7e482cddd99d7389977c300220071c2bd2f31b318a023299ebc41b`
- `evaluation/store/ANN_FIDELITY_INVESTIGATION.mn.md` — `45061654c4aa0dfa9df7f32517c89e2f4bda156757535ec535caadf3b9ded3ce`
- `evaluation/store/CHAPTER6_TRACEABILITY.mn.md` — `a273ebc4c335df581fd0e3e5452e6667ddb1c33521510f93b401286b01e603bc`
- `evaluation/store/LIFECYCLE_AUDIT.mn.md` — `8246b892fd092f8d6d628781afcb31ff58e008213184cca225167d36c12677d9`
- `evaluation/store/RESULTS.mn.md` — `667fd7979cbbf8e294088874f10f14ff237fe7a6b73894b03754803c191d7e5f`
- `app/__init__.py` — `94885717433f88f83fdee967a96e99e2dbeb10441f8ef08772e378bfb4e18fff`
- `app/config.py` — `8f56f01595b42288a19b543a2adfd94e95f791a1ea8db1c9ee192ee41d04463e`
- `app/models.py` — `c571abeebad7c9c3da5855a5a26e610a2b6fb75dee1e3f7894e1c2730eccdf3f`
- `app/services/ingestion.py` — `75c97ed7b6dc382fc9dc911c21f6d616a9288a2795c0f40f754304b6552454a5`
- `app/services/parsing.py` — `939e6722a819af0cf7de638bf5ea18239bbc87c94c09d82bd446b03d85c2328d`
- `app/services/chunking.py` — `65a155a8f689fda78b3209980a220fc242fbfd4dcfd8a547984e249e8c7b25e4`
- `app/services/embedding_contract.py` — `8fb82d2fa7f439687a02ac383b82e676af9dad16a18beb8534dd29696e3cefb8`
- `app/services/vectorstore.py` — `a8a66d96a0620e3a2e3c35691445c13fa1cfc0698e1584a384af62c0d3db65f6`
- `app/services/retrieval.py` — `b7040552f401d0f7637a8ff45cdffef78bfb954662940d64c011519643205993`
- `app/services/sparse.py` — `2bf5bfea57a91c81b35bdff48a64e3ac59d22ee8a6f3de72fdc3dd36c97f5ddd`
- `app/services/rag.py` — `14786c5ec3823b23e79f609ed303e1b263916a3819e5bcdd5b9feb434d167d3b`
- `app/services/openai_provider.py` — `5a9dbc13bbb1fbbf31e1ba118d6863b92a7f2872141603141c6725894cffcd76`
- `app/routes/chat.py` — `9c65e641d1c54344555c5b9ccc2ad5313963ea39535924aa66b5c0bcfa32e1c3`
