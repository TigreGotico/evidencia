"""Embeddings kept in an OPM EmbeddingsDB plugin instead of in memory."""
import numpy as np
import pytest

from conftest import DOC, StubChat, StubDecision
from evidencia import Config, Evidencia
from evidencia import pipeline as pipeline_module
from evidencia.index import Collection, db_collection_name
from test_embedding_index import OTHER, CountingEmbedder


class MemoryDB:
    """An EmbeddingsDB that behaves as the chromadb plugin does: a collection must be created before it is used, a
    key added again replaces its row, and results come best first."""
    instances = []

    def __init__(self, config=None):
        self.config = config or {}
        self.collections: dict[str, dict[str, np.ndarray]] = {}
        self.queries = []
        MemoryDB.instances.append(self)

    def create_collection(self, name, metadata=None):
        self.collections.setdefault(name, {})

    def rows(self, name):
        if name not in self.collections:
            raise ValueError(f"Collection '{name}' not found")
        return self.collections[name]

    def add_embeddings_batch(self, keys, embeddings, metadata=None, collection_name=None):
        rows = self.rows(collection_name)
        for key, vector in zip(keys, embeddings):
            rows[key] = np.asarray(vector, dtype=np.float32)

    def get_embeddings_batch(self, keys, collection_name=None, return_metadata=False):
        rows = self.rows(collection_name)
        return [(key, rows[key]) for key in keys if key in rows]

    def delete_embeddings_batch(self, keys, collection_name=None):
        rows = self.rows(collection_name)
        for key in keys:
            rows.pop(key, None)

    def delete_collection(self, name):
        self.collections.pop(name, None)

    def count_embeddings_in_collection(self, collection_name=None):
        return len(self.rows(collection_name))

    def query(self, embeddings, top_k=5, return_metadata=False, collection_name=None):
        self.queries.append(top_k)
        scored = sorted(((key, float(np.linalg.norm(v - embeddings))) for key, v in self.rows(collection_name).items()),
                        key=lambda item: item[1])
        return scored[:top_k]


class DuplicatingDB(MemoryDB):
    """Stores a key added again as a second row, as the qdrant plugin did with a random point id per add."""

    def add_embeddings_batch(self, keys, embeddings, metadata=None, collection_name=None):
        rows = self.rows(collection_name)
        for key, vector in zip(keys, embeddings):
            rows[(key, len(rows))] = np.asarray(vector, dtype=np.float32)

    def get_embeddings_batch(self, keys, collection_name=None, return_metadata=False):
        found = {}
        for (key, _), vector in self.rows(collection_name).items():
            if key in keys:
                found.setdefault(key, vector)
        return list(found.items())

    def delete_embeddings_batch(self, keys, collection_name=None):
        rows = self.rows(collection_name)
        for row in [row for row in rows if row[0] in keys]:
            del rows[row]

    def query(self, embeddings, top_k=5, return_metadata=False, collection_name=None):
        return [(key, d) for (key, _), d in super().query(embeddings, top_k, return_metadata, collection_name)]


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


def test_below_the_query_size_the_database_gives_the_same_ranking_as_the_vectors_in_memory():
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


def test_a_restart_removes_rows_that_no_passage_has_without_embedding_again():
    col, embed, db = with_db()
    col.add(DOC, "geo.txt")
    ghosts = [f"fantasma-{i}" for i in range(300)]
    db.add_embeddings_batch(ghosts, list(embed(["capital de Portugal"] * 300)), collection_name="geo-db")
    hits = db.query(embed(["capital de Portugal"])[0], top_k=200, collection_name="geo-db")
    assert {key for key, _ in hits}.isdisjoint(p.id for p in col.passages)     # the ghosts fill the 200 hits
    counting = CountingEmbedder("m")
    again = Collection("geo", passages=col.passages, embed=counting, db=db, db_name="geo-db")
    assert counting.texts == []
    assert sorted(db.collections["geo-db"]) == sorted(p.id for p in col.passages)
    memory = Collection("geo", passages=col.passages, embed=HashEmbedder("m"))
    query = "capital de Portugal"
    again.embed = memory.embed
    assert [(p.id, p.score) for p in again.search(query, 4)] == [(p.id, p.score) for p in memory.search(query, 4)]


