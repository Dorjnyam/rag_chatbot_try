# Phase 4.1 — ANN Index Fidelity Investigation

## Шийдвэр

**INDEX FIDELITY VERIFIED WITH LIMITATIONS**

N=10,000 synthetic vector өгөгдөл дээрх бага `Index Recall@10` нь benchmark-ийн query/ID mapping, filter candidate set эсвэл cosine distance-ийн алдаанаас үүсээгүй. Ижил өгөгдөл, query, insertion order, HNSW construction config-тай controlled туршилтад зөвхөн effective `ef_search`-ийг өөрчлөхөд recall тогтвортой өссөн. Энэ нь Chroma HNSW-ийн бодит approximate-search зан төлөв болохыг дэмжинэ. Гэхдээ векторууд random synthetic тул production OpenAI vectors дээрх fidelity, Монгол хэлний semantic relevance батлагдаагүй.

Operational storage integrity ба human relevance нь өөр шийдвэр хэвээр: Phase 4-ийн **Operational Store Readiness — READY WITH DOCUMENTED LIMITATIONS**; **Human-Relevance Evaluation — NOT READY**.

## Хүрээ ба reproducibility

- Experiment: `Phase 4.1 controlled ANN index-fidelity ef_search ablation`; Chapter 6 Sections 6.3–6.5, 6.11.
- Run ID: `99525c3dbb9a`; private artifact: `instance/store-evaluation/ann-fidelity/99525c3dbb9a/result.json`.
- Artifact SHA-256: `76660df0d1f4283aed17777f1247b9d0abea29a4a2bf019146ebe220f0bdae4c`.
- ChromaDB 1.5.9; NumPy 2.5.4; Windows/Python орчныг private run manifest-д хадгалсан.
- N=10,000, d=1,536; 4 `ef_search` утга × 3 тусдаа graph build × 32 тогтмол query = 12 trial.
- Vector seed `6112026`, query seed `6212026`; generated vector hash, query hash болон Chroma-аас read-back хийсэн stored-vector hash бүх trial/config-д ижил.
- Chroma storage round-trip-ийн хамгийн их vector absolute deviation `7.45×10⁻⁹`; exact reference нь generated matrix биш, тухайн isolated collection-оос авсан stored vectors.
- Бүх collection шинээр тусгаарласан private scratch directory-д үүссэн. Collection бүрийг хааж, шинэ `PersistentClient`-ээр нээгээд ижил query-г дахин ажиллуулсан; 24/24 filtered/unfiltered хосод warm ба reopened top-10 ID жагсаалт яг ижил байв. API calls=0, PostgreSQL access=false, production Chroma access=false.
- Construction тогтмол: cosine, `sync_threshold=1`, `max_neighbors=16`, `ef_construction=100`; зөвхөн collection үүсгэх үед `ef_search` 50/100/200/400-аар солигдсон. Бүх effective config утга Chroma-ийн public collection config-тэй таарсан.

## Correctness audit

1. Synthetic document vectors болон query vectors нь тогтсон seed-ээр L2-normalized float32 matrix хэлбэрээр үүссэн. Insert order `v-000000`–`v-009999`; query-ууд query index-ээр яг ижил дарааллаар дамжсан.
2. Exact cosine reference нь collection-оос буцаасан stored vector-ууд болон metadata filter-д таарах яг ижил ID set дээр тооцогдсон. Элементүүдийг stable `(distance, ID)`-аар эрэмбэлж k=1/5/10 харьцуулсан.
3. Бүх 12 trial-ийн filtered/unfiltered candidate ID set exact талтай таарсан; query бүрийн Chroma result count `min(k, eligible_count)`-д хүрсэн.
4. Chroma ба NumPy distance-ийн хамгийн их absolute зөрүү `1.20×10⁻⁷`-оос бага; float32 тооцооллын зөрүүнд нийцнэ. Exact distance tie энэ 12 trial-д ажиглагдаагүй.
5. Stable ID alignment helper нь stored ID, vector, metadata-г ижил дараалалд дахин байрлуулна; тусгай shuffled-order unit test-ээр батлав. Deterministic Chroma fixture нь урьдчилан мэдэгдсэн nearest-neighbor дараалал болон filtered candidate set-ийг шалгасан. `ef_search` 50/100/200/400 утга effective config-д яг хэрэглэгдсэнийг тус бүр тестлэсэн.

