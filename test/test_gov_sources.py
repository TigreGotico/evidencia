from datetime import date

import pytest
import requests
from urllib.parse import quote, quote_plus

from evidencia.sources import (CarrisMetropolitana, CascaisDados, DadosGov, Iefp, PortoDados, RtpNoticias, SnsTransparencia, Dgeg, DnsPt, GeoApiPt, Gleif, GovPt, Ipma, SourceUnavailable, Vies, number,
                               plain, tails, valid_nif)
from test_sources import STOPWORDS, FakeHttp, FakeResponse

SERVICES = {
    "CES:SRV:1": {"title": "Pedir o subsídio de desemprego", "category": "Serviços", "url": "/servicos/pedir-o-subsidio",
                  "description": "O subsídio de desemprego é uma prestação em dinheiro. [Pedir na Segurança Social](https://x.pt)."},
    "NEWS:2": {"title": "Entrega do IRS começa hoje", "category": "Notícias", "url": "/noticias/irs",
               "publicationDate": "2024-04-01T00:00:00Z",
               "description": "<p dir=\"ltr\">O prazo vai de 1 de abril a 30 de junho.&nbsp;</p>\n\n<p>Faça a entrega no Portal das Finanças.</p>"},
}


def gov_route(url, body):
    """The portal finds only the pages that hold every word of the query."""
    words = body["body"]["params"]["value"].split()
    hits = [{"_id": key, "_source": doc} for key, doc in SERVICES.items()
            if all(w in (doc["title"] + " " + doc["description"]).lower() for w in words)]
    return {"hits": {"total": {"value": len(hits)}, "hits": hits}}


def test_gov_pt_also_searches_without_the_first_words_of_the_question():
    http = FakeHttp(gov_route)
    hits = GovPt(http, STOPWORDS).search("Como peço o subsídio de desemprego?", 2)
    # the searches run in parallel, so they reach the portal in any order
    assert sorted(body["body"]["params"]["value"] for _, body in http.calls) == sorted(
        ["peço subsídio desemprego", "peço subsídio", "subsídio desemprego", "peço", "subsídio", "desemprego"])
    assert hits[0].text == ("Pedir o subsídio de desemprego\n"
                            "O subsídio de desemprego é uma prestação em dinheiro. Pedir na Segurança Social.")
    assert (hits[0].url, hits[0].origin) == ("https://www.gov.pt/servicos/pedir-o-subsidio", "gov.pt")


def test_gov_pt_news_is_plain_text_and_its_title_carries_the_day_it_was_published():
    hits = GovPt(FakeHttp(gov_route), STOPWORDS).search("Quando começa a entrega do IRS?", 2)
    assert {h.text for h in hits} == {"Entrega do IRS começa hoje, 2024-04-01\nO prazo vai de 1 de abril a 30 de junho.",
                                      "Entrega do IRS começa hoje, 2024-04-01\nFaça a entrega no Portal das Finanças."}


EVORA = {"nome": "Évora", "codigoine": "0705", "distrito": "Évora", "morada": "Praça do Sertório",
         "codigopostal": "7004-506", "telefone": 266777000, "email": "cmevora@cm-evora.pt", "sitio": "www.cm-evora.pt",
         "geojsons": {"municipio": {"properties": {"Area_T_ha": 130903.5}}},
         "censos2021": {"N_INDIVIDUOS": 53577, "N_INDIVIDUOS_H": 25445, "N_INDIVIDUOS_M": 28132,
                        "N_INDIVIDUOS_0_14": 7084, "N_INDIVIDUOS_65_OU_MAIS": 12629, "N_EDIFICIOS_CLASSICOS": 20823,
                        "N_ALOJAMENTOS_TOTAL": 29836},
         "censos2011": {"N_INDIVIDUOS_RESIDENT": 56596}}


CANAVIAIS = {"nome": "Canaviais", "municipio": "Évora", "distrito_ilha": "Évora", "area_ha": 1941.23,
             "codigoine": "070515", "morada": "Praça Joaquim José Calado Piteira, N.º1", "codigopostal": "7005-247",
             "telefone": "266761594", "email": "", "sitio": None, "censos2021": {"N_INDIVIDUOS": 3314}}


def geo_route(url, params):
    places = {"https://json.geoapi.pt/freguesia/Canaviais": CANAVIAIS,"https://json.geoapi.pt/municipio/%C3%89vora": EVORA,
              "https://json.geoapi.pt/municipio/%C3%89vora/freguesias": {"freguesias": ["Canaviais", "Torre de Coelheiros"]},
              "https://json.geoapi.pt/cp/1000-001": {"Localidade": "Lisboa", "Concelho": "Lisboa", "Distrito": "Lisboa",
                                                     "partes": [{"Artéria": "Rua dos Açores", "Troço": "Impares de 1 a 19"}]}}
    return places[url] if url in places else FakeResponse(b'{"erro": "Munic\\u00edpio n\\u00e3o encontrado!"}', 404)


def test_geoapi_finds_the_municipality_at_the_end_of_a_longer_name():
    http = FakeHttp(geo_route)
    docs = GeoApiPt(http, STOPWORDS).fetch("Qual é o telefone da Câmara Municipal de Évora?")
    assert [url for url, _ in http.calls] == ["https://json.geoapi.pt/municipio/C%C3%A2mara%20Municipal%20de%20%C3%89vora",
                                              "https://json.geoapi.pt/freguesia/C%C3%A2mara%20Municipal%20de%20%C3%89vora",
                                              "https://json.geoapi.pt/municipio/%C3%89vora",
                                              "https://json.geoapi.pt/municipio/%C3%89vora/freguesias"]
    assert docs[0].text.split("\n\n") == [
        "Évora é um município do distrito de Évora.",
        "A área do município de Évora é de 1 309,04 km².",
        "Nos Censos de 2021, o município de Évora tinha 53 577 habitantes: 25 445 homens e 28 132 mulheres.",
        "Nos Censos de 2021, 7 084 residentes de Évora tinham menos de 15 anos e 12 629 tinham 65 anos ou mais.",
        "Nos Censos de 2021, o município de Évora tinha 20 823 edifícios e 29 836 alojamentos.",
        "Nos Censos de 2011, o município de Évora tinha 56 596 habitantes.",
        "A Câmara Municipal de Évora fica em Praça do Sertório, 7004-506. Contactos: telefone 266777000, "
        "correio eletrónico cmevora@cm-evora.pt, sítio www.cm-evora.pt.",
        "O município de Évora tem 2 freguesias: Canaviais, Torre de Coelheiros."]


def test_geoapi_reads_a_postal_code_and_a_name_that_is_no_place_adds_nothing():
    docs = GeoApiPt(FakeHttp(geo_route), STOPWORDS).fetch("A Torre de Belém tem o código postal 1000-001?")
    assert [(d.title, d.text) for d in docs] == [(
        "Código postal 1000-001",
        "O código postal 1000-001 é de Lisboa, no município de Lisboa, distrito de Lisboa.\n\n"
        "O código postal 1000-001 abrange: Rua dos Açores (Impares de 1 a 19).")]


def test_geoapi_server_error_is_source_unavailable_not_a_missing_place():
    source = GeoApiPt(FakeHttp(lambda url, params: FakeResponse(b"down", 500)), STOPWORDS)
    with pytest.raises(SourceUnavailable):
        source.search("Quantos habitantes tem Évora?", 2)


IPMA = "https://api.ipma.pt/open-data"


def ipma_route(url, params):
    return {
        f"{IPMA}/distrits-islands.json": {"data": [{"local": "Viana do Castelo", "globalIdLocal": 1160900, "idAreaAviso": "VCT"},
                                                   {"local": "Lisboa", "globalIdLocal": 1110600, "idAreaAviso": "LSB"}]},
        f"{IPMA}/weather-type-classe.json": {"data": [{"idWeatherType": 6, "descWeatherTypePT": "Aguaceiros/chuva"}]},
        f"{IPMA}/wind-speed-daily-classe.json": {"data": [{"classWindSpeed": "1", "descClassWindSpeedDailyPT": "Fraco"}]},
        f"{IPMA}/forecast/warnings/warnings_www.json": [
            {"idAreaAviso": "VCT", "awarenessLevelID": "yellow", "awarenessTypeName": "Precipitação",
             "startTime": "2026-10-22T06:00:00", "endTime": "2026-10-22T18:00:00", "text": "Chuva por vezes forte."},
            {"idAreaAviso": "VCT", "awarenessLevelID": "green", "awarenessTypeName": "Vento",
             "startTime": "2026-10-22T06:00:00", "endTime": "2026-10-22T18:00:00", "text": ""}],
        f"{IPMA}/forecast/meteorology/cities/daily/1160900.json": {"data": [
            {"forecastDate": "2026-10-22", "idWeatherType": 6, "tMin": "7.2", "tMax": "17.0", "precipitaProb": "100.0",
             "classWindSpeed": 1, "predWindDir": "S"},
            {"forecastDate": "2026-10-26", "idWeatherType": 6, "tMin": "10.0", "tMax": "18.5", "precipitaProb": "-99.0",
             "classWindSpeed": -99, "predWindDir": "S"}]},
        f"{IPMA}/forecast/meteorology/cities/daily/1110600.json": {"data": [
            {"forecastDate": "2026-10-21", "idWeatherType": 6, "tMin": "12.5", "tMax": "21.5", "precipitaProb": "0.0",
             "classWindSpeed": 1, "predWindDir": "NW"}]},
    }[url]


