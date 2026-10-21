import json

import pytest
import requests

from evidencia.gates import load_locale
from evidencia.sources import (CATALOG, MAX_REMOTE_BYTES, ArquivoPt, DicionarioAberto, SourceUnavailable,
                               Wikidata, Wikipedia, build_sources)

STOPWORDS = frozenset(load_locale("pt-PT")["stopwords"])


class FakeResponse:
    def __init__(self, body: bytes, status: int = 200):
        self.body, self.status = body, status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        if self.status >= 400:
            raise requests.HTTPError(f"{self.status}")

    def iter_content(self, size):
        for start in range(0, len(self.body), size):
            yield self.body[start:start + size]


class FakeHttp:
    """Routes a GET to ``route(url, params)``, which returns a JSON-able value, bytes, or an exception."""

    def __init__(self, route):
        self.route = route
        self.calls = []

    def get(self, url, params=None, timeout=None, stream=False):
        self.calls.append((url, dict(params or {})))
        out = self.route(url, dict(params or {}))
        if isinstance(out, Exception):
            raise out
        if isinstance(out, FakeResponse):
            return out
        return FakeResponse(out if isinstance(out, bytes) else json.dumps(out).encode())


LISBOA = ("Lisboa é a capital e a maior cidade de Portugal.\n\n"
          "== História ==\nA cidade foi tomada aos mouros em 1147.\n\n"
          "== Geografia ==\nLisboa fica na margem norte do rio Tejo.")


def wiki_route(url, params):
    if "titles" in params:
        ids = {"Lisboa": 1, "Porto": 2}
        return {"query": {"pages": [{"pageid": ids[t], "title": t} if t in ids else {"title": t, "missing": True}
                                    for t in params["titles"].split("|")]}}
    if params.get("list") == "search":
        return {"query": {"search": [{"pageid": 1, "title": "Lisboa"}, {"pageid": 2, "title": "Porto"}]}}
    pages = {1: ("Lisboa", LISBOA), 2: ("Porto", "O Porto fica no norte de Portugal.")}
    title, text = pages[int(params["pageids"])]
    return {"query": {"pages": [{"pageid": int(params["pageids"]), "title": title, "extract": text,
                                 "fullurl": f"https://pt.wikipedia.org/wiki/{title}"}]}}


def test_terms_drop_the_question_frame():
    source = Wikipedia(FakeHttp(wiki_route), STOPWORDS)
    assert source.terms("Qual é a capital de Portugal?") == ["capital", "portugal"]
    assert source.terms("O que significa saudade?") == ["saudade"]
    # a question of stopwords only still searches for something
    assert source.terms("O que é isso?") == ["o", "que", "é", "isso"]


def test_wikipedia_searches_the_content_words_and_cites_each_page():
    http = FakeHttp(wiki_route)
    hits = Wikipedia(http, STOPWORDS).search("Quando foi tomada Lisboa aos mouros?", 2)
    assert [c["srsearch"] for _, c in http.calls if "srsearch" in c] == ["tomada lisboa mouros"]
    assert hits[0].text == "Lisboa: História\nA cidade foi tomada aos mouros em 1147."
    assert (hits[0].source, hits[0].url, hits[0].origin) == ("Lisboa", "https://pt.wikipedia.org/wiki/Lisboa",
                                                             "pt.wikipedia")


def test_the_page_named_in_the_question_comes_before_the_keyword_hits():
    def route(url, params):
        if params.get("list") == "search":
            return {"query": {"search": [{"pageid": 2, "title": "Porto"}]}}
        return wiki_route(url, params)
    http = FakeHttp(route)
    hits = Wikipedia(http, STOPWORDS).search("Quantos habitantes tem Lisboa?", 4)
    assert [c["titles"] for _, c in http.calls if "titles" in c] == ["Lisboa"]
    assert [c["pageids"] for _, c in http.calls if "pageids" in c] == [1, 2]
    assert hits[0].source == "Lisboa"


def test_names_are_the_capitalised_runs_without_the_question_word():
    source = Wikipedia(FakeHttp(wiki_route), STOPWORDS)
    assert source.names("Quando foi construída a Torre de Belém?") == ["Torre de Belém"]
    assert source.names("Onde nasceu Fernando Pessoa, em Lisboa?") == ["Fernando Pessoa", "Lisboa"]
    assert source.names("O que significa saudade?") == []


