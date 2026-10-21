<!-- agent-hint: Backed by evidencia/decision.py (HttpDecision, LocalDecision, REJECTED_STATUS) and DecisionConfig in evidencia/config.py. The licence statements are legal facts: never soften the non-commercial terms of Jevásio. The grounding probabilities in the comparison table come from a measured probe; re-measure before changing them. -->

# Decision models

The decision model answers the gate questions. evidencia works with any model that uses the System One request shape, through one of two backends:

- **Local:** the model runs inside the evidencia process. This needs the `local` extra.
- **HTTP:** evidencia sends each question to a decision server.

## Models

| Model | Licence | Use |
| --- | --- | --- |
| [Jevásio-0.8B-v2](https://huggingface.co/TigreGotico/jevasio-0.8b-v2) | CC BY-NC-SA 4.0 | Default. Trained on pt-PT decisions. Research and non-commercial use only |
| [Kev-0.8B](https://huggingface.co/jaredpalmer/kev-0.8b) | Apache-2.0 | Commercial use is allowed. Trained on English decisions |

The licence of the model applies to what you do with it. A deployment with Jevásio is for research and non-commercial use only. To use Kev, set `decision.run = "jaredpalmer/kev-0.8b"`.

Kev-0.8B is weaker at the grounded gate, mainly when the evidence is short. With the evidence "Lisboa é a capital de Portugal." and the question "Qual é a capital de Portugal?", the two models give these grounding probabilities, in fp32:

| Answer | Jevásio-0.8B-v2 | Kev-0.8B |
| --- | --- | --- |
| "Lisboa." | 0.996 | 0.960 |
| "O Porto." | 0.101 | 0.876 |
| "Porto, inventado." | 0.008 | 0.841 |
| "Coimbra." | 0.028 | 0.873 |

On this example, only a Kev threshold above 0.876 blocks the wrong answers. On the evaluation cases, a Kev threshold of 0.9 blocks every wrong answer, and 16 of 22 right answers too. If you use Kev, measure the grounded threshold on your own questions first. Refer to [Evaluation](evaluation.md).

## The System One request shape

A request holds a state and one or more typed questions. evidencia asks one `noul` (yes/no) question per request:

```json
{
  "state": {"question": "Qual é a capital de Portugal?", "evidence": ["Lisboa é a capital de Portugal."], "answer": "Lisboa."},
  "model": "kev-latest",
  "questions": {
    "grounded": {
      "type": "noul",
      "instructions": "A resposta é suportada pela evidência? ...",
      "criteria": {"true": "...", "false": "..."}
    }
  }
}
```

The response maps each question to its answer:

```json
{"model": "kev-latest", "answers": {"grounded": {"type": "noul", "noul": 0.9959}}}
```

`noul` must be a number from 0 to 1. evidencia treats any other value as a malformed reply.

## Local backend

```toml
[decision]
backend = "local"
run = "TigreGotico/jevasio-0.8b-v2"   # a Hugging Face model id, or a local checkpoint directory
device = "auto"                        # auto, cpu, cuda or mps
```

- The model loads on the first gate question, not when the service starts.
- `device = "auto"` uses CUDA when it is available and the CPU when it is not.
- The default precision is bf16 on CUDA and fp32 on other devices. Set `decision.dtype` to `bf16` or `fp32` to choose.
- If the model cannot load, the backend is unavailable. After that first failed load, `/health` reports `degraded`. The next question tries to load the model again.

The inference code is four modules of [Kev](https://github.com/jaredpalmer/kev) in `evidencia/_vendor/kev/`, under the Apache License 2.0. They run on plain PyTorch, without CUDA graphs or fused kernels.

## HTTP backend

```toml
[decision]
backend = "http"
url = "http://127.0.0.1:8008"   # the server root
key = "secret"                  # optional bearer token
model = "kev-latest"            # model name sent in each request
timeout = 30
```

evidencia sends `POST {url}/v1/systemone` for each question, and `GET {url}/v1/models` for health checks. The server decides which checkpoint answers.

evidencia sorts the server's replies as follows:

| Reply | Treated as |
| --- | --- |
| 200 with an answer for every question | An answer |
| 400, 413 or 422 | A refusal. The gate that asked abstains. Refer to [Gates](gates.md) |
| Any other error status, no connection, a timeout, or a malformed body | The backend is unavailable. `fail_open` applies |

## One instance as the backend of another

An evidencia instance serves its own decision model on `POST /v1/systemone` and names it on `GET /v1/models`. A second instance can use the first as its HTTP backend:

```toml
[decision]
backend = "http"
url = "http://gpu-host:8080"
key = "the first instance's api_key"
```

The first instance needs `decision.backend = "local"`. This puts the model on one machine with a GPU and lets other instances share it.

---
[← Gates](gates.md) · [Home](index.md) · [Chat models →](generators.md)
