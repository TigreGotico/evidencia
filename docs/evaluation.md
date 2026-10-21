<!-- agent-hint: Backed by eval/gates_pt.json, scripts/gate_eval.py and scripts/parity.py. The result tables are outputs of gate_eval.py on that case file; re-run the script and replace the tables together when the cases, the gate questions or a model change. Never edit a number by hand. -->

# Evaluation

The gate thresholds decide how often evidencia answers and how often it is wrong. Measure them on questions like the ones that your users ask. This page describes the case file and the two scripts in the repository.

## The case file

`eval/gates_pt.json` holds pt-PT cases over a corpus of 12 short passages about Portugal. One person labelled the cases from the passage text. No second person reviewed them.

| Gate | Cases | Built from |
| --- | --- | --- |
| Select | 42 | The passage that answers each question, a related passage that does not, and passages for unanswerable questions |
| Sufficient | 40 | Full evidence, partial evidence for two-step questions, wrong evidence, unanswerable questions, and hard cases |
| Grounded | 44 | A right and a wrong answer for each question, and hard cases |

The hard cases defeat word overlap. The wrong answers use words from the evidence, the unanswerable questions use mostly words that the evidence contains, and the answerable questions are paraphrased.

## Gate scores

```bash
.venv/bin/python scripts/gate_eval.py TigreGotico/jevasio-0.8b-v2 results.json
```

The script runs every case through the local backend and writes each probability to `results.json`. It reports AUROC, which does not depend on a threshold, and the accuracy at 0.5. It also scores a baseline that uses no model: the BM25 score for select, and the share of question or answer words that occur in the evidence for the other gates. The baseline accuracy is read at its best threshold on the same cases, which helps the baseline.

AUROC on `eval/gates_pt.json`:

| Gate, subset | Cases | Jevásio-0.8B-v2 | Kev-0.8B | Baseline |
| --- | --- | --- | --- | --- |
| Select, all | 42 | 0.978 | 0.918 | 0.909 |
| Sufficient, easy | 30 | 0.955 | 0.821 | 0.973 |
| Sufficient, hard | 10 | 0.840 | 0.960 | 0.060 |
| Grounded, easy | 28 | 0.995 | 0.913 | 1.000 |
| Grounded, hard | 16 | 0.953 | 0.812 | 0.500 |

On the easy cases, word overlap is as good as a model. On the hard cases, word overlap fails, and the models stay useful.

## Choosing the grounded threshold

Jevásio ranks the grounding cases well, but it gives high probabilities to some wrong answers. The threshold decides how many of them pass. On the 44 grounding cases, 22 right and 22 wrong:

| Threshold | Jevásio: wrong answers passed | Jevásio: right answers blocked | Kev: wrong answers passed | Kev: right answers blocked |
| --- | --- | --- | --- | --- |
| 0.5 | 10 | 0 | 8 | 1 |
| 0.7 | 5 | 0 | 4 | 4 |
| 0.8 | 3 | 0 | 3 | 9 |
| 0.9 | 3 | 0 | 0 | 16 |

These counts come from the same cases that they describe, so they overstate how well a threshold works on new questions. Use them to choose a starting point, then measure on your own cases.

Both models can call partial evidence sufficient for a question that needs two facts. More retrieval rounds do not help when the first round already passes.

## Backend parity

```bash
.venv/bin/python scripts/parity.py dump  http://127.0.0.1:8008 server.json
.venv/bin/python scripts/parity.py check TigreGotico/jevasio-0.8b-v2 server.json fp32
```

`dump` sends fixed requests to a System One server and stores its answers. `check` sends the same requests to the local backend and compares the probabilities. It exits with an error when a probability differs by more than 0.02. With Kev-0.8B in fp32, the local backend and the reference server agree to four decimals. In bf16, they differ by less than 0.01, because the reference server uses CUDA graphs and the local backend does not.

---
[← Python](python.md) · [Home](index.md) · [Troubleshooting →](troubleshooting.md)
