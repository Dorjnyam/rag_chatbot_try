"""Preview routes and OCR-review versioning use only local synthetic documents."""
import io
import json

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from werkzeug.datastructures import FileStorage

from app.extensions import db
from app.models import Document, DocumentVersion
from app.services import preview
from app.services.chunking import chunk
from app.services.storage import read_json, write_json


def _pdf_bytes(text="Company policy permits five working days."):
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject({NameObject("/Type"):NameObject("/Font"), NameObject("/Subtype"):NameObject("/Type1"), NameObject("/BaseFont"):NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"):DictionaryObject({NameObject("/F1"):writer._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 12 Tf 50 700 Td ({text}) Tj ET".encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def _uploaded_pdf(client, text="Company policy permits five working days."):
    response = client.post("/documents", data={
        "title":"Synthetic policy", "language":"en",
        "file":(io.BytesIO(_pdf_bytes(text)),"policy.pdf"),
    }, content_type="multipart/form-data", headers={"X-CSRF-Token":_csrf(client)})
    assert response.status_code == 302
    version_id = response.headers["Location"].split("/versions/", 1)[1].split("?", 1)[0]
    with client.application.app_context():
        version = db.session.get(DocumentVersion, version_id)
        artifact = {"blocks":[{"text":text,"heading":"Policy","kind":"page","method":"native","page":1,"block":1}],"candidates":[],"warnings":[]}
        relative = f"extractions/test-preview-{version.id}.json"
        write_json(relative, artifact)
        version.extraction_path = relative
        version.state = "ready"
        version.expected_chunks = 1
        document = db.session.get(Document, version.document_id)
        document.active_version_id = version.id
        db.session.commit()
        return version.id, document.id


def _csrf(client):
    with client.session_transaction() as session:
        return session["csrf"]


def test_preview_prepare_requires_existing_csrf(client):
    response = client.post("/versions/missing/preview/prepare")
    assert response.status_code == 400


def test_preview_prepare_exposes_safe_actionable_failure(client, monkeypatch):
    version_id, _ = _uploaded_pdf(client)
    headers = {"X-CSRF-Token":_csrf(client)}
    monkeypatch.setattr(preview, "prepare", lambda version: (_ for _ in ()).throw(
        ValueError("Synthetic preview conversion failure.")))
    response = client.post(f"/versions/{version_id}/preview/prepare", headers=headers)
    assert response.status_code == 422
    assert response.json["error"] == "Synthetic preview conversion failure."


def test_preview_prepare_map_and_pdf_use_synthetic_source(client, app):
    version_id, _ = _uploaded_pdf(client)
    headers = {"X-CSRF-Token":_csrf(client)}
    prepared = client.post(f"/versions/{version_id}/preview/prepare", headers=headers)
    assert prepared.status_code == 200, prepared.get_data(as_text=True)
    assert prepared.json["status"] == "ready"
    detail = client.get(f"/versions/{version_id}")
    assert detail.status_code == 200
    assert "preview-workspace" in detail.get_data(as_text=True)
    page = client.get(f"/versions/{version_id}/preview/map?page=1")
    assert page.status_code == 200
    assert page.json["mapped"][0]["status"] == "mapped"
    source = client.get(f"/versions/{version_id}/preview")
    assert source.status_code == 200
    assert source.mimetype == "application/pdf"
    assert source.headers["Cache-Control"] == "private, no-store"


def test_ocr_proposal_creates_review_version_without_changing_active(client, app):
    version_id, document_id = _uploaded_pdf(client)
    headers = {"X-CSRF-Token":_csrf(client)}
    prepared = client.post(f"/versions/{version_id}/preview/prepare", headers=headers)
    assert prepared.status_code == 200
    with app.app_context():
        version = db.session.get(DocumentVersion, version_id)
        mapping = read_json(preview.current_paths(version)[1])
        proposal = {"id":"synthetic-proposal", "page":1, "rect":[40,680,280,720],
            "text":"Supplementary synthetic OCR evidence", "words":[], "mean_confidence":88.0,
            "source_sha":version.sha256,"extraction_sha":mapping["extraction_sha"],"status":"proposal"}
        write_json(f"previews/{version.id}/proposals.json", {"source_sha":version.sha256,
            "extraction_sha":mapping["extraction_sha"],"items":[proposal]})
        response = client.post(f"/versions/{version.id}/preview/apply", json={
            "proposal_ids":[proposal["id"]],"return_to":"/documents"}, headers=headers)
        assert response.status_code == 201, response.get_data(as_text=True)
        reviewed = db.session.get(DocumentVersion, response.json["version_id"])
        document = db.session.get(Document, document_id)
        db.session.refresh(document)
        assert reviewed.state == "review"
        assert reviewed.review_required is True
        assert reviewed.approved_at is None
        assert document.active_version_id == version.id
        artifact = json.loads(preview.path(reviewed.extraction_path).read_text(encoding="utf8"))
        assert artifact["preview_ocr_provenance"]["proposal_ids"] == [proposal["id"]]
        assert artifact["blocks"][-1]["source_region"]["page"] == 1


def test_ocr_crop_validation_rejects_outside_coordinates(client):
    version_id, _ = _uploaded_pdf(client)
    headers = {"X-CSRF-Token":_csrf(client)}
    assert client.post(f"/versions/{version_id}/preview/prepare", headers=headers).status_code == 200
    response = client.post(f"/versions/{version_id}/preview/ocr", json={
        "page":1,"rect":[-1,10,100,100],"untrusted_path":"C:/private/source.pdf"}, headers=headers)
    assert response.status_code == 400


def test_changed_extraction_invalidates_preview_map(client, app):
    version_id, _ = _uploaded_pdf(client)
    headers = {"X-CSRF-Token":_csrf(client)}
    assert client.post(f"/versions/{version_id}/preview/prepare", headers=headers).status_code == 200
    with app.app_context():
        version = db.session.get(DocumentVersion, version_id)
        write_json(version.extraction_path, {"blocks":[{"text":"Changed synthetic evidence","page":1,"block":1}],"candidates":[],"warnings":[]})
    response = client.get(f"/versions/{version_id}/preview/map?page=1")
    assert response.status_code == 404


def test_preview_sidecar_cleanup_is_version_scoped(app):
    with app.app_context():
        write_json("previews/synthetic-version/cache/map.json", {"synthetic":True})
        preview.delete_version_artifacts("synthetic-version")
        assert not preview.path("previews/synthetic-version").exists()


def test_chunk_keeps_ocr_region_provenance_in_scalar_metadata():
    blocks = [{"text":"Supplementary OCR evidence","heading":"OCR proposal · page 1","kind":"ocr_region",
        "method":"ocr_proposal","page":1,"block":2,
        "source_region":{"page":1,"rect":[10,20,100,50],"proposal_id":"abc"}}]
    items = chunk(blocks,{"chunk_size":100,"chunk_overlap":10},"00000000-0000-0000-0000-000000000001")
    assert items[0]["metadata"]["extraction_method"] == "ocr_proposal"
    assert json.loads(items[0]["metadata"]["source_regions_json"])[0]["proposal_id"] == "abc"
