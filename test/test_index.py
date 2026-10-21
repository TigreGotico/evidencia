import pytest
import requests

from conftest import DOC
from evidencia import Collection
from evidencia.index import chunk


@pytest.fixture
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a splitter tried to fetch a URL")
    monkeypatch.setattr(requests, "get", refuse)


def test_markdown_keeps_every_paragraph_under_its_heading():
    md = "# Título\n\nTexto inicial.\n\n## Secção\n\nTexto da secção.\n\n- item um\n- item dois"
    assert chunk(md, "markdown") == ["Título\nTexto inicial.", "Secção\nTexto da secção.",
                                     "Secção\n- item um\n- item dois"]


def test_html_content_starting_with_http_is_not_fetched(no_network):
    html = "http://169.254.169.254/latest <p>um parágrafo</p>"
    assert chunk(html, "html") == ["http://169.254.169.254/latest", "um parágrafo"]


def test_html_content_naming_a_local_file_is_not_read(tmp_path):
    secret = tmp_path / "secret.html"
    secret.write_text("<p>conteúdo secreto</p>")
    assert chunk(str(secret), "html") == [str(secret)]


def test_html_keeps_short_lines_and_drops_scripts():
    html = ("<html><head><title>t</title><style>p {}</style></head><body>"
            "<p>Preço: 5 euros.</p><div>Aceita cookies? Sim.</div><script>alert(1)</script>"
            "<ul><li>um</li><li>dois &amp; três</li></ul></body></html>")
    assert chunk(html, "html") == ["Preço: 5 euros.", "Aceita cookies? Sim.", "um", "dois & três"]


def test_long_paragraph_is_split_between_words():
    from evidencia.index import MAX_PASSAGE_CHARS
    text = " ".join(f"palavra{i}" for i in range(2000)) + " " + "x" * (MAX_PASSAGE_CHARS + 10)
    pieces = chunk(text, "text")
    assert max(len(p) for p in pieces) <= MAX_PASSAGE_CHARS
    assert " ".join(pieces).split() == text.split()[:-1] + ["x" * MAX_PASSAGE_CHARS, "x" * 10]


def test_same_text_in_two_sources_is_returned_for_each():
    col = Collection("dup")
    col.add("Lisboa é a capital.", "a.txt")
    col.add("Lisboa é a capital.", "b.txt")
    hits = col.search("capital", 4)
    assert sorted(h.source for h in hits) == ["a.txt", "b.txt"]
    assert len({h.id for h in hits}) == 2


def test_malformed_pdf_is_a_value_error():
    with pytest.raises(ValueError, match="could not read the pdf"):
        chunk(b"%PDF-1.4 not really a pdf", "pdf")


def test_text_splits_on_blank_lines():
    assert chunk(DOC, "text")[0] == "Lisboa é a capital de Portugal desde o século XIII."
    assert len(chunk(DOC, "text")) == 4


def test_unknown_format_is_rejected():
    with pytest.raises(ValueError):
        chunk("x", "docx")


def test_search_ranks_and_clamps_k():
    col = Collection("t")
    col.add(DOC, "geo")
    hits = col.search("capital de Portugal", 100)
    assert hits[0].text.startswith("Lisboa")
    assert len(hits) <= 4
    assert hits == sorted(hits, key=lambda p: p.score, reverse=True)


def test_no_shared_term_returns_nothing():
    col = Collection("t")
    col.add(DOC, "geo")
    assert col.search("xilofone", 5) == []


def test_readding_a_source_replaces_it():
    col = Collection("t")
    col.add(DOC, "geo")
    col.add("Faro fica no Algarve.", "geo")
    assert [p.text for p in col.passages] == ["Faro fica no Algarve."]
    assert col.search("Lisboa", 5) == []


def test_remove_source():
    col = Collection("t")
    col.add(DOC, "a")
    col.add("Faro fica no Algarve.", "b")
    assert col.remove("a") == 4
    assert col.search("Faro", 5)[0].source == "b"
    assert col.remove("a") == 0


def test_empty_document_is_rejected():
    with pytest.raises(ValueError):
        Collection("t").add("   \n\n  ", "vazio")
