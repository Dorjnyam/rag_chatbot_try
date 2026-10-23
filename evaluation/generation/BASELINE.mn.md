# Phase 6 — Baseline ба ажиллах хүрээ

## Frozen application baseline

| Component | Тохиргоо | Үндэслэл |
|---|---|---|
| Source corpus | Load snapshot: 151 successful documents; 3 өмнөх Load failure хэвээр тусдаа | Өмнөх Load/Split reports; энэ үе шат дахин parse хийгээгүй |
| Chunking | A0: 1,800 characters; 250 overlap | Production config; энэ үе шат өөрчлөөгүй |
| Embeddings | OpenAI `text-embedding-3-small`, 1,536 dimensions | Existing provider/config; энэ үе шат өөрчлөөгүй |
| Retrieval | Hybrid; Dense 15 + BM25 15; RRF `k=60` | `app/config.py`, Phase 5 reports |
| Reranking/query rewrite | Хоёулаа disabled | Existing baseline |
| Context | Дээд тал нь 6 chunk; context 8,000, history 2,000, input 12,000, output 1,000 tokens | `app/config.py` defaults |
| Generation | OpenAI `gpt-6-luna`, reasoning `none`; API output schema strict JSON | Local config болон `OpenAIProvider.generate` |
| Human reference | 2 independent verified question; 6 correlated language variation; 16 draft question | Private `ground-truth-gt-v1.json`; G7 зөвхөн question/evidence payload-ийг offline mechanics-д ашигласан |

Environment package versions: Python 3.12.15, Flask 3.1.3, OpenAI SDK 2.30.0, tiktoken 0.12.0. Эдгээр нь энэ implementation run-ийн орчин; цааш өөрчлөгдөж болно.

## Frozen versus measured

- Production parser, splitter, PostgreSQL, Chroma collection, embedding model/config өөрчлөгдөөгүй.
- G7 нь **6 хувийн annotation question variation + 12 synthetic fixture**-ийг local context/budget/output-validator урсгалаар ажиллуулсан. Model response нь тогтмол fake Mongolian response байсан.
- Эх асуулт/evidence-ийг зөвхөн process дотор ашиглаж, raw private content-ийг result файлд хадгалаагүй. Expected answer-ийг generator эсвэл quality evaluator болгон ашиглаагүй.
- Human claim correctness, faithfulness, citation entailment, language quality хэмжээгүй.
- Ретрай/repair provider performance, OpenAI latency, TTFT, billed tokens/cost, production concurrency хэмжээгүй.

Historical Phase 5.1 full suite: 196 passed, 1 skipped, 0 failed. Энэ бол **өмнөх run**; Phase 6-ийн regression үр дүнг `RESULTS.mn.md`-д тусад нь тайлагнана.
