# 07 — Аюулгүй байдлын аудит

## Локал boundary

`create_app`-ийн before-request hook нь loopback бус remote address-ийг хааж, өөрчлөх HTTP method-д session CSRF token болон Origin шалгана. Session cookie HTTPOnly/SameSite Strict; Jinja default escaping ашиглана. Upload нийт хэмжээ 20 MiB, өргөтгөл болон parsed content шалгалттай. Local-only хэрэглээнд эдгээр хамгаалалт бодитой эхлэл болно. Нэвтрэх хэрэглэгч, role болон баримт тус бүрийн ACL байхгүй.

## Хамтын үйлчилгээний readiness

**AUDIT-002 HIGH, CONFIRMED:** app remote access-ийг хааж байгаа нь intended local use боловч олон ажилтантай shared service-д authentication, document authorization, tenant boundary байхгүй. Network exposure эсвэл proxy topology-г өөрчлөхөөс өмнө identity/ACL болон trusted proxy behavior-ийг загварчилж шалгах шаардлагатай. Энд unauthorized network access test хийгээгүй.

`/health` үргэлж static `{status: ok}` өгнө (`app/__init__.py:50-52`), DB/Chroma/index/provider readiness шалгахгүй (**AUDIT-008 MEDIUM**, operational). Энэ нь liveness-д байж болох ч readiness гэж ашиглавал буруу эерэг төлөв өгнө.

## Threat review ба хязгаарлалт

Prompt-д document text untrusted evidence гэж тодорхойлж, citations зөвхөн supplied labels-аас сонгоно. CSRF/XSS/session isolation route assertions test кодод байгаа боловч database suite ажиллаагүй тул execution баталгаа алга. SSRF, CORS, external network exposure, proxy, filesystem ACL, malicious document fuzzing, dependency advisory sweep гүйцэтгээгүй. Scanner суулгаагүй; dependency vulnerability “байхгүй” гэж дүгнээгүй. API keys, company text тайлан/логт оруулаагүй.

`rag.ask` exception string-ийг query error талбарт хадгалдаг; stack trace/key өөрөө зориудаар шалгагдсангүй. Provider SDK exception redaction-ийг production logging threat model-д нэмж баталгаажуулахыг зөвлөж байна.
