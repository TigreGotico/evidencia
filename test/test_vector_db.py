"""Embeddings kept in an OPM EmbeddingsDB plugin instead of in memory."""
import numpy as np
import pytest

from conftest import DOC, StubChat, StubDecision
from evidencia import Config, Evidencia
from evidencia import pipeline as pipeline_module
from evidencia.index import Collection, db_collection_name
from test_embedding_index import OTHER, CountingEmbedder


class MemoryDB:
    """An EmbeddingsDB with the template's behaviour: a key added again replaces its row; results best first."""
    instances = []

    def __init__(self, config=None):
        self.config = config or {}
        self.collections: dict[str, dict[str, np.ndarray]] = {}
        self.queries = []
        MemoryDB.instances.append(self)

    def add_embeddings_batch(self, keys, embeddings, metadata=None, collection_name=None):
        rows = self.collections.setdefault(collection_name, {})
        for key, vector in zip(keys, embeddings):
            rows[key] = np.asarray(vector, dtype=np.float32)

    def get_embeddings_batch(self, keys, collection_name=None, return_metadata=False):
        rows = self.collections.get(collection_name, {})
        return [(key, rows[key]) for key in keys if key in rows]

    def delete_embeddings_batch(self, keys, collection_name=None):
        rows = self.collections.get(collection_name, {})
        for key in keys:
            rows.pop(key, None)

    def delete_collection(self, name):
        self.collections.pop(name, None)

    def query(self, embeddings, top_k=5, return_metadata=False, collection_name=None):
        self.queries.append(top_k)
        rows = self.collections.get(collection_name, {})
        scored = sorted(((key, float(np.linalg.norm(v - embeddings))) for key, v in rows.items()),
                        key=lambda item: item[1])
        return scored[:top_k]


class HashEmbedder(CountingEmbedder):
    """Distinct pseudo-random rows, so no two passages tie in similarity."""

    def __call__(self, texts):
        self.texts += list(texts)
        import hashlib
        rows = [np.random.default_rng(int(hashlib.sha256(t.encode()).hexdigest()[:8], 16)).normal(size=8)
                for t in texts]
        v = np.array(rows, dtype=np.float32)
        return v / np.linalg.norm(v, axis=1, keepdims=True)


def with_db(embed=None, db=None):
    embed = embed or HashEmbedder("m")
    db = db or MemoryDB()
    return Collection("geo", embed=embed, db=db, db_name="geo-db"), embed, db


def test_the_database_gives_the_same_ranking_as_the_vectors_in_memory():
    memory = Collection("geo", embed=HashEmbedder("m"))
    stored, _, db = with_db()
    for col in (memory, stored):
        col.add(DOC, "geo.txt")
        col.add(OTHER, "outro.txt")
    for query in ("capital de Portugal", "Faro Algarve", "universidades antigas"):
        assert [(p.id, p.score) for p in stored.search(query, 6)] == [(p.id, p.score) for p in memory.search(query, 6)]
    assert stored.vectors is None
    assert sorted(db.collections["geo-db"]) == sorted(p.id for p in stored.passages)


def test_a_removed_or_replaced_source_leaves_no_rows_in_the_database():
    col, _, db = with_db()
    col.add(DOC, "geo.txt")
    col.add(OTHER, "outro.txt")
    col.remove("outro.txt")
    col.add("Lisboa é a capital.\n\nO Tejo passa em Lisboa.", "geo.txt")
    assert sorted(db.collections["geo-db"]) == sorted(p.id for p in col.passages)
    assert len(col.passages) == 2


def test_a_restart_embeds_only_the_passages_the_database_lacks():
    col, _, db = with_db()
    col.add(DOC, "geo.txt")
    missing = col.passages[0]
    db.delete_embeddings_batch([missing.id], collection_name="geo-db")
    embed = CountingEmbedder("m")
    Collection("geo", passages=col.passages, embed=embed, db=db, db_name="geo-db")
    assert embed.texts == [missing.text]
    assert sorted(db.collections["geo-db"]) == sorted(p.id for p in col.passages)