def test_ipma_forecast_names_the_day_and_states_active_warnings_only():
    source = Ipma(FakeHttp(ipma_route), STOPWORDS)
    source.today = lambda: date(2026, 10, 21)
    docs = source.fetch("Vai chover amanhã em Viana do Castelo?")
    assert docs[0].text.split("\n\n") == [
        "Previsão do IPMA para Viana do Castelo, quinta-feira, 2026-10-22 (amanhã): aguaceiros/chuva; "
        "temperatura mínima 7,2 °C e máxima 17,0 °C; probabilidade de precipitação 100 %; vento fraco de sul.",
        "Previsão do IPMA para Viana do Castelo, segunda-feira, 2026-10-26: aguaceiros/chuva; "
        "temperatura mínima 10,0 °C e máxima 18,5 °C.",
        "Aviso amarelo do IPMA para a zona de Viana do Castelo: Precipitação, de 2026-10-22 06:00 a 2026-10-22 18:00. "
        "Chuva por vezes forte."]


def test_ipma_answers_for_the_place_inside_a_longer_name_and_nothing_for_a_place_it_does_not_forecast():
    http = FakeHttp(ipma_route)
    source = Ipma(http, STOPWORDS)
    assert source.fetch("Como está o tempo na Câmara Municipal de Évora?") == []
    assert [url for url, _ in http.calls] == [f"{IPMA}/distrits-islands.json"]
    source.today = lambda: date(2026, 10, 21)
    assert [d.text for d in source.fetch("Como está o tempo no Aeroporto de Lisboa?")] == [
        "Previsão do IPMA para Lisboa, quarta-feira, 2026-10-21 (hoje): aguaceiros/chuva; temperatura mínima 12,5 °C "
        "e máxima 21,5 °C; probabilidade de precipitação 0 %; vento fraco de noroeste.\n\n"
        "O IPMA não tem avisos meteorológicos amarelos, laranja ou vermelhos para a zona de Lisboa."]


def test_dados_gov_names_the_publisher_the_licence_and_the_formats():
    dataset = {"id": "d1", "title": "Índice de qualidade do ar", "page": "https://dados.gov.pt/datasets/iqar",
               "organization": {"name": "Instituto Nacional de Estatística"}, "license": "notspecified",
               "last_update": "2019-12-20T00:00:00+00:00", "description": "Índice diário por localização.",
               "resources": [{"format": "JSON"}, {"format": "csv"}, {"format": "json"}]}
    http = FakeHttp(lambda url, params: {"data": [dataset]})
    hits = DadosGov(http, STOPWORDS).search("Há dados sobre a qualidade do ar?", 3)
    assert http.calls[0][1] == {"q": "dados qualidade ar", "page_size": 5}
    assert {h.text for h in hits} == {
        "Índice de qualidade do ar\nConjunto de dados publicado por Instituto Nacional de Estatística. "
        "Licença: não indicada. Formatos: csv, json. Última atualização: 2019-12-20.",
        "Índice de qualidade do ar\nÍndice diário por localização."}


def test_numbers_use_the_pt_pt_form_and_names_give_their_tails():
    assert (number(53577), number(1309.035, 2), number(7.25, 1)) == ("53 577", "1 309,04", "7,2")
    assert tails("Câmara Municipal de Vila Nova de Gaia") == ["Câmara Municipal de Vila Nova de Gaia",
                                                              "Vila Nova de Gaia", "Gaia"]
    assert plain("<p>Um&nbsp;dois</p><p>[três](https://x.pt)</p>") == "Um dois\n\ntrês"


def test_geoapi_reads_a_parish_and_its_parish_council():
    docs = GeoApiPt(FakeHttp(geo_route), STOPWORDS).fetch("Onde fica a Junta de Freguesia de Canaviais?")
    assert docs[0].title == "Freguesia de Canaviais"
    assert docs[0].text.split("\n\n") == [
        "Canaviais é uma freguesia do município de Évora, distrito de Évora.",
        "A área da freguesia de Canaviais é de 19,41 km².",
        "Nos Censos de 2021, a freguesia de Canaviais tinha 3 314 habitantes.",
        "A Junta de Freguesia de Canaviais fica em Praça Joaquim José Calado Piteira, N.º1, 7005-247. "
        "Contactos: telefone 266761594."]


def test_geoapi_reads_a_reply_larger_than_the_default_cap():
    big = dict(EVORA, geojsons={"municipio": {"properties": {"Area_T_ha": 100.0}, "geometry": "x" * 2_600_000}})
    route = lambda url, params: big if url.endswith("/municipio/%C3%89vora") else geo_route(url, params)
    docs = GeoApiPt(FakeHttp(route), STOPWORDS).fetch("Quantos habitantes tem Évora?")
    assert docs[0].text.split("\n\n")[1] == "A área do município de Évora é de 1,00 km²."


def test_an_unexpected_reply_shape_is_source_unavailable_not_a_crash():
    for source, reply in ((GovPt, {"hits": {"hits": [{"_id": "x", "_source": None}]}}),
                          (GeoApiPt, [1, 2]), (DadosGov, [1, 2])):
        with pytest.raises(SourceUnavailable):
            source(FakeHttp(lambda url, params: reply), STOPWORDS).search("Quantos habitantes tem Évora?", 2)


def test_gov_pt_service_desks_and_public_bodies_give_address_hours_and_site():
    hits = {"POC:1": {"title": "IEFP - Serviço de Emprego de Évora", "category": "Locais de atendimento",
                      "description": "O IEFP é o serviço público de emprego nacional.",
                      "mainAddress": {"address": "Rua do Menino Jesus, 43-45", "postalCode": "7000-601  Évora"},
                      "openingHours": "Dias úteis das 9h às 17h.", "url": "/locais-de-atendimento/iefp-evora"},
            "ORG:2": {"title": "Câmara Municipal de Sintra", "category": "Entidades", "website": "http://www.cm-sintra.pt",
                      "url": "/entidades/camara-municipal-de-sintra",
                      "categories": [{"name": "Distritos", "value": "Lisboa"}, {"name": "Areas", "value": "Administração Local"}]},
            "POC:3": {"title": "Balcão de Amadora", "category": "Serviços", "description": "Atendimento municipal.",
                      "url": "http://op.cm-amadora.pt/"}}
    route = lambda url, body: {"hits": {"hits": [{"_id": k, "_source": v} for k, v in hits.items()]}}
    docs = {d.key: d for d in GovPt(FakeHttp(route), STOPWORDS).fetch("Onde fica o centro de emprego?")}
    assert docs["POC:1"].text == ("# IEFP - Serviço de Emprego de Évora\n\n"
                                  "IEFP - Serviço de Emprego de Évora fica em Rua do Menino Jesus, 43-45, 7000-601 Évora. "
                                  "Horário de IEFP - Serviço de Emprego de Évora: Dias úteis das 9h às 17h.")
    assert docs["ORG:2"].text == ("# Câmara Municipal de Sintra\n\n"
                                  "Câmara Municipal de Sintra é uma entidade pública da área Administração Local, "
                                  "no distrito de Lisboa.\n\nO sítio de Câmara Municipal de Sintra é http://www.cm-sintra.pt.")
    assert docs["POC:3"].url == "http://op.cm-amadora.pt/"


def test_ipma_takes_the_longest_place_in_a_name_and_skips_unknown_codes():
    places = {"data": [{"local": "Vila do Porto", "globalIdLocal": 1, "idAreaAviso": "AMS"},
                       {"local": "Porto", "globalIdLocal": 2, "idAreaAviso": "PTO"},
                       {"local": "Lisboa", "globalIdLocal": 3, "idAreaAviso": "LSB"},
                       {"local": "Faro", "globalIdLocal": 4, "idAreaAviso": "FAR"}]}
    day = {"forecastDate": "2026-10-21", "idWeatherType": -99, "tMin": "15.0", "tMax": "20.0", "precipitaProb": "10.0",
           "classWindSpeed": -99, "predWindDir": "S"}

    def route(url, params):
        if url.endswith("distrits-islands.json"):
            return places
        if url.endswith("weather-type-classe.json"):
            return {"data": [{"idWeatherType": -99, "descWeatherTypePT": "---"}]}
        if url.endswith("wind-speed-daily-classe.json"):
            return {"data": [{"classWindSpeed": "-99", "descClassWindSpeedDailyPT": "---"}]}
        if url.endswith("warnings_www.json"):
            return []
        return {"data": [day]}
    source = Ipma(FakeHttp(route), STOPWORDS)
    source.today = lambda: date(2026, 10, 21)
    docs = source.fetch("Como está o tempo em Vila do Porto?")
    assert [d.title for d in docs] == ["Previsão do IPMA para Vila do Porto"]
    assert docs[0].text.split("\n\n")[0] == ("Previsão do IPMA para Vila do Porto, quarta-feira, 2026-10-21 (hoje): "
                                             "temperatura mínima 15,0 °C e máxima 20,0 °C; probabilidade de precipitação 10 %.")
    assert len(source.fetch("Compara o tempo em Lisboa, Faro e Porto.")) == 2


