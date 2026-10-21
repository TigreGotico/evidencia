"""Score the three gates on eval/gates_pt.json, for one decision model and for a lexical baseline.

    python scripts/gate_eval.py jaredpalmer/kev-0.8b results.json

The baseline needs no model: BM25 score for select, the share of question words found in the evidence for
sufficiency, and the share of answer words found in the evidence for grounding. AUROC is threshold-free. The
model's accuracy is read at 0.5; the baseline has no natural threshold, so its accuracy is read at the best
threshold on these same cases, which favours the baseline.
"""
import json
import re
import sys
from pathlib import Path

from evidencia import Collection, Gates, LocalDecision

CASES = json.loads((Path(__file__).parent.parent / "eval" / "gates_pt.json").read_text(encoding="utf-8"))
STOP = set("a o as os de do da dos das em no na nos nas um uma e é que qual quem onde quando como por pelo pela "
           "ao à se foi tem com para".split())


def words(text):
    return {w for w in re.findall(r"\w+", text.lower()) if w not in STOP and len(w) > 1}


def coverage(needle, haystack):
    n = words(needle)
    return len(n & words(" ".join(haystack))) / len(n) if n else 0.0


def build_cases():
    corpus = CASES["corpus"]
    select, sufficient, grounded = [], [], []
    for q in CASES["answerable"]:
        for c in q["gold"]:
            select.append((q["id"], q["question"], corpus[c], 1))
        select.append((q["id"], q["question"], corpus[q["distractor"]], 0))
        ev = [corpus[c] for c in q["gold"]]
        sufficient.append((q["id"], q["question"], ev, 1))
        if "partial" in q:
            sufficient.append((q["id"] + "-partial", q["question"], [corpus[c] for c in q["partial"]], 0))
        grounded.append((q["id"] + "-right", q["question"], ev, q["right"], 1))
        grounded.append((q["id"] + "-wrong", q["question"], ev, q["wrong"], 0))
    for w in CASES["wrong_evidence"]:
        sufficient.append((w["id"], w["question"], [corpus[c] for c in w["evidence"]], 0))
    for u in CASES["unanswerable"]:
        select.append((u["id"], u["question"], corpus[u["related"]], 0))
        sufficient.append((u["id"], u["question"], [corpus[u["related"]]], 0))
    for h in CASES["hard_grounding"]:
        ev = [corpus[c] for c in h["evidence"]]
        grounded.append(("hard-" + h["id"] + "-right", h["question"], ev, h["right"], 1))
        grounded.append(("hard-" + h["id"] + "-wrong", h["question"], ev, h["wrong"], 0))
    for h in CASES["hard_sufficiency"]:
        sufficient.append(("hard-" + h["id"], h["question"], [corpus[c] for c in h["evidence"]], h["label"]))
    return select, sufficient, grounded


def scores(model, base, labels):
    return {"cases": len(labels), "positives": sum(labels),
            "model_auroc": round(auroc(model, labels), 3),
            "model_accuracy_at_0.5": round(accuracy(model, labels, 0.5), 3),
            "baseline_auroc": round(auroc(base, labels), 3),
            "baseline_best_accuracy": round(best_accuracy(base, labels), 3)}


def auroc(scores, labels):
    pos = [s for s, y in zip(scores, labels) if y]
    neg = [s for s, y in zip(scores, labels) if not y]
    wins = sum(1.0 if p > n else 0.5 if p == n else 0.0 for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def accuracy(scores, labels, threshold):
    return sum((s >= threshold) == bool(y) for s, y in zip(scores, labels)) / len(labels)


def best_accuracy(scores, labels):
    return max(accuracy(scores, labels, t) for t in sorted(set(scores)) + [float("inf")])


def main(run, out):
    select, sufficient, grounded = build_cases()
    col = Collection("eval")
    col.add("\n\n".join(CASES["corpus"].values()), "corpus")
    gates = Gates(LocalDecision(run))
    rows, report = [], {}
    for gate, cases in (("select", select), ("sufficient", sufficient), ("grounded", grounded)):
        model, base, labels, hard = [], [], [], []
        for case in cases:
            cid, question, *rest, label = case
            if gate == "select":
                (passage,) = rest
                p = gates.select(question, passage)
                b = next((h.score for h in col.search(question, len(CASES["corpus"])) if h.text == passage), 0.0)
            elif gate == "sufficient":
                (evidence,) = rest
                p, b = gates.sufficient(question, evidence), coverage(question, evidence)
            else:
                evidence, answer = rest
                p, b = gates.grounded(question, evidence, answer), coverage(answer, evidence)
            model.append(p), base.append(b), labels.append(label), hard.append(cid.startswith("hard-"))
            rows.append({"gate": gate, "case": cid, "label": label, "p": p, "baseline": b})
        for subset, keep in (("all", [True] * len(labels)), ("easy", [not h for h in hard]), ("hard", hard)):
            if sum(keep) and 0 < sum(y for y, k in zip(labels, keep) if k) < sum(keep):
                pick = lambda xs: [x for x, k in zip(xs, keep) if k]
                report[f"{gate}/{subset}"] = scores(pick(model), pick(base), pick(labels))
                print(f"{gate}/{subset}", report[f"{gate}/{subset}"])
    Path(out).write_text(json.dumps({"run": run, "report": report, "rows": rows}, ensure_ascii=False, indent=1))
    print(f"cases in={len(select) + len(sufficient) + len(grounded)} rows out={len(rows)}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
