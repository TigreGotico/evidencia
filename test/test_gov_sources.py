from datetime import date

import pytest

from evidencia.sources import (DadosGov, Dgeg, DnsPt, GeoApiPt, Gleif, GovPt, Ipma, SourceUnavailable, Vies, number,
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
    assert [body["body"]["params"]["value"] for _, body in http.calls] == [
        "peço subsídio desemprego", "peço subsídio", "subsídio desemprego", "peço", "subsídio", "desemprego"]
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
    assert [body["body"]["params"]["value"] for _, body in http.calls] == ["peço subsídio desemprego", "peço subsídio"]


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