def test_rows_the_passages_do_not_have_are_not_returned():
    col, embed, db = with_db()
    col.add(DOC, "geo.txt")
    db.add_embeddings_batch(["fantasma"], [embed(["capital de Portugal"])[0]], collection_name="geo-db")
    assert "fantasma" not in {p.id for p in col.search("capital de Portugal", 10)}
    assert len(col.search("capital de Portugal", 10)) == 4


def test_the_database_query_is_bounded():
    col, _, db = with_db()
    col.add("\n\n".join(f"Parágrafo {i} sobre Lisboa." for i in range(300)), "big.txt")
    col.search("Lisboa", 5)
    assert db.queries == [200]


@pytest.fixture
def plugins(monkeypatch):
    names = []
    monkeypatch.setattr(pipeline_module, "Embedder", CountingEmbedder)
    monkeypatch.setattr(pipeline_module, "load_embeddings_db_plugin", lambda name: names.append(name) or MemoryDB)
    return names


def engine(tmp_path, **cfg):
    return Evidencia(Config(data_dir=str(tmp_path), embedding_model="m", vector_db="ovos-memory-db", **cfg),
                     decision=StubDecision(), chat=StubChat(), sources={})


def test_the_engine_loads_the_plugin_and_keeps_its_files_in_the_data_dir(tmp_path, plugins):
    e = engine(tmp_path, vector_db_config={"distance_metric": "cosine"})
    e.add_document("geo", DOC, "geo.txt")
    db = MemoryDB.instances[-1]
    assert plugins == ["ovos-memory-db"]
    assert db.config == {"distance_metric": "cosine", "path": str(tmp_path / "vectors"), "vector_size": 3}
    name = db_collection_name("geo", e.embed.id)
    assert sorted(db.collections[name]) == sorted(p.id for p in e.collection("geo").passages)
    assert not list(tmp_path.glob("*.vectors.npz"))


def test_a_path_or_host_in_the_config_is_kept(tmp_path, plugins):
    engine(tmp_path, vector_db_config={"host": "qdrant.lan", "vector_size": 3})
    assert MemoryDB.instances[-1].config == {"host": "qdrant.lan", "vector_size": 3}


def test_a_restart_with_the_database_embeds_nothing(tmp_path, plugins, monkeypatch):
    first = engine(tmp_path)
    first.add_document("geo", DOC, "geo.txt")
    db = MemoryDB.instances[-1]     # the same database, as a server or a path on disk gives it
    monkeypatch.setattr(pipeline_module, "load_embeddings_db_plugin", lambda name: lambda config: db)
    second = engine(tmp_path)
    assert second.embed.texts == []
    assert second.collection("geo").search("capital de Portugal", 4) == first.collection("geo").search(
        "capital de Portugal", 4)


def test_dropping_a_collection_drops_its_database_collection(tmp_path, plugins):
    e = engine(tmp_path)
    e.add_document("geo", DOC, "geo.txt")
    db = MemoryDB.instances[-1]
    e.drop_collection("geo")
    assert db.collections == {}


def test_the_database_collection_name_changes_with_the_embedder():
    a, b = db_collection_name("geo", "model2vec:a"), db_collection_name("geo", "model2vec:b")
    assert a != b and a.startswith("evidencia-") and len(a) <= 63
    assert db_collection_name("Coleção de Évora / 2", "x").replace("-", "").isalnum()


def test_an_unknown_vector_db_plugin_stops_the_start(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_module, "Embedder", CountingEmbedder)
    monkeypatch.setattr(pipeline_module, "load_embeddings_db_plugin", lambda name: None)
    with pytest.raises(ValueError, match="ovos-nada-db"):
        Evidencia(Config(data_dir=str(tmp_path), embedding_model="m", vector_db="ovos-nada-db"),
                  decision=StubDecision(), chat=StubChat(), sources={})


def test_a_vector_db_needs_embeddings_and_a_place_to_keep_them():
    with pytest.raises(ValueError, match="vector_db needs embedding_model or embedder"):
        Config(vector_db="ovos-chromadb-embeddings-plugin", data_dir="d")
    with pytest.raises(ValueError, match="vector_db needs data_dir"):
        Config(vector_db="ovos-chromadb-embeddings-plugin", embedding_model="m")
    assert Config(vector_db="p", embedding_model="m", vector_db_config={"host": "h"}).vector_db == "p"
