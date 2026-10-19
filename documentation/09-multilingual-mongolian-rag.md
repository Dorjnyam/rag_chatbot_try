## Query processing

`app/services/language.py:query_candidates` original query-г NFC болон зайгаар нормчилж, `mn_cyrl`, `mn_latn`, `en`, `mixed`, `unknown` ангилал болон confidence буцаана. Нэр, acronym, URL, дугаар, кодыг хамгаална. `mn_latn` үед зөвхөн танигдсан бүтэн үгийг толь болон нийтлэг хувилбараар кирилл candidate болгоно. Танигдаагүй үг, холимог English segment хүчээр орчуулагдахгүй. Original асуулт үргэлж хадгалагдана; нийт давхцаагүй candidate гурваас ихгүй.

Англи query эх хувилбараараа dense embedding-д орно. Баримтын хэлээр query-г автоматаар шүүхгүй; зөвхөн хэрэглэгч explicit document-language filter сонгосон үед шүүнэ. `QUERY_REWRITING_ENABLED=false` анхдагч. Идэвхжсэн бол mixed/unknown query-д хамгийн ихдээ нэг Gemini structured request хийнэ. Урт болон хамгаалсан identifier шалгалт амжилтгүй бол original candidate хэвээр.

## Хариултын хэл ба UI

`app/services/rag.py:SYSTEM` хариултыг монгол кириллээр шаардана. Одоогийн route, JSON key, citation contract хадгалагдана. Хэлний эвдрэл илэрвэл нэг удаа засварлуулна; эх citation list, тоо, protected token өөрчлөгдвөл Монгол fallback хариу буцаана. Гарчиг, файл нэр, URL, acronym нь англи хэлээр байж болох тул output checker эдгээрийг prose-ийн хэлний нотолгоо гэж үзэхгүй.

HTML `lang="mn"`; Jinja template болон browser validation message Монгол кириллээр. Хэрэглэгчийн query болон эх баримтын title, filename, source text, machine identifier өөрчлөгдөхгүй. OCR, баримтын текст зэрэг UI-д харуулах эх өгөгдөл translation хийхгүй.

## Synthetic draft ба бодит corpus

`evaluation/corpus/questions-multilingual-synthetic.jsonl` нэг intent-ийн кирилл, англи, латин монгол, холимог хувилбаруудыг dev/test split дотор бүлэглэнэ. Энэ нь pipeline-ийн synthetic smoke test бөгөөд бодит байгууллагын баримтын recall, nDCG, normalization утгын чанар эсвэл answer faithfulness-ийг нотлохгүй.

Бодит corpus ирсний дараа gold evidence-г excerpt hash болон location-оор тулган review хийнэ. Ижил corpus, dataset, split, final K дээр dense-only, hybrid, hybrid+reranker, conditional rewrite хувилбаруудыг харьцуулж, хэл тус бүрээр үр дүн гаргана. `run_evaluation` Hit Rate, nDCG, input category, detection accuracy, normalization exact match annotation, answer-language compliance, stage latency-г бүртгэнэ. Тайлбарласан labels болон хүний review дутуу бол тухайн acceptance хүлээгдэж байна.

Автомат хэл илрүүлэлт нь кирилл болон тодорхой lexical cue-д сайн боловч богино нэр, acronym, олон утгатай латин үг, код-сонголтод эргэлзээтэй. Тиймээс confidence болон original query-г trace-д хадгална; эргэлзээтэй үгийг хүчээр хөрвүүлэхгүй.
