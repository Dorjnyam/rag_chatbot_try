"""Chroma owns vectors; PostgreSQL decides which versions may be queried."""
from flask import current_app
from .embedding_contract import collection_contract_metadata, validate_collection_metadata, validate_vectors
from .storage import root


class VectorStore:
    def __init__(self):
        import chromadb
        from chromadb.config import Settings
        self.client = chromadb.PersistentClient(path=str(root() / "chroma"), settings=Settings(anonymized_telemetry=False))

    def collection(self, name, create=False, metadata=None):
        if create:
            # Flush each mutation: tiny local collections must remain searchable
            # after the processing CLI exits, before default thresholds are met.
            return self.client.get_or_create_collection(name, embedding_function=None, metadata=metadata,
                configuration={"hnsw": {"space": "cosine", "batch_size": 1, "sync_threshold": 1}})
        return self.client.get_collection(name, embedding_function=None)

    def upsert(self, name, chunks, vectors, embedding_config=None):
        if len(chunks) != len(vectors):
            raise ValueError("Embedding vector count does not match chunk count.")
        metadata = collection_contract_metadata(embedding_config) if embedding_config is not None else None
        if embedding_config is not None:
            validate_vectors(vectors, metadata["embedding_dimensions"], metadata["embedding_normalization"])
            for item in chunks:
                if item.get("metadata", {}).get("embedding_contract_sha256") != metadata["embedding_contract_sha256"]:
                    raise ValueError("Chunk embedding identity does not match the collection contract.")
        collection = self.collection(name, create=True, metadata=metadata)
        if embedding_config is not None:
            validate_collection_metadata(collection.metadata, embedding_config)
        collection.upsert(ids=[c["id"] for c in chunks], documents=[c["text"] for c in chunks], metadatas=[c["metadata"] for c in chunks], embeddings=vectors)

    def ids(self, name, version_id):
        from chromadb.errors import NotFoundError
        try:
            return set(self.collection(name).get(where={"version_id": version_id}, include=[])["ids"])
        except NotFoundError:
            return set()

    def query(self, name, vector, version_ids, k, embedding_config=None):
        if not version_ids:
            return []
        collection = self.collection(name)
        if embedding_config is not None:
            observed = (collection.metadata or {}).get("embedding_contract_sha256")
            if observed and observed != collection_contract_metadata(embedding_config)["embedding_contract_sha256"]:
                raise ValueError("Chroma collection embedding contract differs from the active document version.")
            if embedding_config.get("embedding_contract_sha256") and not observed:
                raise ValueError("Chroma collection is missing its embedding contract; refusing retrieval.")
        data = collection.query(query_embeddings=[vector], n_results=k, where={"version_id": {"$in": version_ids}}, include=["documents", "metadatas", "distances"])
        return [dict(id=id_, text=text, metadata=meta, distance=distance) for id_, text, meta, distance in zip(data["ids"][0], data["documents"][0], data["metadatas"][0], data["distances"][0])]

    def delete_version(self, name, version_id):
        from chromadb.errors import NotFoundError
        try:
            self.collection(name).delete(where={"version_id": version_id})
        except NotFoundError:
            pass

    def all_records(self):
        records = []
        for collection in self.client.list_collections():
            data = self.collection(collection.name).get(include=["metadatas"])
            records.extend((collection.name, id_, meta) for id_, meta in zip(data["ids"], data["metadatas"]))
        return records


def store():
    if "VECTOR_STORE" in current_app.config:
        return current_app.config["VECTOR_STORE"]
    if "vector_store" not in current_app.extensions:
        current_app.extensions["vector_store"] = VectorStore()
    return current_app.extensions["vector_store"]