def test_vies_reads_only_nifs_with_a_valid_check_digit():
    http = FakeHttp(lambda url, params: {"isValid": True, "requestDate": "2026-10-21T21:18:05Z", "name": "EDP, S.A.",
                                         "address": "AV 24 DE JULHO N 12\nLISBOA\n1249-300 LISBOA"})
    docs = Vies(http, STOPWORDS).fetch("Os NIF 500697256 e 500697257 são de quem?")
    assert [url for url, _ in http.calls] == ["https://ec.europa.eu/taxation_customs/vies/rest-api/ms/PT/vat/500697256"]
    assert docs[0].text.split("\n\n") == [
        "O NIF 500697256 tem o número de IVA PT500697256, válido no VIES em 2026-10-21.",
        "O NIF 500697256 pertence a EDP, S.A., com morada em AV 24 DE JULHO N 12, LISBOA, 1249-300 LISBOA."]
    assert (valid_nif("500697256"), valid_nif("505060515"), valid_nif("500697257")) == (True, True, False)


def test_vies_says_when_a_nif_has_no_valid_vat_number():
    docs = Vies(FakeHttp(lambda url, params: {"isValid": False}), STOPWORDS).fetch("De quem é o NIF 505060515?")
    assert docs[0].text == "O NIF 505060515 não tem um número de IVA válido no VIES."


EDP = {"id": "529900CLC3WDMGI9VH80", "attributes": {"lei": "529900CLC3WDMGI9VH80", "entity": {
    "legalName": {"name": "EDP, S.A."}, "registeredAs": "500697256", "status": "ACTIVE",
    "creationDate": "1976-06-30T00:00:00Z",
    "legalAddress": {"addressLines": ["Avenida 24 de Julho, n.º 12"], "postalCode": "1249-300", "city": "Lisboa"}}}}


def test_gleif_searches_the_name_and_its_last_tail_and_states_each_fact():
    http = FakeHttp(lambda url, params: {"data": [EDP] if params["filter[entity.legalName]"] == "EDP" else []})
    docs = Gleif(http, STOPWORDS).fetch("Qual é o NIPC da EDP?")
    assert [c["filter[entity.legalName]"] for _, c in http.calls] == ["NIPC da EDP", "EDP"]
    assert {c["filter[entity.legalAddress.country]"] for _, c in http.calls} == {"PT"}
    assert [(d.url, d.text.split("\n\n")) for d in docs] == [(
        "https://search.gleif.org/#/record/529900CLC3WDMGI9VH80",
        ["EDP, S.A. é uma entidade jurídica com sede em Avenida 24 de Julho, n.º 12, 1249-300 Lisboa.",
         "O NIPC de EDP, S.A. é 500697256.", "EDP, S.A. foi constituída em 1976-06-30.",
         "O registo LEI de EDP, S.A. indica que a entidade está ativa.", "O LEI de EDP, S.A. é 529900CLC3WDMGI9VH80."])]


class FakeWhois:
    def __init__(self, replies):
        self.replies, self.asked = replies, []

    def __call__(self, address, timeout=None):
        test = self

        class Conn:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def sendall(self, data):
                test.asked.append(data)
                self.out = [test.replies[data.decode().strip()].encode(), b""]

            def recv(self, size):
                return self.out.pop(0)
        return Conn()


SAPO = ("Domain: sapo.pt\nDomain Status: Registered\nCreation Date: 30/10/2002 00:00:00\n"
        "Expiration Date: 02/11/2026 23:59:00\nOwner Name: MEO - SERVIÇOS DE COMUNICAÇÕES E MULTIMÉDIA S.A. \n"
        "Owner Locality: Lisboa\nName Server: ns2.sapo.pt | IPv4: 212.55.154.194 and IPv6: \n"
        "Name Server: dns01.sapo.pt | IPv4: 213.13.28.116 and IPv6: 2001:8a0::116\n")


def test_dns_pt_asks_the_registered_domain_and_reads_the_whois_reply():
    source = DnsPt(FakeHttp(lambda url, params: {}), STOPWORDS)
    source.connect = FakeWhois({"sapo.pt": SAPO, "empresa.com.pt": "empresa.com.pt - No Match\n"})
    docs = source.fetch("Quem tem www.sapo.pt e loja.empresa.com.pt?")
    assert sorted(source.connect.asked) == [b"empresa.com.pt\r\n", b"sapo.pt\r\n"]
    assert [d.text.split("\n\n") for d in docs] == [
        ["O domínio sapo.pt foi registado em 30/10/2002 e expira em 02/11/2026.",
         "O estado do domínio sapo.pt é Registered.",
         "O titular do domínio sapo.pt é MEO - SERVIÇOS DE COMUNICAÇÕES E MULTIMÉDIA S.A., de Lisboa.",
         "Os servidores de nomes do domínio sapo.pt são ns2.sapo.pt, dns01.sapo.pt."],
        ["O domínio empresa.com.pt não está registado."]]


def test_a_whois_server_that_cannot_be_reached_is_source_unavailable():
    source = DnsPt(FakeHttp(lambda url, params: {}), STOPWORDS)

    def refuse(address, timeout=None):
        raise ConnectionRefusedError(111, "Connection refused")
    source.connect = refuse
    with pytest.raises(SourceUnavailable):
        source.search("Quem é o titular de sapo.pt?", 2)


DGEG = "https://precoscombustiveis.dgeg.gov.pt/api/PrecoComb"
STATION = {"Id": 1, "Nome": "Auto Julio Évora", "Marca": "AUTO JULIO", "Morada": "EN 114", "Localidade": "Évora",
           "Municipio": "Évora", "Distrito": "Évora", "Combustivel": "Gasóleo simples", "Preco": "2,161 €",
           "DataAtualizacao": "2026-10-21 08:00"}


def dgeg_route(url, params):
    return {f"{DGEG}/GetTiposCombustiveis": {"resultado": [
                {"Descritivo": "Gasóleo simples", "Id": 2101, "fl_ativo": True, "fl_rodoviario": True},
                {"Descritivo": "Gasolina simples 95", "Id": 3201, "fl_ativo": True, "fl_rodoviario": True},
                {"Descritivo": "Gasolina especial 98", "Id": 3405, "fl_ativo": True, "fl_rodoviario": True},
                {"Descritivo": "Gasóleo de aquecimento", "Id": 2155, "fl_ativo": True, "fl_rodoviario": False}]},
            f"{DGEG}/GetMunicipios": {"resultado": [{"Descritivo": "Évora", "Id": 92, "IdDistrito": 7},
                                                    {"Descritivo": "Lisboa", "Id": 160, "IdDistrito": 11}]},
            f"{DGEG}/GetDistritos": {"resultado": [{"Descritivo": "Évora", "Id": 7}, {"Descritivo": "Lisboa", "Id": 11}]},
            f"{DGEG}/PesquisarPostos": {"resultado": [STATION]}}[url]


def test_dgeg_names_the_fuel_and_the_municipality_and_states_the_cheapest_station():
    http = FakeHttp(dgeg_route)
    docs = Dgeg(http, STOPWORDS).fetch("Onde está o gasóleo mais barato em Évora?")
    assert http.calls[-1][1] == {"idsTiposComb": 2101, "idDistrito": 7, "idsMunicipios": "92", "qtdPorPagina": 3,
                                 "pagina": 1}
    assert [d.text for d in docs] == [
        "O combustível Gasóleo simples mais barato no município de Évora custava 2,161 euros por litro, no posto Auto Julio Évora "
        "(AUTO JULIO), em EN 114, Évora, em 2026-10-21 08:00.",
        "No posto Auto Julio Évora (AUTO JULIO), em EN 114, Évora, município de Évora, o combustível Gasóleo simples "
        "custava 2,161 euros por litro em 2026-10-21 08:00. É o 1.º preço mais baixo do combustível Gasóleo simples "
        "entre os postos do "
        "município de Évora."]


def test_dgeg_reads_the_longest_fuel_name_a_district_and_nothing_without_a_fuel():
    http = FakeHttp(dgeg_route)
    source = Dgeg(http, STOPWORDS)
    source.fetch("Quanto custa a gasolina especial 98 no distrito de Lisboa?")
    assert http.calls[-1][1]["idsTiposComb"] == 3405 and http.calls[-1][1]["idDistrito"] == 11
    assert http.calls[-1][1]["idsMunicipios"] == ""
    assert source.fetch("Quantos postos há em Évora?") == []


def test_gov_pt_finds_a_desk_when_the_question_holds_a_word_that_no_page_has():
    desk = {"POC:1": {"title": "IEFP - Serviço de Emprego de Évora", "category": "Locais de atendimento",
                      "description": "centro emprego évora", "mainAddress": {"address": "Rua do Menino Jesus, 43-45"}}}
    SERVICES.update(desk)
    try:
        hits = GovPt(FakeHttp(gov_route), STOPWORDS).search("Onde fica o centro de emprego de Évora e qual é o horário?", 3)
    finally:
        SERVICES.pop("POC:1")
    assert "IEFP - Serviço de Emprego de Évora fica em Rua do Menino Jesus, 43-45." in [h.text.split("\n")[1] for h in hits]


def test_gov_pt_asks_nothing_for_a_question_with_no_search_words():
    http = FakeHttp(gov_route)
    assert GovPt(http, STOPWORDS).fetch("?") == []
    assert http.calls == []


