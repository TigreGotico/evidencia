<!-- agent-hint: Describes Evidencia.context() and Evidencia.answer() in evidencia/pipeline.py and the gate questions in evidencia/locale/pt-PT.json. The round arithmetic, the abstained_at values and the gates values here are tested in test/test_pipeline.py; change the tests and this page together. -->

# Gates

A gate is one yes/no question to the decision model. The model answers with a probability. The gate passes when the probability is equal to or more than its threshold.

| Gate | Question to the model | State sent | Threshold setting |
| --- | --- | --- | --- |
| Select | Does the passage contain information that helps to answer the question? | `question`, `passage` | `select_threshold` |
| Sufficient | Is the evidence enough to answer correctly? | `question`, `evidence` | `sufficient_threshold` |
| Grounded | Is every fact in the answer in the evidence, or does it follow directly from it? | `question`, `evidence`, `answer` | `grounded_threshold` |

The question texts are in `evidencia/locale/pt-PT.json`, in European Portuguese. Each one is a System One `noul` question with an instruction and a description of the true and false cases. The wording is a machine draft. A native speaker has not reviewed it.

All three thresholds are `0.5` by default. These values are not tuned. Refer to [Evaluation](evaluation.md) for the measured trade-offs.

## Retrieval rounds

evidencia retrieves candidates in rounds. Each round retrieves more candidates than the round before it.

1. Round *r* retrieves the best `n_candidates × 2^(r−1)` passages by BM25 score. With the defaults, round 1 retrieves 8 and round 2 retrieves 16.
2. The select gate scores each passage that an earlier round did not score. A passage is scored once per question.
3. The evidence is the `top_k` passages with the highest select probability, from the passages that passed.
4. If no passage passed, the round ends and the next round starts.
5. If the evidence is the same as in the round before, evidencia does not ask the sufficiency question again, because the answer would be the same.
6. Otherwise, the sufficient gate judges the evidence. If it passes, the rounds stop.

The rounds also stop after `max_rounds`, or when a round finds no new candidate.

## Answers

`POST /v1/answer` runs the rounds, then the chat model writes an answer from the evidence, then the grounded gate checks that answer. If a step stops the answer, `answer` is `null`, `abstained_at` names the step, and `message` holds a sentence you can show the user.

| `abstained_at` | Meaning |
| --- | --- |
| `retrieve` | No passage shares a word with the question |
| `select` | Passages were found, but the select gate kept none |
| `sufficient` | The kept evidence is not enough to answer |
| `grounded` | The answer that the chat model wrote is not supported by the evidence |

When the grounded gate stops an answer, `draft` holds the rejected answer for audit. Do not show `draft` to users.

## When the decision backend fails

Two different failures can occur. evidencia handles them differently.

**The backend is unavailable.** It cannot be reached, it returns an error status other than 400, 413 or 422, its reply is malformed, or a local model cannot load. A wrong `decision.key` gives 401, so it also makes the backend unavailable. The `fail_open` setting decides what happens:

| `fail_open` | Result |
| --- | --- |
| `true` (default) | evidencia answers from the best BM25 passages without gates. The response has `"gates": "unavailable"`, and the service logs a warning |
| `false` | The request returns 503 |

If the backend becomes unavailable after the chat model wrote the answer, `fail_open = true` returns the answer without a grounding check and marks the response `"gates": "partial"`.

**The model refuses the request.** The backend read the request and refused its content, for example because the state is longer than the model context. Document content can cause a refusal, so a refusal never turns the gates off, whatever the value of `fail_open`:

- A refused passage is not used as evidence.
- A refused sufficiency question makes the answer abstain at `sufficient`.
- A refused grounding question makes the answer abstain at `grounded`.
- In `/v1/retrieve`, a refused passage has `p_select` `null`.
- A direct call to `/v1/check/grounding` or `/v1/systemone` that the model refuses returns 422.

## The gates field

| `gates` | Meaning |
| --- | --- |
| `applied` | No gate was skipped because of a backend failure. A request that abstains at one gate does not run the gates after it |
| `unavailable` | The backend was unavailable and `fail_open` let the request continue without gates |
| `partial` | The backend became unavailable after generation. The answer has no grounding check |

## Limits

BM25 finds passages by shared words. A large document that repeats the words of a question can fill every candidate round. Then the passage with the answer is not retrieved, and the answer abstains at `select`. The result is safe, but the question goes unanswered.

The gates can only judge what retrieval finds, and a decision model can be wrong. A passed gate is a probability, not a proof.

---
[← Getting started](getting-started.md) · [Home](index.md) · [Decision models →](decision-models.md)
