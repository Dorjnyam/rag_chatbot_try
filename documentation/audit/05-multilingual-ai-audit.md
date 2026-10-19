# 05 — Монгол хэл ба AI provider аудит

## Хэлний боловсруулалт

`services/language.py` Unicode normalization, Cyrillic болон fixed lexicon-аар латин монгол бичлэг, English evidence ашиглан `mn_cyrl`, `mn_latn`, `en`, `mixed`, `unknown` label/candidate гаргадаг. Эх асуулт хадгалагдаж, танигдаагүй үгийг автоматаар хүчээр хөрвүүлэхгүй. Candidate тоо хязгаартай; identifier хамгаалах шалгалттай. Эдгээр нь conservative heuristic болохоос өргөн корпус дээр батлагдсан multilingual detector биш.

`answer_is_mongolian` (`language.py:88-93`) ихэнх үсэг Cyrillic эсэх script ratio-оор шалгадаг. Cyrillic ашигладаг бусад хэлний prose мөн тэнцэх боломжтой. **AUDIT-005 MEDIUM, CONFIRMED**: энэ нь Mongolian-language identification биш. Одоогийн synthetic unit тест энэ false positive-г шалгахгүй.

Generation prompt нь Монгол кирилл шаардаж, citation/source identifier-ийг яг хадгалахыг заадаг. `rag.ask` хариултыг шалгаж, citation/JSON/lang алдаанд нэг repair оролддог; protected token болон source list өөрчлөгдвөл fallback өгдөг. Тестүүд mocked Gemini/OpenAI client ашигласан бөгөөд бодит model output биш.

## Provider coverage

Provider factory `AI_PROVIDER` сонголт, legacy Gemini default/generic model config болон OpenAI-specific model/dimension setting дэмждэг. OpenAI embedding local tokenizer/token/byte guard, ordered batches, normalization; Gemini adapter тусдаа API behavior, bounded retry/deadline. Selected key validation байх ёстойг CLI doctor харуулдаг. OpenAI болон Gemini live API access, account quota, 2026 оны model availability, prompt adherence энэ audit-д шалгагдаагүй. Key утга тайланд ороогүй.

## Чанарын дүгнэлт

Бодит Монгол/англи/латин монгол policy corpus, reviewed gold evidence, хүний faithful/citation review байгаагүй тул retrieval recall, faithfulness, translation/normalization meaning quality болон answer quality **NOT VERIFIED**. Synthetic split/group consistency-ийн smoke test ажилласан; тэр нь чанарын benchmark биш.
