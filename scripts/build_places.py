"""Write evidencia/locale/pt-places.json: the names a question can give a place by, without capitals or accents.

    python scripts/build_places.py

It reads the 308 municipalities and the districts and islands from geoapi.pt (CAOP), and the places that IPMA
forecasts. Parishes stay out: many parish names are common words, such as Ajuda, Luz or Sé.
"""
import json
import re
import unicodedata
from pathlib import Path

import requests

GEOAPI = "https://json.geoapi.pt"
IPMA = "https://api.ipma.pt/open-data/distrits-islands.json"
# place names that are also common words or person names, such as "nelas" (in them) or "tomar" (to take):
# a match needs a capital letter
COMMON = ["almeida", "amadora", "avis", "batalha", "caminha", "chaves", "constância", "corvo", "cuba", "entroncamento",
          "espinho", "fronteira", "gavião", "guarda", "horta", "lagoa", "lagos", "lousã", "madalena", "maia", "mira",
          "moita", "monção", "mora", "moura", "nazaré", "nelas", "nordeste", "oleiros", "olhão", "paredes", "pombal",
          "povoação", "redondo", "sagres", "santana", "tomar", "tábua", "vagos", "valença", "velas", "vendas novas"]
JOINS = {"a", "o", "e", "à", "de", "da", "do", "das", "dos"}
OUT = Path(__file__).parent.parent / "evidencia" / "locale" / "pt-places.json"


def fold(text):
    return "".join(c for c in unicodedata.normalize("NFD", text.casefold()) if unicodedata.category(c) != "Mn")


def proper(name):
    """The name with each word capitalised but the joining words: geoapi.pt gives "Albergaria-a-velha" and
    "Arruda Dos Vinhos"."""
    return re.sub(r"[^\W\d_]+", lambda m: m[0].lower() if m.start() and m[0].lower() in JOINS
                  else m[0][:1].upper() + m[0][1:], name)


def get(url):
    resp = requests.get(url, timeout=120)
    resp.raise_for_status()
    return resp.json()


def main():
    municipalities = sorted(get(f"{GEOAPI}/municipios"))
    districts = sorted(d["distrito"] for d in get(f"{GEOAPI}/distritos"))
    forecast = sorted(p["local"] for p in get(IPMA)["data"])
    by_key = {}
    for name in municipalities + districts + forecast:    # the CAOP spelling wins over IPMA's
        if "(" not in name:     # "Lagoa (açores)" is the key geoapi.pt gives the second Lagoa; no question says it
            by_key.setdefault(fold(name), proper(name))
    names = sorted(by_key.values())
    print(f"municipalities {len(municipalities)}, districts and islands {len(districts)}, IPMA places {len(forecast)}, "
          f"names {len(names)}")
    OUT.write_text(json.dumps({"source": "geoapi.pt (CAOP) and IPMA", "capitalised_only": COMMON, "names": names}, ensure_ascii=False, indent=0)
                   + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
