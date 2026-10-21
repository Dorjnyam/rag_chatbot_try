# Split evaluation артефактууд

Энэ хавтас Chapter 4 traceability, A0 offline baseline, experiment plan, ground-truth template, synthetic span-scan probe болон дүгнэлтийн тайланг агуулна.

```powershell
flask --app app benchmark-split freeze
flask --app app benchmark-split run
flask --app app benchmark-split complexity-probe
flask --app app benchmark-split token-count-probe
```

`freeze` нь Phase 1 dataset SHA-256 болон бүх raw cache hash/count-ийг тулгаж, confidential extraction artifact-уудыг Git-ээс үл хамаарах `instance/split-evaluation/` дотор хуулна. `run` нь production PostgreSQL/ChromaDB болон embedding API ашиглахгүй. Public JSON/CSV нь anonymous DOC ID, hashes, metrics хадгална; text хадгалахгүй.

Унших дараалал: [шийдвэр ба хэмжилт](RESULTS.mn.md), [4.1–4.10 traceability](CHAPTER4_TRACEABILITY.mn.md), [туршилтын manifest](EXPERIMENT_PLAN.json), [verified ground-truth template](ground_truth_template.mn.json).

Төлөвлөгөөнд `planned_not_executed` гэж тэмдэглэсэн алгоритмын туршилтууд энэ delivery-д ажиллаагүй.
