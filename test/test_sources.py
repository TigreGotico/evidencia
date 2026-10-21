import json
import time

import pytest
import requests

from evidencia.gates import load_locale
from evidencia.sources import (CATALOG, MAX_REMOTE_BYTES, ArquivoPt, DicionarioAberto, SourceUnavailable,
                               Wikidata, Wikipedia, Wiktionary, build_sources)

STOPWORDS = frozenset(load_locale("pt-PT")["stopwords"])


class FakeResponse:
    def __init__(self, body: bytes, status: int = 200):
        self.body, self.status_code = body, status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}", response=self)

    def iter_content(self, size):
        for start in range(0, len(self.body), size):
            yield self.body[start:start + size]


class FakeHttp:
    """Routes a request to ``route(url, params)``, which returns a JSON-able value, bytes, or an exception.
    For a POST, ``params`` is the JSON body."""

    def __init__(self, route):
        self.route = route
        self.calls = []

    def get(self, url, params=None, timeout=None, stream=False):
        return self._send(url, dict(params or {}))

    def post(self, url, json=None, timeout=None, stream=False):
        return self._send(url, json)

    def _send(self, url, params):
        self.calls.append((url, params))
        out = self.route(url, params)
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


def test_commercial_use_is_cleared_only_for_the_openly_licensed_sources():
    assert {name for name, cls in CATALOG.items() if cls.commercial} == {
        "pt.wikipedia", "pt.wikisource", "pt.wiktionary", "wikidata", "dicionario-aberto", "gleif"}


def test_build_sources_refuses_an_unknown_name_and_sends_the_user_agent():
    with pytest.raises(ValueError, match="unknown sources"):
        build_sources(["pt.wikipedia", "infopedia"], "ua", STOPWORDS, "pt-PT", 5)
    built = build_sources(["wikidata"], "evidencia-test (me@example.org)", STOPWORDS, "pt-PT", 5)
    assert built["wikidata"].http.headers["User-Agent"] == "evidencia-test (me@example.org)"


def test_wiktionary_looks_up_the_entry_of_a_lowercase_word():
    http = FakeHttp(lambda url, params: {"query": {"pages": [], "search": []}})
    Wiktionary(http, STOPWORDS).fetch("O que significa saudade?")
    assert [c["titles"] for _, c in http.calls if "titles" in c] == ["saudade"]


def item(qid, value, rank="normal"):
    return {"mainsnak": {"snaktype": "value", "datatype": "wikibase-item",
                         "datavalue": {"type": "wikibase-entityid", "value": {"id": value}}}, "rank": rank}


def test_wikidata_gives_the_preferred_value_first_drops_deprecated_ones_and_skips_external_ids_before_the_cap():
    external = {f"P{900 + i}": [{"mainsnak": {"snaktype": "value", "datatype": "external-id",
                                              "datavalue": {"type": "string", "value": "x"}}}] for i in range(40)}
    entity = {"id": "Q1", "labels": {"pt": {"value": "Lisboa"}},
              "claims": {**external, "P6": [item("Q1", "Q10"), item("Q1", "Q11", "preferred"), item("Q1", "Q12", "deprecated")]}}
    labels = {"P6": "presidente da câmara", "Q10": "Antigo", "Q11": "Atual", "Q12": "Errado"}
    doc = Wikidata(FakeHttp(lambda url, params: {}), STOPWORDS).document(entity, labels)
    assert doc.text == "Lisboa.\nLisboa, presidente da câmara: Atual, Antigo."


def test_wikidata_asks_for_portuguese_labels_only():
    http = FakeHttp(lambda url, params: {"entities": {}})
    Wikidata(http, STOPWORDS).entities(["Q1"], "labels")
    assert http.calls[0][1]["languages"] == "pt|pt-br|mul"


def test_the_same_question_with_other_spacing_is_fetched_once():
    http = FakeHttp(wiki_route)
    source = Wikipedia(http, STOPWORDS)
    source.search("Lisboa  ", 2)
    calls = len(http.calls)
    source.search(" Lisboa", 2)
    assert len(http.calls) == calls


