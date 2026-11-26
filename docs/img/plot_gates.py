"""AUROC of each gate model and the word-overlap baseline, from benchmarks/*.json."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from plot_style import save

ROOT = Path(__file__).resolve().parents[2]
RUNS = {"Jevásio-0.8B-v2": "jevasio-0.8b-v2", "Kev-0.8B": "kev-0.8b"}
SUBSETS = ["select/all", "sufficient/easy", "sufficient/hard", "grounded/easy", "grounded/hard"]
LABELS = {"select/all": "Select, all", "sufficient/easy": "Sufficient, easy", "sufficient/hard": "Sufficient, hard",
          "grounded/easy": "Grounded, easy", "grounded/hard": "Grounded, hard"}


def load():
    reports = {name: json.loads((ROOT / f"benchmarks/{stem}.json").read_text(encoding="utf-8"))["report"]
               for name, stem in RUNS.items()}
    first = next(iter(reports.values()))
    series = {name: [r[s]["model_auroc"] for s in SUBSETS] for name, r in reports.items()}
    series["Baseline"] = [first[s]["baseline_auroc"] for s in SUBSETS]
    return {"subsets": [LABELS[s] for s in SUBSETS], "cases": [first[s]["cases"] for s in SUBSETS], "series": series}


def alt(data):
    hard = data["subsets"].index("Grounded, hard")
    s = data["series"]
    return (f"AUROC by gate on eval/gates_pt.json. On the hard grounding cases Jevásio-0.8B-v2 scores "
            f"{s['Jevásio-0.8B-v2'][hard]:.3f}, Kev-0.8B {s['Kev-0.8B'][hard]:.3f} and the word-overlap baseline "
            f"{s['Baseline'][hard]:.3f}; on the easy cases the baseline is as good as a model.")


def draw(fig, theme, data):
    ax = fig.add_subplot()
    ys = range(len(data["subsets"]))
    for i, (name, values) in enumerate(data["series"].items()):
        ax.scatter(values, [y + (i - 1) * 0.22 for y in ys], s=40, label=name, zorder=3)
    ax.set_yticks(list(ys), [f"{s} ({n})" for s, n in zip(data["subsets"], data["cases"])])
    ax.invert_yaxis()
    ax.set_xlim(0, 1.02)
    ax.axvline(0.5, color=theme["muted"], linewidth=1, linestyle=":")
    ax.set_xlabel("AUROC (0.5 is chance)")
    ax.set_title("Gate AUROC, cases in brackets", loc="left")
    ax.legend(loc="lower left", frameon=False)


if __name__ == "__main__":
    data = load()
    save(str(ROOT / "docs/img/gates-auroc"), draw, data)
    print(alt(data))