def test_wikidata_looks_up_the_item_of_a_named_article_first():
    def route(url, params):
        if params.get("list") == "search":
            return {"query": {"search": []}}
        return wikidata_route(url, params)
    http = FakeHttp(route)
    hits = Wikidata(http, STOPWORDS).search("Qual é a capital de Portugal?", 1)
    assert hits[0].url == "https://www.wikidata.org/wiki/Q45"
    assert [(c["sites"], c["titles"]) for _, c in http.calls if "titles" in c] == [("ptwiki", "Portugal")]


def test_a_wider_round_keeps_the_first_passages_and_fetches_nothing_again():
    http = FakeHttp(wiki_route)
    source = Wikipedia(http, STOPWORDS)
    first = source.search("Lisboa Tejo cidade", 1)
    calls = len(http.calls)
    wider = source.search("Lisboa Tejo cidade", 3)
    assert wider[:1] == first
    assert len(wider) == 3
    assert len(http.calls) == calls


def test_repeated_text_in_one_page_is_returned_once():
    def route(url, params):
        if params.get("list") == "search":
            return {"query": {"search": [{"pageid": 7, "title": "Lissabon"}]}}
        text = ("== Alemão ==\n=== Substantivo ===\nLisboa, capital de Portugal\n\n"
                "== Neerlandês ==\n=== Substantivo ===\nLisboa, capital de Portugal")
        return {"query": {"pages": [{"pageid": 7, "title": "Lissabon", "extract": text, "fullurl": "u"}]}}
    hits = Wikipedia(FakeHttp(route), STOPWORDS).search("capital de Portugal", 4)
    assert [h.text for h in hits] == ["Lissabon: Substantivo\nLisboa, capital de Portugal"]


def test_a_network_error_is_source_unavailable_and_is_not_cached():
    state = {"down": True}

    def route(url, params):
        return requests.ConnectionError("no route") if state["down"] else wiki_route(url, params)
    source = Wikipedia(FakeHttp(route), STOPWORDS)
    with pytest.raises(SourceUnavailable):
        source.search("Lisboa", 2)
    state["down"] = False
    assert source.search("Lisboa", 2)


def test_an_http_error_status_is_source_unavailable():
    source = Wikipedia(FakeHttp(lambda url, params: FakeResponse(b"busy", 429)), STOPWORDS)
    with pytest.raises(SourceUnavailable):
        source.search("Lisboa", 2)


def test_a_body_over_the_cap_is_cut_and_unreadable_json_is_source_unavailable():
    big = b'{"query": {"search": [' + b" " * MAX_REMOTE_BYTES + b"]}}"
    http = FakeHttp(lambda url, params: big)
    source = Wikipedia(http, STOPWORDS)
    assert len(source.get("https://pt.wikipedia.org/w/api.php")) == MAX_REMOTE_BYTES
    with pytest.raises(SourceUnavailable):
        source.search("Lisboa", 2)


WIKIDATA = {
    "Q45": {"id": "Q45", "labels": {"pt": {"value": "Portugal"}}, "descriptions": {"pt": {"value": "país na Europa"}},
            "claims": {
                "P36": [{"mainsnak": {"snaktype": "value", "datatype": "wikibase-item",
                                      "datavalue": {"type": "wikibase-entityid", "value": {"id": "Q597"}}}}],
                "P1082": [{"mainsnak": {"snaktype": "value", "datatype": "quantity",
                                        "datavalue": {"type": "quantity", "value": {"amount": "+10347892",
                                                                                    "unit": "1"}}}}],
                "P571": [{"mainsnak": {"snaktype": "value", "datatype": "time",
                                       "datavalue": {"type": "time", "value": {"time": "+1143-10-05T00:00:00Z",
                                                                               "precision": 11}}}}],
                "P2044": [{"mainsnak": {"snaktype": "value", "datatype": "quantity",
                                        "datavalue": {"type": "quantity", "value": {
                                            "amount": "+1993", "unit": "http://www.wikidata.org/entity/Q11573"}}}}],
                "P214": [{"mainsnak": {"snaktype": "value", "datatype": "external-id",
                                       "datavalue": {"type": "string", "value": "141950880"}}}],
                "P1": [{"mainsnak": {"snaktype": "novalue", "datatype": "wikibase-item"}}],
            }},
}
LABELS = {"Q597": "Lisboa", "P36": "capital", "P1082": "população", "P571": "data de fundação",
          "P2044": "altitude máxima", "Q11573": "metro", "P214": "VIAF"}


