# Ачаалах үе шатны benchmark dataset

`manifest.json` нь RAG evaluation-д аль хэдийн орсон 16 синтетик DOCX баримтыг тогтмол input болгоно. Эдгээр файл бодит байгууллагын өгөгдөл биш. Одоогоор гараар баталгаажуулсан extraction reference байхгүй тул runner нь амжилт, latency, хэмжээ, бүтэц, warning хэмжинэ; accuracy/CER-ийг мэдээлэхгүй.

Ground truth нэмэхдээ reference бүрийг хүн эх баримттай тулгаж баталгаажуулж, доорх талбаруудыг тухайн case-д оруулна:

```json
{
  "id": "leave-mn-reviewed-1",
  "file": "../corpus/leave-mn.docx",
  "ground_truth": {
    "text_file": "references/leave-mn.txt",
    "headings": ["Чөлөө"],
    "clauses": ["Чөлөөний хүсэлтийг эхлэх өдрөөс 5 ажлын өдрийн өмнө өгнө."],
    "table_texts": ["Төрөл | Хугацаа"],
    "table_blocks": 1
  }
}
```

`text_file` нь NFC Unicode plain text байх ба reference-ийг автоматаар parser output-оос үүсгэж болохгүй. Гарчиг, заалт, хүснэгтийн шалгуурыг хүн баталгаажуулна. `table_texts` нь хүснэгт доторх зайлшгүй текстийн subset; `table_blocks` нь бүтэц хадгалсан хүснэгтийн block-ийн нарийн тоо.

Benchmark нь `parse_bounded()` subprocess-ийг ашиглаж, PostgreSQL/Chroma руу холбогдохгүй. Алдааны кейсүүдийг тестээр синтетик файл үүсгэн шалгана: буруу DOCX, дэмжигдээгүй өргөтгөл, хоосон баримт, нууц үгтэй PDF, давхардсан upload, болон хэсэгчлэн тексттэй баримт. Хувийн баримтыг benchmark dataset-д бүү хуул; private fixture ашиглах шаардлагатай бол output directory болон ажиллуулах орчны ACL-ийг хязгаарла.
