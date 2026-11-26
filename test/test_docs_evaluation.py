"""docs/evaluation.md: every table cell, the figure data and its alt text equal benchmarks/*.json."""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE = (ROOT / "docs/evaluation.md").read_text(encoding="utf-8")
RUNS = {name: json.loads((ROOT / f"benchmarks/{stem}.json").read_text(encoding="utf-8"))
        for name, stem in {"jev": "jevasio-0.8b-v2", "kev": "kev-0.8b"}.items()}
SUBSETS = {"Select, all": "select/all", "Sufficient, easy": "sufficient/easy", "Sufficient, hard": "sufficient/hard",
           "Grounded, easy": "grounded/easy", "Grounded, hard": "grounded/hard"}


def rows(first_cell):
    return [[c.strip() for c in line.strip("|").split("|")]
            for line in PAGE.splitlines() if re.match(rf"\| {first_cell} ", line)]


def test_auroc_table():
    table = {r[0]: r for r in rows("(Select|Sufficient|Grounded),")}
    assert set(table) == set(SUBSETS)
    for label, key in SUBSETS.items():
        jev, kev = RUNS["jev"]["report"][key], RUNS["kev"]["report"][key]
        assert table[label][1:] == [str(jev["cases"]), f"{jev['model_auroc']:.3f}", f"{kev['model_auroc']:.3f}",
                                    f"{jev['baseline_auroc']:.3f}"], label


def passed_and_blocked(run, threshold):
    grounded = [r for r in RUNS[run]["rows"] if r["gate"] == "grounded"]
    return (sum(r["label"] == 0 and r["p"] >= threshold for r in grounded),
            sum(r["label"] == 1 and r["p"] < threshold for r in grounded))


def test_threshold_table():
    table = rows(r"0\.\d")
    assert [r[0] for r in table] == ["0.5", "0.7", "0.8", "0.9"]
    for r in table:
        t = float(r[0])
        assert r[1:] == [str(n) for n in passed_and_blocked("jev", t) + passed_and_blocked("kev", t)], r[0]


def test_figure_data_and_alt_text():
    committed = (ROOT / "docs/img/gates-auroc.json").read_text(encoding="utf-8")
    done = subprocess.run([sys.executable, "docs/img/plot_gates.py"], cwd=ROOT, capture_output=True, text=True,
                          env={**os.environ, "DOCS_FIGURES_DATA_ONLY": "1"}, check=True)
    fresh = (ROOT / "docs/img/gates-auroc.json").read_text(encoding="utf-8")
    (ROOT / "docs/img/gates-auroc.json").write_text(committed, encoding="utf-8")
    assert fresh == committed
    assert f'alt="{done.stdout.strip()}"' in PAGE