def test_gov_pt_runs_at_most_the_configured_number_of_searches():
    http = FakeHttp(gov_route)
    source = GovPt(http, STOPWORDS)
    source.searches = 2
    source.fetch("Como peço o subsídio de desemprego?")
    assert sorted(body["body"]["params"]["value"] for _, body in http.calls) == ["peço subsídio", "peço subsídio desemprego"]


def test_dns_pt_reads_a_no_match_in_any_case_and_registers_gov_pt_names_at_gov_pt():
    source = DnsPt(FakeHttp(lambda url, params: {}), STOPWORDS)
    source.connect = FakeWhois({"gov.pt": SAPO.replace("sapo.pt", "gov.pt"), "nada.pt": "nada.pt no match\n"})
    docs = source.fetch("Quem tem portugal.gov.pt e nada.pt?")
    assert sorted(source.connect.asked) == [b"gov.pt\r\n", b"nada.pt\r\n"]
    assert docs[1].text == "O domínio nada.pt não está registado."


def test_a_whois_reply_with_no_domain_record_is_source_unavailable():
    source = DnsPt(FakeHttp(lambda url, params: {}), STOPWORDS)
    source.connect = FakeWhois({"sapo.pt": "Query rate limit exceeded\n"})
    with pytest.raises(SourceUnavailable, match="no domain record"):
        source.search("Quem é o titular de sapo.pt?", 2)


def test_a_whois_record_without_an_expiry_date_still_gives_the_registration_date():
    source = DnsPt(FakeHttp(lambda url, params: {}), STOPWORDS)
    source.connect = FakeWhois({"sapo.pt": SAPO.replace("Expiration Date: 02/11/2026 23:59:00\n", "")})
    assert source.fetch("Quem tem sapo.pt?")[0].text.split("\n\n")[0] == "O domínio sapo.pt foi registado em 30/10/2002."


def test_a_whois_reply_over_the_cap_is_source_unavailable_and_says_so():
    source = DnsPt(FakeHttp(lambda url, params: {}), STOPWORDS)
    source.max_bytes = 10
    source.connect = FakeWhois({"sapo.pt": SAPO})
    with pytest.raises(SourceUnavailable, match="more than 10 bytes"):
        source.search("Quem tem sapo.pt?", 2)


def test_dgeg_takes_the_district_when_the_question_says_distrito():
    http = FakeHttp(dgeg_route)
    Dgeg(http, STOPWORDS).fetch("Qual é o gasóleo mais barato no distrito de Lisboa?")
    assert (http.calls[-1][1]["idDistrito"], http.calls[-1][1]["idsMunicipios"]) == (11, "")
    Dgeg(http, STOPWORDS).fetch("Qual é o gasóleo mais barato em Lisboa?")
    assert (http.calls[-1][1]["idDistrito"], http.calls[-1][1]["idsMunicipios"]) == (11, "160")


def test_dgeg_finds_the_municipality_at_the_end_of_a_longer_name():
    http = FakeHttp(dgeg_route)
    Dgeg(http, STOPWORDS).fetch("Quanto custa o gasóleo na Cidade de Évora?")
    assert http.calls[-1][1]["idsMunicipios"] == "92"


def test_vies_says_unavailable_when_the_member_state_service_is_down():
    http = FakeHttp(lambda url, params: {"isValid": False, "userError": "MS_UNAVAILABLE"})
    with pytest.raises(SourceUnavailable, match="MS_UNAVAILABLE"):
        Vies(http, STOPWORDS).search("De quem é o NIF 505060515?", 2)


def test_vies_gives_no_holder_line_when_the_name_is_withheld():
    http = FakeHttp(lambda url, params: {"isValid": True, "userError": "VALID", "requestDate": "2026-10-21", "name": "---",
                                         "address": "---"})
    assert Vies(http, STOPWORDS).fetch("De quem é o NIF 505060515?")[0].text == \
        "O NIF 505060515 tem o número de IVA PT505060515, válido no VIES em 2026-10-21."


def test_nif_check_digit_is_zero_when_the_remainder_leaves_ten_or_eleven():
    # 1 2 3 4 5 6 7 8 weighted 9..2 sum to 156, 156 % 11 == 2, so the check digit is 9
    assert [valid_nif(f"12345678{d}") for d in range(10)] == [d == 9 for d in range(10)]
    # 1 0 0 0 0 0 0 1: 9 + 2 = 11, remainder 0, so 11 - 0 = 11 and the check digit is 0
    assert [valid_nif(f"10000001{d}") for d in range(10)] == [d == 0 for d in range(10)]
    # 1 0 0 0 0 0 0 0: 9, remainder 9, 11 - 9 = 2
    assert [valid_nif(f"10000000{d}") for d in range(10)] == [d == 2 for d in range(10)]


def test_gleif_searches_the_last_tail_only_when_the_whole_name_finds_nothing():
    def route(url, params):
        return {"data": [EDP] if params.get("filter[entity.legalName]") == "Câmara Municipal do Porto" else []}
    http = FakeHttp(route)
    Gleif(http, STOPWORDS).fetch("Qual é a morada da Câmara Municipal do Porto?")
    assert [c.get("filter[entity.legalName]") for _, c in http.calls] == ["Câmara Municipal do Porto"]


def test_gleif_searches_a_valid_nipc_as_the_registration_number():
    http = FakeHttp(lambda url, params: {"data": [EDP]})
    docs = Gleif(http, STOPWORDS).fetch("de quem é o nipc 500697256 ou 500697257?")
    assert [c.get("filter[entity.registeredAs]") for _, c in http.calls] == ["500697256"]
    assert [d.key for d in docs] == ["529900CLC3WDMGI9VH80"]


def test_a_nine_digit_number_is_a_nif_only_when_the_question_says_so():
    http = FakeHttp(lambda url, params: {"isValid": True, "userError": "VALID", "name": "X", "address": ""})
    assert Vies(http, STOPWORDS).fetch("Liguei para o 912345675 e ninguém atendeu.") == []
    assert Gleif(FakeHttp(lambda url, params: {"data": []}), STOPWORDS).fetch("liguei para o 912345675") == []
    assert len(Vies(http, STOPWORDS).fetch("De quem é o contribuinte 912345675?")) == 1
    for asked in ("Qual é o número fiscal 912345675?", "De quem é PT912345675?", "NIF912345675 é de quem?",
                  "Os contribuintes 912345675 e 500697256", "número de identificação fiscal 912345675",
                  "n.º de pessoa coletiva 912345675"):
        assert len(Vies(http, STOPWORDS).fetch(asked)) >= 1, asked


def test_ipma_says_hoje_for_the_day_in_lisbon_not_on_the_server_clock(monkeypatch):
    from datetime import datetime, timezone
    import evidencia.sources

    class LateUtc(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 21, 23, 30, tzinfo=timezone.utc).astimezone(tz)
    monkeypatch.setattr(evidencia.sources, "datetime", LateUtc, raising=False)
    assert Ipma.today() == date(2026, 10, 22)


def test_a_place_in_a_lowercase_question_without_accents_is_found_by_its_proper_name():
    source = GeoApiPt(FakeHttp(geo_route), STOPWORDS)
    assert source.names("quantos habitantes tem evora") == ["Évora"]
    assert source.names("Quantos habitantes tem Evora?") == ["Évora"]
    assert "o município de Évora tinha 53 577 habitantes" in source.fetch("quantos habitantes tem evora")[0].text


def test_a_place_name_that_is_also_a_common_word_needs_a_capital():
    source = GeoApiPt(FakeHttp(geo_route), STOPWORDS)
    assert source.names("onde mora o presidente") == []
    assert source.names("Quantos habitantes tem Mora?") == ["Mora"]


def test_ipma_and_dgeg_read_a_place_and_a_fuel_without_accents():
    source = Ipma(FakeHttp(ipma_route), STOPWORDS)
    source.today = lambda: date(2026, 10, 21)
    assert source.fetch("vai chover amanha em viana do castelo")[0].title == "Previsão do IPMA para Viana do Castelo"
    http = FakeHttp(dgeg_route)
    Dgeg(http, STOPWORDS).fetch("onde esta o gasoleo mais barato em evora")
    assert (http.calls[-1][1]["idsTiposComb"], http.calls[-1][1]["idsMunicipios"]) == (2101, "92")


def test_a_question_written_with_capitals_names_places_only_by_its_capitals():
    source = GeoApiPt(FakeHttp(geo_route), STOPWORDS)
    assert source.names("Qual é o maior porto de Portugal?") == ["Portugal"]
    assert source.names("Quem fundou a Porto Editora?") == ["Porto Editora"]
    assert source.names("o bebé já caminha sozinho?") == []


def test_an_acronym_does_not_turn_the_gazetteer_off():
    source = GeoApiPt(FakeHttp(geo_route), STOPWORDS)
    assert source.names("quantos habitantes tem evora segundo o INE") == ["INE", "Évora"]


def test_a_capitalised_place_keeps_its_lowercase_joining_words():
    source = GeoApiPt(FakeHttp(geo_route), STOPWORDS)
    assert source.names("Quantos habitantes tem Arruda dos Vinhos?") == ["Arruda dos Vinhos"]
    assert source.names("quantos habitantes tem arruda dos vinhos") == ["Arruda dos Vinhos"]


def quake(time, mag, region, local=None, sensed=None, degree=None, depth=10):
    return {"time": time, "magnitud": mag, "magType": "L", "obsRegion": region, "local": local, "sensed": sensed,
            "degree": degree, "depth": depth}


