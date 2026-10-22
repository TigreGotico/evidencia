"""Embedders by plugin, the incremental index, and the vectors kept on disk."""
import threading
import time

import numpy as np
import pytest

from conftest import DOC, StubChat, StubDecision
from evidencia import Config, Evidencia
from evidencia import embeddings as embeddings_module
from evidencia import pipeline as pipeline_module
from evidencia.index import Collection

OTHER = "Évora tem um templo romano.\n\nFaro fica no Algarve."


class CountingEmbedder:
    """A deterministic embedder that records every text it embeds."""

    def __init__(self, model):
        self.id = f"counting:{model}"
        self.texts = []
        self.loads = 0

    def load(self):
        self.loads += 1

    def __call__(self, texts):
        self.texts += list(texts)
        rows = [[len(t) % 7 + 1.0, t.count("a") + 1.0, 1.0] for t in texts]
        v = np.array(rows, dtype=np.float32)
        return v / np.linalg.norm(v, axis=1, keepdims=True)


@pytest.fixture
def counting(monkeypatch):
    made = []

    def make(model):
        made.append(CountingEmbedder(model))
        return made[-1]
    monkeypatch.setattr(pipeline_module, "Embedder", make)
    return made


def engine(tmp_path, **cfg):
    return Evidencia(Config(data_dir=str(tmp_path), embedding_model="m", **cfg), decision=StubDecision(),
                     chat=StubChat(), sources={})


def test_an_add_embeds_only_the_new_passages():
    embed = CountingEmbedder("m")
    col = Collection("geo", embed=embed)
    col.add(DOC, "geo.txt")
    embed.texts.clear()
    col.add(OTHER, "outro.txt")
    assert embed.texts == ["Évora tem um templo romano.", "Faro fica no Algarve."]


def test_a_removed_source_leaves_no_vectors_and_search_still_matches_the_passages():
    embed = CountingEmbedder("m")
    col = Collection("geo", embed=embed)
    col.add(DOC, "geo.txt")
    col.add(OTHER, "outro.txt")
    col.remove("geo.txt")
    embed.texts.clear()
    assert len(col.vectors) == len(col.passages) == 2
    fresh = Collection("geo", embed=CountingEmbedder("m"))
    fresh.add(OTHER, "outro.txt")
    assert np.allclose(col.vectors, fresh.vectors)
    assert [p.text for p in col.search("Faro Algarve", 2)] == [p.text for p in fresh.search("Faro Algarve", 2)]


def test_a_restart_loads_the_stored_vectors_and_embeds_nothing(tmp_path, counting):
    first = engine(tmp_path)
    first.add_document("geo", DOC, "geo.txt")
    before = first.collection("geo").search("capital de Portugal", 4)
    second = engine(tmp_path)
    assert counting[-1].texts == []
    assert second.collection("geo").search("capital de Portugal", 4) == before
    assert counting[-1].texts == ["capital de Portugal"]


def test_the_model_loads_at_start_even_when_no_text_needs_embedding(tmp_path, counting):
    engine(tmp_path).add_document("geo", DOC, "geo.txt")
    engine(tmp_path)
    assert counting[-1].loads == 1 and counting[-1].texts == []


def test_a_model_that_cannot_load_stops_the_start_when_the_vectors_are_stored(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_module, "Embedder", CountingEmbedder)
    engine(tmp_path).add_document("geo", DOC, "geo.txt")

    class Offline(CountingEmbedder):
        def load(self):
            raise OSError("LocalEntryNotFoundError: cannot reach the Hub")
    monkeypatch.setattr(pipeline_module, "Embedder", Offline)
    with pytest.raises(OSError, match="cannot reach the Hub"):
        engine(tmp_path)


def test_a_different_embedder_re_embeds_on_restart(tmp_path, counting):
    engine(tmp_path).add_document("geo", DOC, "geo.txt")
    Evidencia(Config(data_dir=str(tmp_path), embedding_model="outro"), decision=StubDecision(), chat=StubChat(),
              sources={})
    assert len(counting[-1].texts) == 4


def test_vectors_that_do_not_match_the_passages_are_rebuilt(tmp_path, counting):
    engine(tmp_path).add_document("geo", DOC, "geo.txt")
    (vectors,) = tmp_path.glob("*.vectors.npy")
    np.save(vectors, np.zeros((2, 3), dtype=np.float32))
    engine(tmp_path)
    assert len(counting[-1].texts) == 4


