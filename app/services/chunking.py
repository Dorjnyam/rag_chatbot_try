"""Character windows, preserving outer page/heading boundaries and offsets."""
import json
from uuid import UUID, uuid5
from .storage import fingerprint


def chunk(blocks, config, version_id):
    size, overlap = config["chunk_size"], config["chunk_overlap"]
    if size <= 0 or not 0 <= overlap < size:
        raise ValueError("Invalid chunk size/overlap")
    groups = []
    for block in blocks:
        if not block["text"].strip():
            continue
        if not groups or block.get("page") or groups[-1][-1].get("page") or block.get("heading") != groups[-1][-1].get("heading"):
            groups.append([])
        groups[-1].append(block)
    result = []
    for group in groups:
        text = "\n\n".join(b["text"] for b in group)
        spans, cursor = [], 0
        for b in group:
            spans.append((cursor, cursor + len(b["text"]), b))
            cursor += len(b["text"]) + 2
        start = 0
        while start < len(text):
            end = min(start + size, len(text))
            included = [b for lo, hi, b in spans if hi > start and lo < end]
            methods = {b.get("method", "native") for b in included}
            extraction_method = "ocr_proposal" if "ocr_proposal" in methods else "ocr" if "ocr" in methods else "native"
            metadata = dict(block_start=included[0]["block"], block_end=included[-1]["block"], heading=group[0].get("heading", ""),
                extraction_method=extraction_method, char_start=start, char_end=end,
                partial_table=any(b.get("kind") == "table" and (lo < start or hi > end) for lo, hi, b in spans if b in included))
            source_regions = [b["source_region"] for b in included if b.get("source_region")]
            if source_regions:
                # Chroma metadata accepts scalar values only; preserve crop provenance as JSON text.
                metadata["source_regions_json"] = json.dumps(source_regions, ensure_ascii=False, separators=(",", ":"))
            if group[0].get("page"):
                metadata.update(page_start=group[0]["page"], page_end=group[-1]["page"])
            result.append(dict(text=text[start:end], metadata=metadata))
            if end == len(text):
                break
            start = end - overlap
    return assign_ids(result, version_id, config)


def assign_ids(chunks, version_id, config):
    for ordinal, item in enumerate(chunks):
        item["id"] = str(uuid5(UUID(version_id), f"{fingerprint(config)}:{ordinal}"))
        item["metadata"].update(chunk_ordinal=ordinal, content_hash=fingerprint(item["text"]))
    return chunks