def ipma_topics_route(url, params):
    if url.endswith("/observation/seismic/7.json"):
        return {"data": [quake("2026-10-15T20:34:46", "4.2", "NE Bragança", "Vinhais (Bragança)", True, "III", 16),
                         quake("2026-10-20T03:00:00", "1.1", "W  Monchique"),
                         quake("2026-10-21T10:00:00", "2.6", "SW Sagres")]}
    if url.endswith("/observation/seismic/3.json"):
        return {"data": [quake("2026-10-20T18:07:19", "3.1", "Fossa de Hirondelle")]}
    if url.endswith("/sea-locations.json"):
        return [{"local": "Faro, costa", "globalIdLocal": 1080526}, {"local": "Lisboa, costa", "globalIdLocal": 1110526}]
    if "hp-daily-sea-forecast-day" in url:
        day = int(url[-6])
        return {"forecastDate": f"2026-10-{21 + day}", "data": [
            {"globalIdLocal": 1080526, "predWaveDir": "SW", "waveHighMin": "0.8", "waveHighMax": "1.0",
             "wavePeriodMin": "4.6", "wavePeriodMax": "6.7", "totalSeaMin": 1.0, "totalSeaMax": 1.5,
             "sstMin": "21.1", "sstMax": "21.9"}]}
    if url.endswith("/uv/uv.json"):
        return [{"globalIdLocal": 1110600, "data": "2026-10-21", "iUv": "2.7", "idPeriodo": 10},
                {"globalIdLocal": 1110600, "data": "2026-10-22", "iUv": "6.4", "idPeriodo": 10},
                {"globalIdLocal": 1160900, "data": "2026-10-22", "iUv": "1.0", "idPeriodo": 10}]
    return ipma_route(url, params)


def test_ipma_lists_the_earthquakes_of_the_last_days_with_the_ones_in_the_named_place_first():
    source = Ipma(FakeHttp(ipma_topics_route), STOPWORDS)
    docs = {d.title: d for d in source.fetch("houve algum sismo em braganca?")}
    mainland = docs["Sismos registados pelo IPMA: Portugal continental e Madeira"].text.split("\n\n")
    assert mainland == [
        "Nos 30 dias até 2026-10-21, o IPMA registou 3 sismos em Portugal continental e na Madeira. O maior teve "
        "magnitude 4,2, em 2026-10-15, na região NE Bragança.",
        "Sismo de magnitude 4,2 em 2026-10-15 às 20:34 UTC, na região NE Bragança, perto de Vinhais (Bragança), a 16 km "
        "de profundidade. Foi sentido, com intensidade III.",
        "Sismo de magnitude 2,6 em 2026-10-21 às 10:00 UTC, na região SW Sagres, a 10 km de profundidade."]
    assert docs["Sismos registados pelo IPMA: Açores"].text.startswith(
        "Nos 30 dias até 2026-10-20, o IPMA registou 1 sismo nos Açores.")


def test_ipma_gives_the_sea_state_of_a_coastal_place():
    source = Ipma(FakeHttp(ipma_topics_route), STOPWORDS)
    source.today = lambda: date(2026, 10, 21)
    docs = [d for d in source.fetch("como esta o mar em faro amanha?") if d.title.startswith("Estado do mar")]
    assert docs[0].title == "Estado do mar previsto pelo IPMA para Faro"
    assert docs[0].text.split("\n\n")[2] == (
        "Estado do mar previsto pelo IPMA para Faro, quinta-feira, 2026-10-22 (amanhã): ondulação de sudoeste com "
        "0,8 a 1,0 m e período de 4,6 a 6,7 s; altura total do mar de 1,0 a 1,5 m; temperatura da água do mar de "
        "21,1 a 21,9 °C.")


def test_ipma_gives_the_uv_index_with_its_risk_level():
    source = Ipma(FakeHttp(ipma_topics_route), STOPWORDS)
    source.today = lambda: date(2026, 10, 21)
    docs = [d for d in source.fetch("qual e o indice uv em lisboa?") if d.title.startswith("Índice ultravioleta")]
    assert docs[0].text.split("\n\n") == [
        "Índice ultravioleta previsto pelo IPMA para Lisboa, quarta-feira, 2026-10-21 (hoje): 2,7, risco moderado.",
        "Índice ultravioleta previsto pelo IPMA para Lisboa, quinta-feira, 2026-10-22 (amanhã): 6,4, risco elevado."]


def test_ipma_asks_for_earthquakes_sea_or_uv_only_when_the_question_does():
    http = FakeHttp(ipma_topics_route)
    source = Ipma(http, STOPWORDS)
    source.today = lambda: date(2026, 10, 21)
    source.fetch("vai chover em lisboa?")
    assert not [url for url, _ in http.calls if "seismic" in url or "sea" in url or "uv" in url]


def test_ipma_gives_the_earthquakes_of_the_azores_only_when_the_question_names_them():
    source = Ipma(FakeHttp(ipma_topics_route), STOPWORDS)
    assert [d.title for d in source.fetch("Houve sismos nos Açores?")] == ["Sismos registados pelo IPMA: Açores"]
    assert [d.title for d in source.fetch("houve sismos na madeira?")] == [
        "Sismos registados pelo IPMA: Portugal continental e Madeira"]


def test_ipma_states_a_range_with_equal_ends_as_one_value():
    def route(url, params):
        if "hp-daily-sea-forecast-day" in url:
            return {"forecastDate": "2026-10-21", "data": [
                {"globalIdLocal": 1080526, "predWaveDir": "W", "waveHighMin": "0.5", "waveHighMax": "0.5",
                 "wavePeriodMin": "4.6", "wavePeriodMax": "6.7", "totalSeaMin": 0.5, "totalSeaMax": 0.5,
                 "sstMin": "21.1", "sstMax": "21.9"}]}
        return ipma_topics_route(url, params)
    source = Ipma(FakeHttp(route), STOPWORDS)
    source.today = lambda: date(2026, 10, 21)
    text = source.fetch("como esta o mar em faro?")[0].text
    assert "ondulação de oeste com 0,5 m e período de 4,6 a 6,7 s; altura total do mar de 0,5 m;" in text


CM = "https://api.carrismetropolitana.pt"
CM_LINES = [{"id": "3210", "long_name": "Sesimbra (Terminal) - Zambujal", "municipality_ids": ["1511"],
             "locality_ids": ["100001", "100002"]},
            {"id": "4600", "long_name": "Barreiro (Terminal) - Moita", "municipality_ids": ["1504", "1506"],
             "locality_ids": ["100003"]}]
CM_ALERT = {"active_period": [{"start": 1792654200, "end": 1792670400}], "effect": "DETOUR",
            "header_text": {"translation": [{"language": "pt", "text": "Sesimbra | 3210: Desvio de percurso "}]},
            "description_text": {"translation": [{"language": "pt", "text": "A linha 3210 terá um desvio."}]},
            "informed_entity": [{"route_id": "[YA15B]3210_0", "stop_id": "150327"}]}
CM_OLD = {**CM_ALERT, "active_period": [{"start": 1700000000, "end": 1700003600}],
          "header_text": {"translation": [{"language": "pt", "text": "Sesimbra | 3210: Obras antigas"}]}}


def cm_route(url, params):
    return {f"{CM}/v2/lines": CM_LINES, f"{CM}/v2/alerts": [CM_ALERT, CM_OLD],
            f"{CM}/v2/locations/municipalities": {"data": [{"id": "1511", "name": "Sesimbra"}, {"id": "1504", "name": "Barreiro"},
                                                           {"id": "1506", "name": "Moita"}]},
            f"{CM}/v2/locations/localities": {"data": [{"id": "100001", "name": "Sesimbra"}, {"id": "100002", "name": "Zambujal"},
                                                        {"id": "100003", "name": "Alhos Vedros"}]}}[url]


def test_carris_metropolitana_describes_a_line_with_its_current_alerts():
    source = CarrisMetropolitana(FakeHttp(cm_route), STOPWORDS)
    source.now = lambda: 1792600000
    docs = source.fetch("ha alguma perturbacao na linha 3210?")
    assert docs[0].title == "Linha 3210 da Carris Metropolitana"
    assert docs[0].text.split("\n\n") == [
        "A linha 3210 da Carris Metropolitana faz o percurso Sesimbra (Terminal) - Zambujal. Serve o município de "
        "Sesimbra e as localidades Sesimbra e Zambujal.",
        "Alerta da Carris Metropolitana para a linha 3210, de 2026-10-22 08:30 a 2026-10-22 13:00: Sesimbra | 3210: "
        "Desvio de percurso. A linha 3210 terá um desvio."]


def test_carris_metropolitana_names_the_lines_that_serve_a_place():
    source = CarrisMetropolitana(FakeHttp(cm_route), STOPWORDS)
    source.now = lambda: 1792600000
    docs = source.fetch("Que autocarros passam em Alhos Vedros?")
    assert [d.text for d in docs] == [
        "As linhas da Carris Metropolitana que servem Alhos Vedros são: 4600 (Barreiro (Terminal) - Moita)."]


def test_carris_metropolitana_answers_only_a_question_about_buses():
    http = FakeHttp(cm_route)
    assert CarrisMetropolitana(http, STOPWORDS).fetch("Quantos habitantes tem Sesimbra em 2021?") == []
    assert http.calls == []


