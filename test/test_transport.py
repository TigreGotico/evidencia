import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from unblock_requests import CloudflareSession

from evidencia import Config
from evidencia.sources import SourceUnavailable, build_sources
from test_sources import STOPWORDS

CHALLENGE = b"<!DOCTYPE html><html><head><title>Just a moment...</title></head><body>cf_chl_opt</body></html>"
SERVICE = {"title": "Pedir o subsídio de desemprego", "category": "Serviços", "url": "/servicos/subsidio",
           "description": "O subsídio de desemprego é uma prestação em dinheiro."}
DATASET = {"data": [{"id": "d1", "title": "Escolas de Lisboa", "organization": {"name": "Câmara Municipal de Lisboa"},
                     "license": "cc-by", "resources": [{"format": "CSV"}], "page": "https://dados.cm-lisboa.pt/d1"}]}


class Site(BaseHTTPRequestHandler):
    """A site behind a challenge at /api, and a FlareSolverr at /v1 that solves it like a browser:
    a JSON reply comes back inside the <pre> of the page the browser shows."""
    solves: list[str] = []

    def do_GET(self):
        self.send_response(403)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(CHALLENGE)

    def do_POST(self):
        asked = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/search":
            self.reply({"hits": {"hits": [{"_id": "S1", "_source": SERVICE}]}})
            return
        Site.solves.append(asked["url"])
        page = f"<html><head></head><body><pre>{json.dumps(DATASET, ensure_ascii=False).replace('&', '&amp;')}</pre></body></html>"
        self.reply({"status": "ok", "solution": {"response": page}})

    def reply(self, data):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def site():
    Site.solves = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def dados(base, **transport):
    source = build_sources(["dados.gov.pt"], "evidencia-test", STOPWORDS, "pt-PT", 5, **transport)["dados.gov.pt"]
    source.api = f"{base}/api"
    return source


def test_a_challenged_request_is_solved_by_flaresolverr_and_the_json_inside_its_page_is_read(site):
    hits = dados(site, flaresolverr_url=site).search("escolas de Lisboa", 2)
    assert Site.solves == [f"{site}/api?q=escolas+lisboa&page_size=5"]
    assert hits[0].text == ("Escolas de Lisboa\nConjunto de dados publicado por Câmara Municipal de Lisboa. "
                            "Licença: cc-by. Formatos: csv.")
    assert hits[0].url == "https://dados.cm-lisboa.pt/d1"


def test_a_post_through_the_session_is_read_in_full(site):
    source = build_sources(["gov.pt"], "evidencia-test", STOPWORDS, "pt-PT", 5)["gov.pt"]
    source.api = f"{site}/search"
    hits = source.search("subsídio de desemprego", 1)
    assert hits[0].text == "Pedir o subsídio de desemprego\nO subsídio de desemprego é uma prestação em dinheiro."


def test_a_challenge_without_a_solver_is_source_unavailable_and_never_an_archived_copy(site):
    with pytest.raises(SourceUnavailable, match="Cloudflare challenge"):
        dados(site).search("escolas de Lisboa", 2)
    assert Site.solves == []


def test_the_session_uses_curl_cffi_first_and_never_the_wayback_machine():
    http = build_sources(["wikidata"], "ua", STOPWORDS, "pt-PT", 5, flaresolverr_url="http://solver:8191")["wikidata"].http
    assert isinstance(http, CloudflareSession)
    assert (http.cf_mode, http.flaresolverr_url, http.flaresolverr_fallback, http.wayback_fallback) == \
        ("curl_cffi", "http://solver:8191", True, False)
    assert http.headers["User-Agent"] == "ua"


def test_transport_settings_come_from_the_file_or_the_environment(tmp_path):
    path = tmp_path / "evidencia.toml"
    path.write_text('transport = "flaresolverr"\nflaresolverr_url = "http://a:8191"\n')
    config = Config.load(str(path), env={"EVIDENCIA_BROWSERLESS_URL": "http://b:3000"})
    assert (config.transport, config.flaresolverr_url, config.browserless_url) == \
        ("flaresolverr", "http://a:8191", "http://b:3000")
    with pytest.raises(ValueError, match="transport must be one of"):
        Config(transport="wayback")


def test_every_get_goes_through_flaresolverr_in_the_flaresolverr_transport(site):
    hits = dados(site, transport="flaresolverr", flaresolverr_url=site).search("escolas de Lisboa", 2)
    assert Site.solves == [f"{site}/api?q=escolas+lisboa&page_size=5"]
    assert hits[0].url == "https://dados.cm-lisboa.pt/d1"


class Big(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'{"data": [' + b" " * 5_000_000 + b"]}"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def test_curl_stops_a_reply_larger_than_the_largest_cap_of_the_sources():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Big)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with pytest.raises(SourceUnavailable, match=r"curl: \(63\)"):
            dados(f"http://127.0.0.1:{server.server_address[1]}").search("escolas", 1)
    finally:
        server.shutdown()


def test_the_transport_is_what_the_config_says_and_not_a_stray_environment_variable(monkeypatch):
    monkeypatch.setenv("EVIDENCIA_TRANSPORT", "wayback")
    http = build_sources(["wikidata"], "ua", STOPWORDS, "pt-PT", 5)["wikidata"].http
    assert http.cf_mode == "curl_cffi"