Иймээс candidate mismatch, буруу query mapping, normalization болон distance formula нь бага recall-ийн шалтгаан гэсэн нотолгоо олдсонгүй. `Index Recall` нь exact vector neighbor-уудын давхцал бөгөөд хүний relevance биш.

## Controlled үр дүн

Хүснэгтийн Recall нь 3 graph build тус бүрийн дундаж; P50/P95/P99 нь trial бүрийн warm search quantile-уудын дундаж. Latency-д embedding, PostgreSQL, reranking, LLM generation ороогүй. Filter нь synthetic `segment=A`, 5,000 eligible vector.

| ef_search | Filter | R@1 | R@5 | R@10 | R@10 давталтын хүрээ | P50/P95/P99 (ms) | Build+upsert дундаж (ms) | Disk дундаж (MiB) | Peak RSS max (MiB) |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 50 | Unfiltered | .271 | .281 | .260 | .253–.269 | 2.78 / 3.42 / 5.04 | 7,986 | 64.26 | 1,047.7* |
| 100 | Unfiltered | .312 | .417 | .401 | .369–.438 | 3.32 / 4.04 / 4.10 | 7,341 | 64.26 | 576.7 |
| 200 | Unfiltered | .604 | .573 | .556 | .541–.572 | 4.20 / 5.12 / 6.37 | 7,810 | 64.27 | 695.2 |
| 400 | Unfiltered | .781 | .806 | .786 | .772–.809 | 5.43 / 6.25 / 10.28 | 7,274 | 64.27 | 709.1 |
| 50 | `segment=A` | .312 | .321 | .341 | .322–.372 | 11.72 / 14.38 / 15.32 | 7,986 | 64.26 | 1,047.7* |
| 100 | `segment=A` | .531 | .506 | .531 | .512–.550 | 12.27 / 15.15 / 16.97 | 7,341 | 64.26 | 576.7 |
| 200 | `segment=A` | .802 | .775 | .772 | .759–.787 | 13.21 / 15.96 / 16.47 | 7,810 | 64.27 | 695.2 |
| 400 | `segment=A` | .938 | .921 | .928 | .925–.934 | 14.59 / 17.41 / 18.01 | 7,274 | 64.27 | 709.1 |

`ef_search=100`-аас `400` руу өсгөхөд unfiltered R@10 `.401 → .786`, filtered R@10 `.531 → .928` болсон. Үүний зэрэгцээ search P95 дунджаар unfiltered `4.04 → 6.25 ms`, filtered `15.15 → 17.41 ms` өсөв. Disk хэмжээнд мэдэгдэхүйц ялгаа гараагүй. `ef_search=50`-ийн нэг trial-д whole-process peak RSS `1,047.7 MiB` хүрч, үлдсэн 11 trial `496–709 MiB` байв; ганц өндөр peak-ийн шалтгаан тогтоогдоогүй тул RSS тохиргоо тус бүрийн ялгаа гэж тайлбарлахгүй.

Persistence-ийн дараах эхний query latency-ийн 3 repeat дундаж unfiltered `ef_search=50/100/200/400` үед `2.68/3.45/5.64/5.56 ms`, filtered үед `12.55/12.47/13.87/15.33 ms` байв. Эдгээр нь шинэ client-ийн эхний query; OS file cache цэвэрлээгүй тул cold-disk latency биш. Warm ба reopen top-10 нь бүх туршилтад ижил ID дарааллаар буцсан.