def test_a_dropped_collection_leaves_no_vector_file(tmp_path, counting):
    e = engine(tmp_path)
    e.add_document("geo", DOC, "geo.txt")
    assert list(tmp_path.glob("*.vectors.npy"))
    e.drop_collection("geo")
    assert list(tmp_path.iterdir()) == []


class SlowEmbedder(CountingEmbedder):
    def __init__(self):
        super().__init__("lento")
        self.embedding, self.release = threading.Event(), threading.Event()

    def __call__(self, texts):
        if len(texts) > 1:      # a document, not a query
            self.embedding.set()
            self.release.wait(5)
        return super().__call__(texts)


def test_a_search_does_not_wait_for_an_add_that_is_embedding():
    embed = SlowEmbedder()
    col = Collection("geo", embed=embed)
    embed.release.set()
    col.add(DOC, "geo.txt")
    embed.release.clear()
    embed.embedding.clear()
    worker = threading.Thread(target=col.add, args=(OTHER, "outro.txt"))
    worker.start()
    assert embed.embedding.wait(5)
    started = time.monotonic()
    hits = col.search("capital de Portugal", 10)
    waited = time.monotonic() - started
    embed.release.set()
    worker.join(5)
    assert waited < 1.0
    # the search ran on the index from before the add
    assert {h.source for h in hits} == {"geo.txt"} and len(hits) == 4
    assert {p.source for p in col.passages} == {"geo.txt", "outro.txt"}


def test_a_new_collection_is_embedded_outside_the_engine_lock(tmp_path, monkeypatch):
    embed = SlowEmbedder()
    monkeypatch.setattr(pipeline_module, "Embedder", lambda model: embed)
    e = engine(tmp_path)
    worker = threading.Thread(target=e.add_document, args=("geo", DOC, "geo.txt"))
    worker.start()
    assert embed.embedding.wait(5)
    started = time.monotonic()
    snapshot = e.collections_snapshot()
    waited = time.monotonic() - started
    embed.release.set()
    worker.join(5)
    assert waited < 1.0 and snapshot == {}
    assert len(e.collection("geo").passages) == 4


class FakeTextEmbedder:
    """The OPM TextEmbedder surface that evidencia uses."""
    instances = []

    def __init__(self, config=None):
        self.config = config or {}
        self.batches = []
        FakeTextEmbedder.instances.append(self)

    def get_embeddings_batch(self, texts):
        self.batches.append(list(texts))
        return [np.array([3.0, 4.0, float(len(t))], dtype=np.float32) for t in texts]


def test_an_embedder_plugin_is_loaded_by_name_and_called_once_per_batch(monkeypatch):
    names = []
    monkeypatch.setattr(embeddings_module, "load_text_embeddings_plugin",
                        lambda name: names.append(name) or FakeTextEmbedder)
    embed = embeddings_module.PluginEmbedder("ovos-fake-embeddings-plugin", {"model": "x"})
    vectors = embed(["um", "dois"])
    assert names == ["ovos-fake-embeddings-plugin"]
    assert FakeTextEmbedder.instances[-1].config == {"model": "x"}
    assert FakeTextEmbedder.instances[-1].batches == [["um", "dois"]]
    assert vectors.dtype == np.float32
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1.0)
    assert np.allclose(vectors[0], np.array([3, 4, 2]) / np.sqrt(29))


def test_an_unknown_embedder_plugin_stops_the_start(monkeypatch):
    monkeypatch.setattr(embeddings_module, "load_text_embeddings_plugin", lambda name: None)
    with pytest.raises(ValueError, match="ovos-nada-plugin"):
        embeddings_module.PluginEmbedder("ovos-nada-plugin", {})


def test_the_embedder_id_names_the_plugin_and_its_config(monkeypatch):
    monkeypatch.setattr(embeddings_module, "load_text_embeddings_plugin", lambda name: FakeTextEmbedder)
    a = embeddings_module.PluginEmbedder("p", {"model": "x"})
    b = embeddings_module.PluginEmbedder("p", {"model": "y"})
    assert a.id != b.id and a.id.startswith("p:")
    assert embeddings_module.Embedder("minishlab/potion").id == "model2vec:minishlab/potion"


def test_embedder_and_embedding_model_are_not_both_set():
    with pytest.raises(ValueError, match="embedder or embedding_model"):
        Config(embedder="ovos-fake-embeddings-plugin", embedding_model="m")


def test_dedupe_accepts_an_embedder_plugin():
    assert Config(dedupe_threshold=0.9, embedder="ovos-fake-embeddings-plugin").embedder_config == {}