def wikidata_route(url, params):
    if "titles" in params:
        return {"entities": {"Q45": {"id": "Q45"}} if "Portugal" in params["titles"].split("|") else {"-1": {}}}
    if params.get("list") == "search":
        return {"query": {"search": [{"title": "Q45"}, {"title": "Property:P36"}]}}
    ids = params["ids"].split("|")
    if params["props"] == "labels":
        return {"entities": {i: {"id": i, "labels": {"pt": {"value": LABELS[i]}}} for i in ids if i in LABELS}}
    return {"entities": {i: WIKIDATA[i] for i in ids}}


def test_wikidata_states_each_fact_as_a_sentence_about_the_item():
    http = FakeHttp(wikidata_route)
    hits = Wikidata(http, STOPWORDS).search("capital de Portugal", 1)
    assert hits[0].text == ("Portugal: país na Europa.\n"
                            "Portugal, capital: Lisboa.\n"
                            "Portugal, população: 10347892.\n"
                            "Portugal, data de fundação: 1143-10-05.\n"
                            "Portugal, altitude máxima: 1993 metro.")
    assert hits[0].url == "https://www.wikidata.org/wiki/Q45"
    # external ids are not facts to answer from, and their properties are never looked up
    assert "P214" not in http.calls[-1][1]["ids"]


def test_wikidata_dates_keep_only_the_precision_they_have():
    source = Wikidata(FakeHttp(wikidata_route), STOPWORDS)
    year = {"datavalue": {"type": "time", "value": {"time": "+1755-01-01T00:00:00Z", "precision": 9}}}
    month = {"datavalue": {"type": "time", "value": {"time": "+1755-11-00T00:00:00Z", "precision": 10}}}
    assert source._render(year, {}) == "1755"
    assert source._render(month, {}) == "1755-11"


DICTIONARY = [{"word_id": 111274, "xml": "<entry id=\"saudade\"><form><orth>Saudade</orth></form><sense><gramGrp>f."
                                         "</gramGrp><def>\nLembrança triste e suave de pessoas ausentes.\nPesar.\n"
                                         "</def></sense></entry>"}]


def test_dicionario_aberto_returns_the_definition_of_each_content_word():
    http = FakeHttp(lambda url, params: DICTIONARY if url.endswith("/saudade") else [])
    hits = DicionarioAberto(http, STOPWORDS).search("O que significa saudade?", 2)
    assert [c[0] for c in http.calls] == ["https://api.dicionario-aberto.net/word/saudade"]
    assert hits[0].text == "Saudade: Lembrança triste e suave de pessoas ausentes. Pesar."
    assert hits[0].url == "https://dicionario-aberto.net/search/saudade"


def test_arquivo_reads_the_extracted_text_and_cites_the_archived_copy():
    def route(url, params):
        if url == ArquivoPt.api:
            return {"response_items": [{"title": "Torre de Belém", "tstamp": "20151125022013",
                                        "linkToArchive": "https://arquivo.pt/wayback/20151125022013/x",
                                        "linkToExtractedText": "https://arquivo.pt/textextracted?x"}]}
        return "Início Monumento. A Torre de Belém foi construída entre 1514 e 1520. Contactos.".encode()
    hits = ArquivoPt(FakeHttp(route), STOPWORDS).search("Quando foi construída a Torre de Belém?", 1)
    assert hits[0].text == "A Torre de Belém foi construída entre 1514 e 1520."
    assert hits[0].source == "Torre de Belém, 2015-11-25"
    assert hits[0].url == "https://arquivo.pt/wayback/20151125022013/x"


def test_only_arquivo_forbids_commercial_use():
    assert {name for name, cls in CATALOG.items() if not cls.commercial} == {"arquivo.pt"}


def test_build_sources_refuses_an_unknown_name_and_sends_the_user_agent():
    with pytest.raises(ValueError, match="unknown sources"):
        build_sources(["pt.wikipedia", "infopedia"], "ua", STOPWORDS, "pt-PT", 5)
    built = build_sources(["wikidata"], "evidencia-test (me@example.org)", STOPWORDS, "pt-PT", 5)
    assert built["wikidata"].http.headers["User-Agent"] == "evidencia-test (me@example.org)"