Өмнөх Phase 4 scale sweep нь N=1,000 дээр R@10 `.956`, N=10,000 дээр `.394` (`ef_search=100`, хоёр repeat)-ийг тайлагнасан; тэр baseline query-г шинэ client-ээр index reopen хийсний дараа гүйцэтгэсэн. Одоогийн paired warm/reopen ablation-д N=10,000 `ef_search=100` R@10 `.401` бөгөөд warm/reopened query-уудын top-10 яг таарсан. Query count/repeat өөр тул `.394` ба `.401`-ийг нэг sample гэж нэгтгэхгүй; ойролцоо үр дүн нь бага recall reopen эсвэл ID mapping-оос үүсээгүй гэдгийг дэмждэг.

## Failure жишээ

`ef_search=50`, unfiltered, query index 0:

- Exact rank 1: synthetic ID `v-005844`, cosine distance `0.898892`.
- ANN top-1 distance `0.911455`; exact rank-1 vector top-10-д олдоогүй. Зөрүү `0.012563`.
- Exact rank-10/11 boundary margin `0.000250` — top-10 захын neighbors маш ойр.
- Filtered query-д мөн энэ exact rank-1 neighbor дутсан (5,000 eligible candidate).

Эдгээр ID ба distance нь synthetic vectors-оос; confidential баримтын текст эсвэл metadata агуулаагүй. Boundary margin бага байх нь ANN rank солигдох эмзэг байдлыг тайлбарлах нэг дохио боловч ганц query-н алдааны цорын ганц шалтгаан гэж дүгнэхгүй.

## Бодит векторын боломж

Өмнөх Phase 4 audit-аар Embed smoke artifact-ууд plan-only эсвэл `local_mock` байсан; баталгаатай real OpenAI vector snapshot байгаагүй. Production Chroma-г шалгаж нээгээгүй. Энэ investigation шинэ embedding API дуудлага хийгээгүй. Иймээс OpenAI `text-embedding-3-small` vectors дээр ANN fidelity-г одоогоор батлах боломжгүй.

## Зөвлөмж ба readiness

- Benchmark method correctness **verified**: known-neighbor, filtered/unfiltered, stable IDs, exact stored vectors, effective config, result completeness, distance parity.
- Low N=10,000 recall нь Chroma HNSW-ийн бодит approximation-ийн үр дүн гэдгийг controlled search breadth ablation дэмжив. Өндөр `ef_search` synthetic fidelity-г сайжруулж, latency/RSS-ийн өртөгтэй.
- **Production `ef_search`-ийг автоматаар өөрчлөхгүй.** Бодит vector snapshot болон representative human-verified query set-гүйгээр ямар утга production-д зөв болохыг тогтоохгүй.
- Synthetic vector benchmark-ийг Chapter 6-ийн operational/index-fidelity evidence гэж ашиглана; Mongolian retrieval accuracy гэж тайлбарлахгүй.
- **Operational Store Readiness:** `READY WITH DOCUMENTED LIMITATIONS` — өмнөх lifecycle regression нотолгоо; энэ investigation production store-д хүрээгүй.
- **Index Fidelity:** `INDEX FIDELITY VERIFIED WITH LIMITATIONS`.
- **Human-Relevance Evaluation:** `NOT READY`.

## Reproduction

```powershell
.\.venv\Scripts\python.exe -m flask --app app benchmark-store ann-fidelity --repeats 3 --queries 32 --ef-search 50,100,200,400
.\.venv\Scripts\python.exe -m pytest tests\test_store_benchmark.py -q -p no:cacheprovider --basetemp=instance\pytest-store-fidelity
```

Private detailed per-query miss records болон vectors-ийн hash-ууд `instance/store-evaluation/ann-fidelity/<run_id>/result.json` дотор байна; repository-д commit хийхгүй. Нийтийн тайланд зөвхөн aggregate synthetic хэмжилт, synthetic жишээ орсон.
