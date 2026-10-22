"""Run the gates on eval/regressions_pt.json and say, for each row, whether the gate decides as the label says at
the default threshold.

    python scripts/regressions.py TigreGotico/jevasio-0.8b-v2

Each row is an answer from a live run that the gates passed and should have stopped. A row that still passes is
a known weakness of the decision model, not a test failure.
"""
import json
import sys
from pathlib import Path

from evidencia import Config, Gates, LocalDecision

ROWS = json.loads((Path(__file__).parent.parent / "eval" / "regressions_pt.json").read_text(encoding="utf-8"))["rows"]


def main(run):
    config, gates = Config(), Gates(LocalDecision(run))
    right = 0
    for row in ROWS:
        if row["gate"] == "sufficient":
            p, threshold = gates.sufficient(row["question"], row["evidence"]), config.sufficient_threshold
        else:
            p, threshold = gates.grounded(row["question"], row["evidence"], row["answer"]), config.grounded_threshold
        verdict = (p >= threshold) == bool(row["label"])
        right += verdict
        print(f"{row['id']} {row['gate']} p={p:.3f} threshold={threshold} label={row['label']} "
              f"{'right' if verdict else 'wrong'}: {row['question']}")
    print(f"rows in={len(ROWS)} scored={len(ROWS)} right={right}")


if __name__ == "__main__":
    main(sys.argv[1])
