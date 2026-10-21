"""Compare a decision backend's answers with a stored reference, question by question.

    python scripts/parity.py dump  http://127.0.0.1:8008 out.json        # answers from a System One server
    python scripts/parity.py check TigreGotico/jevasio-0.8b-v2 out.json [fp32]  # same requests, in process

Prints the largest probability difference and exits non-zero above the tolerance.
"""
import json
import sys

from evidencia import HttpDecision, LocalDecision
from evidencia.gates import load_locale

LOCALE = load_locale("pt-PT")
EVIDENCE = ["Lisboa é a capital de Portugal desde o século XIII.",
            "A Torre de Belém foi construída no século XVI, junto ao rio Tejo."]
REQUESTS = [
    ({"question": "Qual é a capital de Portugal?", "passage": EVIDENCE[0]}, {"select": LOCALE["select"]}),
    ({"question": "Qual é a capital de Portugal?", "passage": EVIDENCE[1]}, {"select": LOCALE["select"]}),
    ({"question": "Qual é a capital de Portugal?", "evidence": EVIDENCE}, {"sufficient": LOCALE["sufficient"]}),
    ({"question": "Quem construiu a Torre de Belém?", "evidence": EVIDENCE}, {"sufficient": LOCALE["sufficient"]}),
    ({"question": "Qual é a capital de Portugal?", "evidence": EVIDENCE, "answer": "Lisboa."},
     {"grounded": LOCALE["grounded"]}),
    ({"question": "Qual é a capital de Portugal?", "evidence": EVIDENCE, "answer": "O Porto."},
     {"grounded": LOCALE["grounded"]}),
    ("Encomenda chegou atrasada e cobraram duas vezes.",
     {"area": {"type": "choice", "instructions": "Que equipa trata isto?",
               "criteria": {"envios": "Atrasos e entregas", "faturacao": "Cobranças e pagamentos"}},
      "zanga": {"type": "score", "instructions": "Quão zangado está o cliente?",
                "criteria": ["Calmo", "Irritado", "Furioso"]}}),
]
TOLERANCE = 0.02


def probabilities(answers):
    out = {}
    for qid, a in answers.items():
        if a["type"] == "noul":
            out[qid] = [a["noul"]]
        else:
            out[qid] = [a["probabilities"][k] for k in sorted(a["probabilities"])]
    return out


def main(mode, target, path, dtype=None):
    if mode == "dump":
        decision = HttpDecision(target, timeout=300)
        answers = [decision.ask(s, q) for s, q in REQUESTS]
        json.dump({"target": target, "answers": answers}, open(path, "w"), ensure_ascii=False, indent=1)
        print(f"requests in={len(REQUESTS)} answers out={len(answers)}")
        return 0
    ref = json.load(open(path))["answers"]
    local = LocalDecision(target, dtype=dtype)
    worst, compared = 0.0, 0
    for (state, questions), expected in zip(REQUESTS, ref):
        got = local.ask(state, questions)
        for qid, ps in probabilities(expected).items():
            mine = probabilities(got)[qid]
            diff = max(abs(a - b) for a, b in zip(ps, mine))
            worst = max(worst, diff)
            compared += 1
            print(f"{qid:10} server={ps} local={mine} max|dp|={diff:.4f}")
    print(f"requests in={len(REQUESTS)} questions compared={compared} worst max|dp|={worst:.4f} tolerance={TOLERANCE}")
    return 0 if worst <= TOLERANCE else 1


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:5]))
