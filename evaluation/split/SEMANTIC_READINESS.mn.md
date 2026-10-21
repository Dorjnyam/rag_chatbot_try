# Semantic chunking readiness

## Шийдвэр: бодит semantic inference blocked

`app/split_semantic.py` нь тусгаарласан experiment primitive-үүдийг нэмсэн: Монгол/English sentence boundary-ийн conservative segmentation, adjacent-vector cosine distance, 80/90/95 percentile breakpoints, 512-token cap, deterministic fixed-token fallback, injected embedding provider, content-hash/model/config key-тэй private vector cache.

Энэ нь production splitter-д холбогдоогүй. Unit test-д ашигласан `DeterministicMockEmbeddings` нь SHA-256 lexical hash mock бөгөөд semantic model биш; зөвхөн chunking orchestration, cache determinism болон S2 gate шалгана. Үүний үр дүнгээр corpus semantic чанар дүгнэх боломжгүй.

Бодит model inference хийгдээгүй. Шалгасан `instance/models/qwen3-reranker-0.6b` нь CrossEncoder/reranker бөгөөд sentence embedding model биш; локал Hugging Face/Ollama embedding cache олдоогүй. OpenAI embedding API-г дуудаагүй. Иймээс бодит inference token/call/cost, latency, semantic breakpoint quality нь N/A; model сонголт ба үнийн баталгаатай тооцоо, хэрэглэгчийн зөвшөөрөлгүйгээр paid run эхлүүлэхгүй.

## S1/S2 туршилтын төлөв

| Туршилт | Төлөв | Тайлбар |
|---|---|---|
| S1 mock/unit feasibility | Implemented and tested | Dependency injection, deterministic mock, cache, percentile parameter, chunk token cap шалгав. Semantic quality test биш. |
| S1 real corpus inference | Blocked | Боломжтой local embedding model байхгүй; API ашиглах зөвшөөрөл/cost тохиргоо байхгүй. |
| S2 structure-constrained semantic | Blocked | Код нь verified boundary offsets шаардана. Frozen parser output-д human-verified structure boundary байхгүй. S2 нь S1 рүү чимээгүй fallback хийхгүй. |
| Retrieval evaluation | Not ready | 80 асуулт 3 query хувилбартай боловч бүгд draft; human-verified evidence annotation 0. |

## Cost / complexity

Boundary identification embeddings болон retrieval/index embeddings тусдаа үе шат байна. Cache key нь content SHA-256, model identity, config-аас үүснэ; raw text cache-д бичигдэхгүй. API usage 0 тул actual billed cost 0. Бодит model сонгогдоогүй учраас prospective token estimate болон мөнгөн дүнг найдвартай гаргах боломжгүй. Дараагийн алхам: local embedding model/runtime сонгох, эсвэл provider/model/batch/rate/cost тооцоог гаргаж хэрэглэгчээр батлуулах.

## Reproduction

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider --basetemp=instance\split-evaluation\test-tmp tests/test_split_benchmark.py -q
```

S1 unit tests mock provider ашиглана. Corpus inference command санаатайгаар үүсгээгүй тул modelгүй үед бодит run эхлэхгүй.