def test_concurrent_requests_for_one_question_share_one_fetch():
    import threading
    from concurrent.futures import ThreadPoolExecutor
    release = threading.Event()

    def route(url, params):
        release.wait(5)
        return wiki_route(url, params)
    http = FakeHttp(route)
    source = Wikipedia(http, STOPWORDS)
    with ThreadPoolExecutor(4) as pool:
        futures = [pool.submit(source.search, "Lisboa", 2) for _ in range(4)]
        time.sleep(0.2)
        release.set()
        results = [f.result() for f in futures]
    assert len({tuple(p.text for p in r) for r in results}) == 1 and results[0]
    assert len([c for _, c in http.calls if c.get("list") == "search"]) == 1


def test_a_failed_fetch_is_shared_by_the_requests_that_waited_for_it():
    import threading
    from concurrent.futures import ThreadPoolExecutor
    release = threading.Event()

    def route(url, params):
        release.wait(5)
        return requests.ConnectionError("no route")
    http = FakeHttp(route)
    source = Wikipedia(http, STOPWORDS)
    with ThreadPoolExecutor(3) as pool:
        futures = [pool.submit(source.search, "Lisboa", 2) for _ in range(3)]
        time.sleep(0.2)
        release.set()
        for f in futures:
            with pytest.raises(SourceUnavailable):
                f.result()
    assert len(http.calls) == 1


