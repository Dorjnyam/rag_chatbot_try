# Split evaluation артефактууд

Энэ хавтас Chapter 4 traceability, A0–A3 offline baselines, experiment plan, ground-truth review workflow, synthetic span-scan probe болон дүгнэлтийн тайланг агуулна.

```powershell
flask --app app benchmark-split freeze
flask --app app benchmark-split run
flask --app app benchmark-split prepare-annotations --documents 20 --questions-per-document 4
flask --app app benchmark-split run-a1-a3
flask --app app benchmark-split complexity-probe
flask --app app benchmark-split token-count-probe
```

`freeze` нь Phase 1 dataset SHA-256 болон бүх raw cache hash/count-ийг тулгаж, confidential extraction artifact-уудыг Git-ээс үл хамаарах `instance/split-evaluation/` дотор хуулна. `run` нь production PostgreSQL/ChromaDB болон embedding API ашиглахгүй. Public JSON/CSV нь anonymous DOC ID, hashes, metrics хадгална; text хадгалахгүй.

Унших дараалал: [шийдвэр ба хэмжилт](RESULTS.mn.md), [4.1–4.10 traceability](CHAPTER4_TRACEABILITY.mn.md), [туршилтын manifest](EXPERIMENT_PLAN.json), [ground-truth document template](ground_truth_template.mn.json).

Human annotation review package нь ignored `instance/split-evaluation/annotation-review/` дотор үүснэ. Эх document path болон evidence text confidential тул Git-д орохгүй. Public workflow: [annotation review report](ANNOTATION_REVIEW.mn.md). Final A1–A3 run: `split-a1-a3-ecf27e9c`.

Size grid, overlap ablation, sentence-based evaluation болон retrieval experiments `EXPERIMENT_PLAN.json`-д planned хэвээр. A1–A3 offline results нь algorithm superiority эсвэл retrieval quality-г батлахгүй; retrieval gate нь human-verified evidence annotation хүлээж байна.