def test_a_database_that_stores_a_key_twice_ranks_each_passage_once():
    memory = Collection("geo", embed=HashEmbedder("m"))
    stored, _, db = with_db(db=DuplicatingDB())
    for col in (memory, stored):
        col.add(DOC, "geo.txt")
    db.add_embeddings_batch([p.id for p in stored.passages] * 3,
                            [db.collections["geo-db"][row] for row in list(db.collections["geo-db"])] * 3,
                            collection_name="geo-db")
    for query in ("capital de Portugal", "Faro Algarve"):
        assert [(p.id, p.score) for p in stored.search(query, 4)] == [(p.id, p.score) for p in memory.search(query, 4)]


def test_a_restart_rebuilds_a_database_that_stores_a_key_twice():
    col, _, db = with_db(db=DuplicatingDB())
    col.add(DOC, "geo.txt")
    col.add(DOC, "geo.txt")
    assert len(db.collections["geo-db"]) == 8
    Collection("geo", passages=col.passages, embed=CountingEmbedder("m"), db=db, db_name="geo-db")
    assert sorted(key for key, _ in db.collections["geo-db"]) == sorted(p.id for p in col.passages)


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
    name = db_collection_name("geo", e.embed.id, (tmp_path / "instance.id").read_text())
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


def test_a_drop_holds_the_collections_lock_until_the_rows_are_gone(tmp_path, plugins):
    e = engine(tmp_path)
    e.add_document("geo", DOC, "geo.txt")
    db = MemoryDB.instances[-1]
    held = []
    delete = db.delete_collection
    db.delete_collection = lambda name: held.append(e._collections_lock.locked()) or delete(name)
    e.drop_collection("geo")
    assert held == [True]


def test_the_namespace_follows_the_directory_and_not_its_path(tmp_path, plugins, monkeypatch):
    first = engine(tmp_path / "collections")
    first.add_document("geo", DOC, "geo.txt")
    db = MemoryDB.instances[-1]
    monkeypatch.setattr(pipeline_module, "load_embeddings_db_plugin", lambda name: lambda config: db)
    (tmp_path / "collections").rename(tmp_path / "moved")
    moved = engine(tmp_path / "moved")
    assert moved.db_namespace == first.db_namespace and moved.embed.texts == []
    other = engine(tmp_path / "collections")       # another container's directory at the same path
    other.add_document("geo", DOC, "geo.txt")
    assert other.db_namespace != first.db_namespace
    engine(tmp_path / "collections")               # a restart of the other service rebuilds only its own rows
    assert sorted(len(rows) for rows in db.collections.values()) == [4, 4]


def test_two_services_on_one_database_keep_their_collections_apart(tmp_path, plugins, monkeypatch):
    first = engine(tmp_path / "a")
    db = MemoryDB.instances[-1]
    monkeypatch.setattr(pipeline_module, "load_embeddings_db_plugin", lambda name: lambda config: db)
    second = engine(tmp_path / "b")
    first.add_document("geo", DOC, "geo.txt")
    second.add_document("geo", DOC, "geo.txt")
    second.drop_collection("geo")
    assert len(db.collections) == 1
    assert sorted(next(iter(db.collections.values()))) == sorted(p.id for p in first.collection("geo").passages)


def test_the_database_collection_name_changes_with_the_embedder():
    a, b = db_collection_name("geo", "model2vec:a"), db_collection_name("geo", "model2vec:b")
    assert a != b and a.startswith("evidencia-") and len(a) <= 63
    assert db_collection_name("geo", "m", "/srv/a") != db_collection_name("geo", "m", "/srv/b")
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
