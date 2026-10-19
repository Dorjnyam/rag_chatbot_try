# 08 — Deployment ба performance аудит

README application-ийг Windows/Python 3.12, local PostgreSQL, embedded single-process Chroma, synchronous request flow бүхий learning project гэж зөв тайлбарлажээ. Compose PostgreSQL loopback порт ашигладаг. Worker queue, production service topology, backup/restore procedure, secret manager, distributed Chroma, autoscaling байхгүй. Local deployment-д энэ scope ил тод; shared service-д **NOT READY**.

`/health` readiness probe биш (**AUDIT-008**). Chat нь урт parsing/index/evaluation ажлыг synchronous route дээр ажиллуулдаг; query ба evaluation history бүхэлд нь render хийдэг (`chat.py:42-45,104-113`), **AUDIT-007 MEDIUM**. Query detail hit бүрээр DB/Chroma fetch хийж болзошгүй (**AUDIT-010 LOW**). Эдгээр нь өсөлтийн эрсдэл; бодит load/latency test хийгээгүй тул performance хэмжээ, throughput гаргаагүй.

API timeout/retry нь code-д bounded; browser disconnect нь background job cancellation гэсэн үг биш гэдгийг README тайлбарласан. Browser verification permission blocked байсан тул visual/mobile/accessibility runtime шалгалт хийгдээгүй. External process ажиллуулах/сервер restart хийхгүй.

**Deploy шийдвэр:** локал сургалтын хэрэглээний одоогийн ажиллагааг батлах нотолгоо хангалтгүй; иймээс локал readiness **INSUFFICIENT EVIDENCE**. Integration suite болон бодит machine дээр readiness шалгалт хүлээгдэж байна. Байгууллагын service болгохын өмнө auth/ACL, persistent shared vector architecture, async job model, observability, backup/restore болон readiness checks шаардлагатай; shared readiness **NOT READY**.
