# Баримтын preview, холбоос ба хүсэлтээр OCR

Version дэлгэрэнгүй хуудсанд таньсан текстийг эх PDF preview-тэй зэрэгцүүлж харуулна. PDF.js `6.3.289`-ийн browser assets локалд байна. DOC/DOCX болон legacy DOC файлыг macro security өндөртэй түр LibreOffice profile ашиглан тухайн компьютерт PDF болгон хөрвүүлнэ. Баримтыг CDN эсвэл гуравдагч талын viewer рүү илгээхгүй.

Preview нь review extraction болон индексийг өөрчлөхгүй. PDF-ийн page/line координатыг одоо байгаа pypdfium2-оор тусгаарласан процессоос авч, эх хувилбарын SHA-256, extraction-ийн SHA-256, LibreOffice executable-ийн fingerprint, layout revision-ээр sidecar cache үүсгэнэ. Танилтын текст давтагдсан эсвэл таарахгүй үед яг highlight гэж мэдэгдэхгүй; хуудасны түвшин эсвэл “байрлал тодорхойгүй” гэсэн тэмдэглэгээ өгнө. DOC/DOCX-ийн Word citation хуудасны дугаар preview PDF-ээс үүсэхгүй.

PDF.js-ийн text layer ердийн сонголт/copy-г дэмжинэ. Танилсан хэсэгт яг таарсан координат байвал ногоон highlight хийнэ. Image object-уудыг OCR шалгах боломжтой шар бүс гэж үзүүлэх боловч зурагт текст байгаа гэж батлахгүй. “Хэсэг сонгох” горимоор дурын OCR crop зурж болно. Scanned page-д тодорхой image object байхгүй бол гараар бүс сонгох боломж хэвээр.

## OCR тохируулах

Windows-д Tesseract executable-ийг локалд суулгана. Script нь машинд програм суулгахгүй. Дараа нь төслийн үндсэн хавтсаас:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup-ocr.ps1
```

Script нь `tesseract-ocr/tessdata_fast`-ийн pinned commit-оос `mon` болон `eng` өгөгдлийг `instance/tessdata` дотор татаж, SHA-256-ийг шалгана. Global Tesseract tessdata-г өөрчлөхгүй. Хэвлэсэн `TESSERACT_CMD` болон `TESSDATA_PREFIX`-ийг өөрийн `.env` файлд хуулж, Flask-ийг дахин эхлүүлнэ. Компанийн эх баримтыг OCR smoke test-д автоматаар оруулахгүй.

## Хэрэглэх дараалал

1. Баримтын хувилбарын хуудсанд “Preview нээх”-ийг дарна. PDF/DOC хөрвүүлэлт болон mapping нь локалд, хүсэлт бүрээр хийгдэнэ.
2. Танилсан хэсгийг дарахад preview боломжтой координат руу шилжиж ногооноор тэмдэглэнэ. PDF дээр текстийн хэсэгт дарахад холбогдох extraction нээгдэнэ.
3. Шар image region дээр дарах эсвэл “Хэсэг сонгох”-оор OCR тэгш өнцөгт зурж, хүсэлтээр OCR ажиллуулна.
4. OCR саналыг эхтэй тулгаж, хүсвэл нэг эсвэл олон саналыг сонгон “Сонгосон саналаар шалгах шинэ хувилбар үүсгэх”-ийг дарна.
5. Шинэ хувилбар review төлөвт орно. Одоо байгаа checkbox-оор бүх танилтыг шалгаж баталсны дараа л тусдаа боловсруулах үйлдэл индексийг үүсгэж идэвхжүүлнэ. Энэ хүртэл өмнөх active version хайлтад үлдэнэ.

OCR-ийн engine confidence нь accuracy хэмжүүр биш. Нэр, утга, дугаар, огноо, үгүйсгэлийг эх зурагтай хүн тулгана. Уншигдах тэгш өнцөгтүүд гарч ирэхгүй PDF, давтагдсан текст, олон багана, Word-ийн layout дээр mapping хуудасны түвшинд үлдэж болно. Энэ UI нь SyncTeX шиг source-code холбоос биш; синхрон review workspace юм.

Preview sidecar-ууд `instance/previews/<version-id>/` дотор хадгалагдаж, version устгах үед устна. Mapping cache зөрүүтэй бол preview-г дахин бэлтгэж байж highlight харуулна. Preview endpoint-ууд `version`-ийн сервер талын эх замыг ашигладаг; браузерын POST-д зам дамжуулах шаардлагагүй.

## Эх сурвалж

- [PDF.js Getting Started](https://mozilla.github.io/pdf.js/getting_started/) — локал display build, worker, CMap болон font assets.
- [PDF.js API](https://mozilla.github.io/pdf.js/api/index.html) — PDF viewport/text layer.
- [pypdfium2 Python API](https://pypdfium2.readthedocs.io/en/stable/python_api.html) — координат авах болон PDFium-ийн thread safety.
- [Tesseract command line](https://tesseract-ocr.github.io/tessdoc/Command-Line-Usage.html) — TSV text/word bounding boxes ба confidence.
- [Tesseract trained data](https://tesseract-ocr.github.io/tessdoc/Data-Files.html) — fast/best language data-ийн ялгаа.
- [LibreOffice PDF export](https://help.libreoffice.org/latest/en-US/text/shared/guide/pdf_params.html) — локал экспорт.