def test_three_failures_in_a_row_stop_requests_for_a_minute(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("evidencia.sources.time.monotonic", lambda: now[0])
    state = {"down": True}
    http = FakeHttp(lambda url, params: requests.ConnectionError("no route") if state["down"] else wiki_route(url, params))
    source = Wikipedia(http, STOPWORDS)
    for question in ("Lisboa", "Porto", "Faro"):
        with pytest.raises(SourceUnavailable, match="ConnectionError"):
            source.search(question, 2)
    calls = len(http.calls)
    state["down"] = False
    with pytest.raises(SourceUnavailable, match="failed 3 times in a row"):
        source.search("Lisboa", 2)
    assert len(http.calls) == calls
    now[0] += 61
    assert source.search("Lisboa", 2)
    assert source.health()["consecutive_failures"] == 0


def test_one_failure_after_the_pause_stops_requests_again(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("evidencia.sources.time.monotonic", lambda: now[0])
    http = FakeHttp(lambda url, params: requests.ConnectionError("no route"))
    source = Wikipedia(http, STOPWORDS)
    for question in ("Lisboa", "Porto", "Faro"):
        with pytest.raises(SourceUnavailable):
            source.search(question, 2)
    now[0] += 61
    with pytest.raises(SourceUnavailable, match="ConnectionError"):
        source.search("Lisboa", 2)
    calls = len(http.calls)
    with pytest.raises(SourceUnavailable, match="failed 4 times in a row"):
        source.search("Porto", 2)
    assert len(http.calls) == calls


def test_health_counts_requests_and_failures_and_keeps_the_last_error():
    state = {"down": False}
    source = Wikipedia(FakeHttp(lambda url, params: requests.ConnectionError("no route") if state["down"]
                                else wiki_route(url, params)), STOPWORDS)
    assert source.health() == {"fetches": 0, "failures": 0, "consecutive_failures": 0, "last_error": None,
                               "last_error_at": None, "last_latency_ms": None, "paused_for_s": 0}
    source.search("Lisboa", 2)
    source.search("Lisboa", 2)     # from the cache: not a fetch
    state["down"] = True
    with pytest.raises(SourceUnavailable):
        source.search("Porto", 2)
    health = source.health()
    assert (health["fetches"], health["failures"], health["consecutive_failures"]) == (2, 1, 1)
    assert health["last_error"] == "pt.wikipedia: ConnectionError"
    assert health["last_error_at"].endswith("Z") and health["last_latency_ms"] >= 0


def test_a_reply_the_source_cannot_be_blamed_for_does_not_pause_it():
    source = Wikipedia(FakeHttp(lambda url, params: {"error": {"code": "cirrussearch-too-long"}}), STOPWORDS)
    for _ in range(4):
        with pytest.raises(SourceUnavailable, match="KeyError"):
            source.search("Lisboa", 2)
    assert source.health()["consecutive_failures"] == 0 and source.health()["paused_for_s"] == 0


@pytest.mark.parametrize("status", [400, 404, 414])
def test_a_client_error_status_does_not_pause_the_source(status):
    source = Wikipedia(FakeHttp(lambda url, params: FakeResponse(b"bad request", status)), STOPWORDS)
    for _ in range(4):
        with pytest.raises(SourceUnavailable, match="HTTPError"):
            source.search("Lisboa", 2)
    assert source.health()["consecutive_failures"] == 0 and source.health()["paused_for_s"] == 0


def test_a_fetch_that_ends_during_the_trial_request_does_not_let_a_second_one_out(monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    now = [1000.0]
    monkeypatch.setattr("evidencia.sources.time.monotonic", lambda: now[0])
    slow, trial = threading.Event(), threading.Event()
    sent = []

    def fetch(question):
        sent.append(question)
        if question == "Slow":
            slow.wait(5)
            raise KeyError("query")
        if question == "Braga":
            trial.wait(5)
        raise requests.ConnectionError("no route")
    source = Wikipedia(FakeHttp(wiki_route), STOPWORDS)
    source.fetch = fetch
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(source.search, "Slow", 2)
        time.sleep(0.1)
        for question in ("Lisboa", "Porto", "Faro"):
            with pytest.raises(SourceUnavailable):
                source.search(question, 2)
        now[0] += 61
        probe = pool.submit(source.search, "Braga", 2)
        time.sleep(0.1)
        slow.set()
        with pytest.raises(SourceUnavailable):
            first.result()
        with pytest.raises(SourceUnavailable, match="paused"):
            source.search("Beja", 2)
        trial.set()
        with pytest.raises(SourceUnavailable):
            probe.result()
    assert "Beja" not in sent


def test_one_word_longer_than_the_search_limit_is_cut():
    assert len(Wikipedia(FakeHttp(wiki_route), STOPWORDS).query("x" * 400)) == 300


def test_wiktionary_looks_up_the_asked_word_before_the_names():
    source = Wiktionary(FakeHttp(wiki_route), STOPWORDS)
    assert source.titles("O que quer dizer desenrascanço em Lisboa e no Porto?")[0] == "desenrascanço"


def test_a_server_error_and_a_refusal_do_pause_the_source():
    for status in (503, 429):
        source = Wikipedia(FakeHttp(lambda url, params: FakeResponse(b"busy", status)), STOPWORDS)
        for _ in range(3):
            with pytest.raises(SourceUnavailable):
                source.search("Lisboa", 2)
        assert source.health()["paused_for_s"] == 60, status


def test_a_long_question_sends_a_search_of_at_most_300_characters():
    http = FakeHttp(lambda url, params: {"query": {"search": [], "pages": []}})
    Wikipedia(http, STOPWORDS).fetch(" ".join(f"palavra{i}" for i in range(80)))
    assert max(len(c["srsearch"]) for _, c in http.calls if "srsearch" in c) <= 300


def test_health_never_shows_the_question_of_a_request():
    def route(url, params):
        return requests.HTTPError("500 Server Error for url: https://pt.wikipedia.org/w/api.php?srsearch=nif+diabetes",
                                  response=FakeResponse(b"", 500))
    source = Wikipedia(FakeHttp(route), STOPWORDS)
    with pytest.raises(SourceUnavailable):
        source.search("o meu nif e a diabetes", 2)
    assert source.health()["last_error"] == "pt.wikipedia: HTTPError 500"


def test_after_the_pause_one_request_tries_the_source_and_the_others_wait_for_it(monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    now = [1000.0]
    monkeypatch.setattr("evidencia.sources.time.monotonic", lambda: now[0])
    state = {"down": True}
    release = threading.Event()

    def route(url, params):
        if state["down"]:
            return requests.ConnectionError("no route")
        release.wait(5)
        return requests.ConnectionError("still no route")
    http = FakeHttp(route)
    source = Wikipedia(http, STOPWORDS)
    for question in ("Lisboa", "Porto", "Faro"):
        with pytest.raises(SourceUnavailable):
            source.search(question, 2)
    now[0] += 61
    state["down"] = False
    calls = len(http.calls)
    with ThreadPoolExecutor(4) as pool:
        futures = [pool.submit(source.search, q, 2) for q in ("Braga", "Beja", "Viseu", "Leiria")]
        time.sleep(0.2)
        release.set()
        for f in futures:
            with pytest.raises(SourceUnavailable):
                f.result()
    assert len(http.calls) - calls == 1


def test_an_interrupted_fetch_does_not_raise_the_interrupt_in_the_waiting_requests():
    import threading
    from concurrent.futures import ThreadPoolExecutor

    class Interrupt(BaseException):     # as KeyboardInterrupt, without stopping pytest
        pass
    release, started = threading.Event(), threading.Event()
    source = Wikipedia(FakeHttp(wiki_route), STOPWORDS)

    def fetch(question):
        started.set()
        release.wait(5)
        raise Interrupt
    source.fetch = fetch
    with ThreadPoolExecutor(2) as pool:
        leader = pool.submit(source.search, "Lisboa", 2)
        started.wait(5)
        waiter = pool.submit(source.search, "Lisboa", 2)
        time.sleep(0.2)
        release.set()
        with pytest.raises(Interrupt):
            leader.result()
        with pytest.raises(SourceUnavailable, match="interrupted"):
            waiter.result()


def test_wikidata_reads_a_label_that_is_the_same_in_every_language():
    http = FakeHttp(lambda url, params: {"entities": {}})
    Wikidata(http, STOPWORDS).entities(["Q42"], "labels")
    assert http.calls[0][1]["languages"] == "pt|pt-br|mul"
    entity = {"id": "Q42", "labels": {"mul": {"value": "Douglas Adams"}}, "claims": {}}
    assert Wikidata(http, STOPWORDS).document(entity, {}).text == "Douglas Adams."


def test_wiktionary_does_not_look_up_the_words_a_question_uses_about_a_word():
    http = FakeHttp(lambda url, params: {"query": {"pages": [], "search": []}})
    Wiktionary(http, STOPWORDS).fetch("Como se pronuncia a palavra cônjuge em Portugal?")
    assert [c["titles"] for _, c in http.calls if "titles" in c] == ["cônjuge|Portugal"]


def test_wiktionary_looks_up_a_word_that_starts_the_question_in_lower_case():
    source = Wiktionary(FakeHttp(wiki_route), STOPWORDS)
    assert source.titles("Saudade significa o quê?") == ["saudade", "Saudade"]
    assert source.titles("Portugal significa o quê?") == ["portugal", "Portugal"]
    assert source.titles("Guarda-chuva significa o quê?") == ["guarda-chuva", "Guarda-chuva"]


def test_a_fetch_that_succeeds_ends_the_pause(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("evidencia.sources.time.monotonic", lambda: now[0])
    state = {"down": True}
    source = Wikipedia(FakeHttp(lambda url, params: requests.ConnectionError("no route") if state["down"]
                                else wiki_route(url, params)), STOPWORDS)
    for question in ("Lisboa", "Porto", "Faro"):
        with pytest.raises(SourceUnavailable):
            source.search(question, 2)
    now[0] += 61
    state["down"] = False
    source.search("Lisboa", 2)
    source._paused_until = now[0] + 30      # as a failure that ended after the trial, in one race order
    source.search("Porto", 2)
    assert source.health()["paused_for_s"] == 0