def test_carris_metropolitana_skips_a_location_row_without_an_id():
    def route(url, params):
        reply = cm_route(url, params)
        return {"data": reply["data"] + [{"display": ""}]} if url.endswith("/localities") else reply
    source = CarrisMetropolitana(FakeHttp(route), STOPWORDS)
    source.now = lambda: 1792600000
    assert source.fetch("Que autocarros passam em Alhos Vedros?")[0].title == "Linhas da Carris Metropolitana em Alhos Vedros"


def test_carris_metropolitana_reads_a_municipality_name_as_the_municipality_not_a_village_of_that_name():
    def route(url, params):
        reply = cm_route(url, params)
        if url.endswith("/localities"):
            return {"data": reply["data"] + [{"id": "100004", "name": "Moita"}]}
        if url.endswith("/lines"):
            return [{**CM_LINES[0], "locality_ids": CM_LINES[0]["locality_ids"] + ["100004"]}, CM_LINES[1]]
        return reply
    source = CarrisMetropolitana(FakeHttp(route), STOPWORDS)
    source.now = lambda: 1792600000
    assert source.fetch("Que linhas passam na Moita?")[0].text == (
        "As linhas da Carris Metropolitana que servem Moita são: 4600 (Barreiro (Terminal) - Moita).")


RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>RTP</title>
<item><title>Tribunal Penal Internacional vence o  Pr\xc3\xa9mio Sakharov</title>
<link>https://www.rtp.pt/noticias/mundo/sakharov_n1</link><category>Mundo</category>
<description> &lt;img src="x.png"/&gt; </description><pubDate>Thu, 22 Oct 2026 13:29:03 +0100</pubDate></item>
<item><title>  </title><link>https://www.rtp.pt/x</link></item>
<item><title>Greve nos comboios na sexta-feira</title><link>https://www.rtp.pt/noticias/pais/greve_n2</link>
<category>Pa\xc3\xads</category><pubDate>Wed, 21 Oct 2026 23:10:00 +0000</pubDate></item>
</channel></rss>"""


def test_rtp_gives_each_headline_with_its_section_its_time_and_its_link():
    docs = RtpNoticias(FakeHttp(lambda url, params: RSS), STOPWORDS).fetch("Quem ganhou o Prémio Sakharov?")
    assert [(d.title, d.url, d.text) for d in docs] == [
        ("Tribunal Penal Internacional vence o Prémio Sakharov", "https://www.rtp.pt/noticias/mundo/sakharov_n1",
         "Notícia da RTP, secção Mundo, de 2026-10-22 13:29: Tribunal Penal Internacional vence o Prémio Sakharov."),
        ("Greve nos comboios na sexta-feira", "https://www.rtp.pt/noticias/pais/greve_n2",
         "Notícia da RTP, secção País, de 2026-10-22 00:10: Greve nos comboios na sexta-feira.")]


def test_rss_states_a_headline_without_a_section_or_a_time():
    feed = b"<rss><channel><item><title>Sem data</title><link>https://www.rtp.pt/a</link></item></channel></rss>"
    assert [d.text for d in RtpNoticias(FakeHttp(lambda url, params: feed), STOPWORDS).fetch("x")] == [
        "Notícia da RTP: Sem data."]


def test_a_ckan_portal_names_each_dataset_with_publisher_licence_and_formats():
    package = {"id": "p1", "name": "geocascais-estacionamento", "title": "Estacionamento", "license_title": "Creative Commons - Atribuição",
               "metadata_modified": "2026-10-21T17:27:06.107221", "notes": "Dados geográficos de estacionamentos.\r\n\r\nFonte: GeoCascais",
               "organization": {"title": "Câmara Municipal de Cascais"}, "resources": [{"format": "GeoJSON"}, {"format": "CSV"}]}
    http = FakeHttp(lambda url, params: {"success": True, "result": {"results": [package]}})
    docs = CascaisDados(http, STOPWORDS).fetch("Onde há estacionamento em Cascais?")
    assert http.calls[0] == ("https://dadosabertos.cascais.pt/api/3/action/package_search",
                             {"q": "estacionamento cascais", "rows": 5})
    assert (docs[0].url, docs[0].text) == (
        "https://dadosabertos.cascais.pt/dataset/geocascais-estacionamento",
        "# Estacionamento\n\nConjunto de dados publicado por Câmara Municipal de Cascais. Licença: Creative Commons - "
        "Atribuição. Formatos: csv, geojson. Última atualização: 2026-10-21.\n\nDados geográficos de estacionamentos.\n\n"
        "Fonte: GeoCascais")



def test_porto_searches_each_word_in_the_title_and_the_description():
    """The free-text search of the Porto portal finds nothing, and a search by field works."""
    http = FakeHttp(lambda url, params: {"success": True, "result": {"results": []}})
    assert PortoDados(http, STOPWORDS).fetch("Onde há estacionamento no Porto?") == []
    assert http.calls == [("https://dadosabertos.cm-porto.pt/api/3/action/package_search",
                           {"q": "title:estacionamento OR notes:estacionamento OR title:porto OR notes:porto", "rows": 5})]

def test_an_opendatasoft_portal_names_each_dataset_with_its_record_count():
    dataset = {"dataset_id": "monitorizacao-sazonal-csh", "metas": {"default": {
        "title": "Atividade nos Cuidados Saúde Hospitalares", "description": "<p>Episódios de <b>urgência</b>.</p>",
        "license": None, "modified": "2019-01-21T09:33:16+00:00", "publisher": "DE-SNS", "records_count": 85715}}}
    http = FakeHttp(lambda url, params: {"results": [dataset]})
    docs = SnsTransparencia(http, STOPWORDS).fetch("Quantas urgências houve nos hospitais?")
    assert http.calls[0] == ("https://transparencia.sns.gov.pt/api/explore/v2.1/catalog/datasets",
                             {"where": 'search("urgências hospitais")', "limit": 5})
    assert (docs[0].url, docs[0].text) == (
        "https://transparencia.sns.gov.pt/explore/dataset/monitorizacao-sazonal-csh/",
        "# Atividade nos Cuidados Saúde Hospitalares\n\nConjunto de dados publicado por DE-SNS. Licença: não indicada. "
        "Última atualização: 2019-01-21. Tem 85 715 registos.\n\nEpisódios de urgência.")


def iefp_card(offer, title, place, schedule, contract, form, education):
    return f"""<article class="card card-oferta flex-fill mb-3 " id="ofertacard_{offer}">
 <div class="card-header"><div class="row-flex"><div class="col-xs-6">Oferta de Emprego</div>
 <div class="col-xs-6 text-right">ID: {offer}
 </div></div>
 <div class="d-flex align-items-center" style="min-height: 3rem;"><strong>{title}</strong></div></div>
 <div class="card-body small flex-fill"><div class="row-flex mb-3"><div class="col-xs-12 col-sm-6">
 <div class="text-uppercase"><i class="fa fa-map-marker"></i>Localização e Horário</div>
 <div><strong>
 {place}
 | <span class="text-capitalize">{schedule}</span>
 </strong></div></div>
 <div class="col-xs-12 col-sm-6"><div class="text-uppercase"><i class="fa fa-file-text-o"></i>Tipo de contrato </div>
 <div><strong>
 <span class="text-capitalize">{contract}</span>
 (<span class="text-capitalize">A tempo completo</span>)
 </strong></div></div></div>
 <div class="row-flex mb-3"><div class="col-xs-12 col-sm-6">
 <div class="text-uppercase"><i class="fa fa-briefcase"></i>Forma de prestação de trabalho</div>
 <div><strong>{form}</strong></div></div></div>
 <hr class="hr-dark" /><div><div class="text-uppercase mb-2"><strong>Condições de Acesso</strong></div>
 <ul class="list-unstyled"><li>Habilitação:
 <span class="text-lowercase">
 {education}
 </span></li></ul></div></div>
 <div class="card-footer"><a class="btn" href="IEFP/pesquisas/detalheOfertas2.do?idOferta={offer}&posAbs=2&nav=true">Mais Detalhes</a></div>
 </article>"""


IEFP = "https://iefponline.iefp.pt/IEFP/pesquisas/search.do"
IEFP_PAGE = ('<div class="h4">1 - 2 resultado(s) listado(s) de <span id="totalResultados">4</span></div>'
             + iefp_card("589453805", "Cozinheiro", "SINTRA", "Diurno", "Termo certo", "Presencial", "Igual ou superior ao 4.º ano")
             + iefp_card("589400001", "Ajudante de Cozinha", "SÃO BRÁS DE ALPORTEL", "a combinar", "Sem termo", "Presencial",
                         "Igual ou superior ao 6.º ano"))


# the server sends ISO-8859-1 with no charset in the page; a page that FlareSolverr solved comes back in UTF-8
@pytest.mark.parametrize("encoding", ["iso-8859-1", "utf-8"])
def test_iefp_searches_the_job_in_the_named_municipality_and_states_each_offer(encoding):
    http = FakeHttp(lambda url, params: IEFP_PAGE.encode(encoding))
    docs = Iefp(http, STOPWORDS).fetch("há ofertas de emprego para cozinheiro em sintra?")
    assert http.calls == [(f"{IEFP}?cat=ofertaEmprego&text=cozinheiro&concelho_facet=SINTRA", {})]
    assert [d.text for d in docs] == [
        'A pesquisa do IEFP por "cozinheiro" encontra 4 ofertas de emprego no município de Sintra.',
        "Oferta de emprego do IEFP n.º 589453805: Cozinheiro, em Sintra, horário diurno. Contrato termo certo, a tempo "
        "completo. Trabalho presencial. Habilitação: igual ou superior ao 4.º ano.",
        "Oferta de emprego do IEFP n.º 589400001: Ajudante de Cozinha, em São Brás de Alportel, horário a combinar. "
        "Contrato sem termo, a tempo completo. Trabalho presencial. Habilitação: igual ou superior ao 6.º ano."]
    assert docs[1].url == "https://iefponline.iefp.pt/IEFP/pesquisas/detalheOfertas2.do?idOferta=589453805"


def test_iefp_answers_only_a_question_about_job_offers():
    http = FakeHttp(lambda url, params: IEFP_PAGE.encode())
    assert Iefp(http, STOPWORDS).fetch("Qual é o código do trabalho em Sintra?") == []
    assert http.calls == []


def test_iefp_states_that_there_is_no_offer_for_the_job_in_the_municipality():
    http = FakeHttp(lambda url, params: '<div class="h4">N&atilde;o foram encontrados resultados.</div>'.encode("iso-8859-1"))
    docs = Iefp(http, STOPWORDS).fetch("ha vagas de emprego para astronauta em evora")
    assert http.calls == [(f"{IEFP}?cat=ofertaEmprego&text=astronauta&concelho_facet=%C9VORA", {})]
    assert [d.text for d in docs] == ['A pesquisa do IEFP por "astronauta" não encontra ofertas de emprego no município de Évora.']


def test_iefp_lists_the_offers_of_a_municipality_when_the_question_names_no_job():
    http = FakeHttp(lambda url, params: IEFP_PAGE.encode("iso-8859-1"))
    docs = Iefp(http, STOPWORDS).fetch("ha vagas de emprego em evora")
    assert http.calls == [(f"{IEFP}?cat=ofertaEmprego&concelho_facet=%C9VORA", {})]
    assert docs[0].text == "O IEFP tem 4 ofertas de emprego no município de Évora."


def test_iefp_asks_nothing_without_a_job_or_a_municipality():
    http = FakeHttp(lambda url, params: IEFP_PAGE.encode())
    assert Iefp(http, STOPWORDS).fetch("ha ofertas de emprego?") == []
    assert http.calls == []


class Capture(requests.adapters.BaseAdapter):
    """Keeps the URL of each request as it goes on the wire, and sends nothing."""
    def __init__(self):
        super().__init__()
        self.urls = []

    def send(self, request, **kwargs):
        self.urls.append(request.url)
        raise requests.ConnectionError("captured")

    def close(self):
        pass


def test_iefp_sends_the_query_in_the_charset_of_the_server():
    # the server reads the query in ISO-8859-1: ÉVORA as %C9VORA finds 13 cooks, as %C3%89VORA none
    session, capture = requests.Session(), Capture()
    session.mount("https://", capture)
    with pytest.raises(requests.ConnectionError):
        Iefp(session, STOPWORDS).fetch("Há ofertas de emprego para mecânico em Évora?")
    assert capture.urls == [f"{IEFP}?cat=ofertaEmprego&text={quote('mecânico'.encode('iso-8859-1'))}"
                            f"&concelho_facet={quote('ÉVORA'.encode('iso-8859-1'))}"]


@pytest.mark.parametrize("question", ["Vai haver uma vaga de calor em Évora?", "Há vagas de estacionamento em Faro?",
                                      "Há vagas para estacionar em Faro?", "Há vagas para o curso de medicina em Coimbra?",
                                      "Qual o horário do centro de emprego de Évora?", "Qual é a taxa de emprego em Portugal?",
                                      "Há ofertas para telemóveis em Lisboa?"])
def test_iefp_ignores_a_vaga_that_is_not_a_job(question):
    http = FakeHttp(lambda url, params: IEFP_PAGE.encode())
    assert Iefp(http, STOPWORDS).fetch(question) == []
    assert http.calls == []


def ipma_quakes(data):
    def route(url, params):
        if url.endswith("/observation/seismic/7.json"):
            return {"data": data}
        return ipma_topics_route(url, params)
    return route


def test_ipma_never_states_the_no_magnitude_value_and_does_not_count_it():
    # IPMA gives magnitud "-99.0" to a quake with no magnitude
    source = Ipma(FakeHttp(ipma_quakes([quake("2026-10-11T16:19:00", "-99.0", "N  Arraiolos"),
                                        quake("2026-10-12T10:00:00", "1.0", "E  Arraiolos")])), STOPWORDS)
    text = next(d for d in source.fetch("Houve sismos em Arraiolos?") if d.key == "seismic-7").text
    assert "-99" not in text and "registou 1 sismo " in text


@pytest.mark.parametrize("question, region", [("Houve sismos em Mira?", "W  Odemira"), ("Houve sismos na Sé?", "SE Monchique")])
def test_ipma_lists_a_small_quake_only_when_its_region_holds_the_whole_name(question, region):
    source = Ipma(FakeHttp(ipma_quakes([quake("2026-10-08T03:43:00", "0.4", region),
                                        quake("2026-10-12T10:00:00", "3.0", "SW Sagres")])), STOPWORDS)
    text = next(d for d in source.fetch(question) if d.key == "seismic-7").text
    assert "magnitude 0,4" not in text and "Sismo de magnitude 3,0" in text


def test_ipma_lists_a_small_quake_in_the_named_place():
    source = Ipma(FakeHttp(ipma_quakes([quake("2026-10-08T03:43:00", "0.4", "W  Odemira")])), STOPWORDS)
    assert "Sismo de magnitude 0,4" in source.fetch("Houve sismos em Odemira?")[0].text


def test_ipma_a_question_about_madeira_and_the_azores_gets_both_areas():
    source = Ipma(FakeHttp(ipma_topics_route), STOPWORDS)
    assert {d.key for d in source.fetch("Houve sismos na Madeira e nos Açores?")} == {"seismic-7", "seismic-3"}


def test_ipma_reads_the_sea_forecast_once_for_all_the_named_places():
    http = FakeHttp(ipma_topics_route)
    source = Ipma(http, STOPWORDS)
    source.today = lambda: date(2026, 10, 21)
    source.fetch("Como está o mar em Faro e em Lisboa?")
    assert len([url for url, _ in http.calls if "hp-daily-sea-forecast-day" in url]) == 3


@pytest.mark.parametrize("question", ["Vai haver ondas de calor em Faro?", "Houve abalos financeiros em Faro?"])
def test_ipma_ignores_a_phrase_that_is_not_about_the_sea_or_quakes(question):
    http = FakeHttp(ipma_topics_route)
    Ipma(http, STOPWORDS).fetch(question)
    assert not [url for url, _ in http.calls if "seismic" in url or "sea" in url]


def test_carris_answers_a_question_with_the_word_paragem():
    http = FakeHttp(cm_route)
    CarrisMetropolitana(http, STOPWORDS).fetch("Qual é a paragem mais perto de Alhos Vedros?")
    assert http.calls


@pytest.mark.parametrize("question", ["Onde fica a carreira de tiro de Almada?", "Qual é a linha de costa da Moita?"])
def test_carris_ignores_a_linha_or_carreira_that_is_not_a_bus(question):
    http = FakeHttp(cm_route)
    assert CarrisMetropolitana(http, STOPWORDS).fetch(question) == []
    assert http.calls == []


def test_carris_reads_a_year_as_a_year_and_a_line_after_the_word_linha():
    year_line = {"id": "2026", "long_name": "Loures | Circular", "municipality_ids": ["1107"], "locality_ids": []}
    def route(url, params):
        return [*CM_LINES, year_line] if url.endswith("/v2/lines") else cm_route(url, params)
    source = CarrisMetropolitana(FakeHttp(route), STOPWORDS)
    source.now = lambda: 1792600000
    assert [d.title for d in source.fetch("Que autocarros passam na Moita em 2026?")] == [
        "Linhas da Carris Metropolitana em Moita"]
    assert [d.title for d in source.fetch("Há perturbações na linha 2026?")] == ["Linha 2026 da Carris Metropolitana"]


def test_rss_keeps_a_headline_with_a_date_it_cannot_read_and_reads_minus_zero_as_utc():
    feed = ("<rss><channel><item><title>Data errada</title><link>https://www.rtp.pt/a</link><pubDate>ontem</pubDate></item>"
            "<item><title>Sem zona</title><link>https://www.rtp.pt/b</link><pubDate>Wed, 21 Oct 2026 23:10:00 -0000</pubDate>"
            "</item></channel></rss>").encode()
    assert [d.text for d in RtpNoticias(FakeHttp(lambda url, params: feed), STOPWORDS).fetch("x")] == [
        "Notícia da RTP: Data errada.", "Notícia da RTP, de 2026-10-22 00:10: Sem zona."]


@pytest.mark.parametrize("question", ["Há vagas de cozinheiro em Sintra?", "Há ofertas de trabalho para cozinheiro em Sintra?",
                                      "Procuro trabalho como cozinheiro em Sintra"])
def test_iefp_answers_the_common_ways_to_ask_for_a_job(question):
    http = FakeHttp(lambda url, params: IEFP_PAGE.encode())
    Iefp(http, STOPWORDS).fetch(question)
    assert [url for url, _ in http.calls] == [f"{IEFP}?cat=ofertaEmprego&text=cozinheiro&concelho_facet=SINTRA"]


def test_iefp_drops_a_job_word_that_the_server_cannot_read():
    # ISO-8859-1 has no form of 厨师; an empty text would list every offer in the country
    http = FakeHttp(lambda url, params: IEFP_PAGE.encode())
    assert Iefp(http, STOPWORDS).fetch("Há vagas de emprego para 厨师?") == []
    assert http.calls == []
    Iefp(http, STOPWORDS).fetch("Há vagas de emprego para 厨师 em Braga?")
    assert [url for url, _ in http.calls] == [f"{IEFP}?cat=ofertaEmprego&concelho_facet=BRAGA"]


def test_iefp_reads_a_capitalised_sao_municipality():
    http = FakeHttp(lambda url, params: IEFP_PAGE.encode())
    Iefp(http, STOPWORDS).fetch("Ofertas de emprego para cozinheiro em São Brás de Alportel")
    assert [url for url, _ in http.calls] == [
        f"{IEFP}?cat=ofertaEmprego&text=cozinheiro&concelho_facet={quote_plus('SÃO BRÁS DE ALPORTEL'.encode('iso-8859-1'))}"]


def test_a_name_that_starts_with_a_stopword_is_kept_inside_the_question():
    source = Iefp(FakeHttp(lambda url, params: b""), STOPWORDS)
    assert source.names("Onde fica São João da Madeira?") == ["São João da Madeira"]
    assert source.names("Quando abre a Loja do Cidadão?") == ["Loja do Cidadão"]


def test_carris_does_not_read_a_postal_code_as_a_line():
    lines = [{"id": "2900", "long_name": "Lisboa (Campo Grande) - São Sebastião", "municipality_ids": ["1109"], "locality_ids": []},
             {"id": "4400", "long_name": "Setúbal (Terminal) - Azeitão", "municipality_ids": ["1512"], "locality_ids": []}]
    def route(url, params):
        if url.endswith("/lines"):
            return lines
        if url.endswith("/municipalities"):
            return {"data": [{"id": "1109", "name": "Mafra"}, {"id": "1512", "name": "Setúbal"}]}
        return cm_route(url, params)
    source = CarrisMetropolitana(FakeHttp(route), STOPWORDS)
    source.now = lambda: 1792600000
    assert [d.title for d in source.fetch("Que autocarros passam na Avenida Luísa Todi, 2900-461 Setúbal?")] == [
        "Linhas da Carris Metropolitana em Setúbal"]
    assert [d.title for d in source.fetch("Há alterações no autocarro número 2900?")] == ["Linha 2900 da Carris Metropolitana"]


@pytest.mark.parametrize("index, risk", [("2.5", "moderado"), ("10.5", "extremo"), ("2.4", "baixo")])
def test_ipma_rounds_a_uv_index_half_up_to_its_band(index, risk):
    def route(url, params):
        if url.endswith("/uv/uv.json"):
            return [{"globalIdLocal": 1110600, "data": "2026-10-21", "iUv": index, "idPeriodo": 10}]
        return ipma_topics_route(url, params)
    source = Ipma(FakeHttp(route), STOPWORDS)
    source.today = lambda: date(2026, 10, 21)
    assert source.fetch("Qual é o índice UV em Lisboa?")[0].text.endswith(f", risco {risk}.")


# the names that origin/dev 6987ef0 gives for these questions, before "São" was kept inside a question
@pytest.mark.parametrize("question, names", [
    ("Qual é a capital de Portugal? E a de Espanha?", ["Portugal", "Espanha"]),
    ("Olá! Qual é a população de Faro?", ["Olá", "Faro"]),
    ("Estou em Lisboa. Quando abre o museu?", ["Estou", "Lisboa"]),
    ("1. Onde fica Évora?", ["Évora"]),
    ("Bom dia, Qual é a população de Faro?", ["Bom", "Faro"]),
    ("Como está o tempo em Lisboa? E No Porto?", ["Lisboa", "Porto"]),
])
def test_a_capitalised_question_word_at_a_sentence_start_is_not_a_name(question, names):
    assert Iefp(FakeHttp(lambda url, params: b""), STOPWORDS).names(question) == names


def test_carris_reads_a_line_before_a_range_of_years():
    source = CarrisMetropolitana(FakeHttp(cm_route), STOPWORDS)
    source.now = lambda: 1792600000
    assert [d.title for d in source.fetch("Há alterações na linha 3210-2027?")] == ["Linha 3210 da Carris Metropolitana"]


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("evidencia.sources.time.monotonic", lambda: now[0])
    return now


def lines_read(http):
    return sum(url.endswith("/v2/lines") for url, _ in http.calls)


def test_carris_reads_its_line_list_again_after_a_day(clock):
    lines = [CM_LINES[0]]
    http = FakeHttp(lambda url, params: lines if url.endswith("/v2/lines") else cm_route(url, params))
    source = CarrisMetropolitana(http, STOPWORDS)
    source.now = lambda: 1792600000
    assert source.fetch("linha 3210?") and lines_read(http) == 1
    lines = CM_LINES
    clock[0] += source.lists_ttl - 1
    assert source.fetch("linha 4600?") == [] and lines_read(http) == 1
    clock[0] += 2
    assert source.fetch("linha 4600?")[0].title == "Linha 4600 da Carris Metropolitana"
    assert lines_read(http) == 2


def test_a_failed_refresh_keeps_the_old_list_and_tries_again_after_a_pause(clock):
    from evidencia.sources import BREAKER_PAUSE
    down = [False]
    def route(url, params):
        if down[0] and url.endswith("/v2/lines"):
            return requests.ConnectionError("down")
        return cm_route(url, params)
    http = FakeHttp(route)
    source = CarrisMetropolitana(http, STOPWORDS)
    source.now = lambda: 1792600000
    source.fetch("linha 3210?")
    down[0] = True
    clock[0] += source.lists_ttl
    assert source.fetch("linha 3210?")[0].title == "Linha 3210 da Carris Metropolitana"
    assert lines_read(http) == 2
    clock[0] += BREAKER_PAUSE - 1
    source.fetch("linha 3210?")
    assert lines_read(http) == 2
    clock[0] += 2
    down[0] = False
    source.fetch("linha 3210?")
    assert lines_read(http) == 3
    clock[0] += source.lists_ttl - 1
    source.fetch("linha 3210?")
    assert lines_read(http) == 3


def test_a_list_that_was_never_read_fails_its_question():
    http = FakeHttp(lambda url, params: requests.ConnectionError("down") if url.endswith("/v2/lines")
                    else cm_route(url, params))
    with pytest.raises(requests.ConnectionError):
        CarrisMetropolitana(http, STOPWORDS).fetch("linha 3210?")


def test_every_question_shares_one_read_of_a_feed_for_five_minutes(clock):
    http = FakeHttp(lambda url, params: RSS)
    source = RtpNoticias(http, STOPWORDS)
    source.search("Quem ganhou o Prémio Sakharov?", 2)
    source.search("Há greve nos comboios?", 2)
    assert len(http.calls) == 1
    clock[0] += 301
    assert source.search("Quem ganhou o Prémio Sakharov?", 2)
    assert len(http.calls) == 2


def test_a_feed_that_cannot_be_read_fails_the_source_and_shows_in_its_health(clock):
    from evidencia.sources import SourceUnavailable
    down = [False]
    source = RtpNoticias(FakeHttp(lambda url, params: requests.ConnectionError("down") if down[0] else RSS), STOPWORDS)
    assert source.search("Quem ganhou o Prémio Sakharov?", 2)
    down[0] = True
    clock[0] += 301
    with pytest.raises(SourceUnavailable):
        source.search("Quem ganhou o Prémio Sakharov?", 2)
    health = source.health()
    assert health["failures"] == 1 and "ConnectionError" in health["last_error"]


def test_a_kept_list_shows_its_failed_refresh_in_the_health_and_logs_its_real_age(clock, caplog):
    from evidencia.sources import BREAKER_PAUSE
    down = [False]
    source = CarrisMetropolitana(FakeHttp(lambda url, params: requests.ConnectionError("down")
                                          if down[0] and url.endswith("/v2/lines") else cm_route(url, params)), STOPWORDS)
    source.now = lambda: 1792600000
    source.search("linha 3210?", 2)
    down[0] = True
    clock[0] += source.lists_ttl
    source.search("linha 4600?", 2)
    clock[0] += BREAKER_PAUSE
    source.search("linha 3201?", 2)
    assert source.health()["failures"] == 2 and "ConnectionError" in source.health()["last_error"]
    ages = [r.getMessage().rsplit("read ", 1)[1] for r in caplog.records if "cannot refresh lines" in r.getMessage()]
    assert ages == [f"{int(source.lists_ttl)} s ago", f"{int(source.lists_ttl + BREAKER_PAUSE)} s ago"]


def test_questions_at_the_same_time_share_one_read_of_the_feed():
    import threading
    import time
    def slow(url, params):
        time.sleep(0.2)
        return RSS
    http = FakeHttp(slow)
    source = RtpNoticias(http, STOPWORDS)
    threads = [threading.Thread(target=source.search, args=(f"Notícia número {i}?", 2)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(http.calls) == 1
