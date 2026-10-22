import pytest

from conftest import StubChat, StubDecision
from evidencia import DecisionUnavailable

Q = "Qual é a capital de Portugal?"
CAPITAL = "Lisboa é a capital de Portugal desde o século XIII."


def test_select_keeps_only_passing_passages(make_engine):
    ctx = make_engine().context(Q, "geo")
    assert [s.passage.text for s in ctx.evidence] == [CAPITAL]
    assert ctx.evidence[0].p_select == 0.9
    rejected = [s for s in ctx.candidates if s.passage.text != CAPITAL]
    assert rejected and all(s.p_select == 0.1 for s in rejected)
    assert ctx.sufficient.p == 0.9 and ctx.sufficient.passed
    assert ctx.rounds == 1 and not ctx.abstain


def test_top_k_orders_by_select_probability(make_engine):
    decision = StubDecision(keyword="século")   # two passages mention a century
    ctx = make_engine(decision=decision, top_k=1).context(Q, "geo")
    assert len(ctx.evidence) == 1
    assert "século" in ctx.evidence[0].passage.text


def test_widening_runs_a_second_round_with_more_candidates(make_engine):
    decision = StubDecision(keyword="", sufficient=[0.2, 0.8])     # every passage is selected
    ctx = make_engine(decision=decision, n_candidates=1, max_rounds=3).context(Q, "geo")
    assert ctx.rounds == 2
    assert decision.count("sufficient") == 2
    assert ctx.sufficient.p == 0.8
    # round one scored one passage, round two only the new ones: no passage is scored twice
    assert decision.count("select") == len(ctx.candidates) == 2


def test_widening_stops_when_the_collection_is_exhausted(make_engine):
    decision = StubDecision(sufficient=0.1)
    ctx = make_engine(decision=decision, n_candidates=8, max_rounds=5).context(Q, "geo")
    assert decision.count("sufficient") == 1
    assert ctx.rounds == 1 and ctx.abstain


def test_insufficient_evidence_abstains_before_generation(make_engine):
    chat = StubChat()
    result = make_engine(decision=StubDecision(sufficient=0.3), chat=chat).answer(Q, "geo")
    assert result["answer"] is None
    assert result["abstained_at"] == "sufficient"
    assert result["message"] == "Não tenho informação suficiente para responder a essa pergunta."
    assert chat.prompts == []


def test_no_selected_passage_abstains_without_asking_sufficiency(make_engine):
    decision = StubDecision(keyword="nenhuma-passagem-tem-isto")
    result = make_engine(decision=decision).answer(Q, "geo")
    assert result["abstained_at"] == "select"
    assert result["sufficient"] is None
    assert decision.count("sufficient") == 0


def test_grounded_answer_is_returned(make_engine):
    chat = StubChat("Lisboa.")
    result = make_engine(chat=chat).answer(Q, "geo")
    assert result["answer"] == "Lisboa."
    assert result["abstained_at"] is None
    assert result["grounded"] == {"p": 0.9, "threshold": 0.8, "passed": True}
    assert chat.prompts == [f"Evidência:\n[1] {CAPITAL}\n\nPergunta: {Q}"]


def test_ungrounded_answer_is_suppressed(make_engine):
    result = make_engine(decision=StubDecision(grounded=0.2), chat=StubChat("Paris.")).answer(Q, "geo")
    assert result["answer"] is None
    assert result["abstained_at"] == "grounded"
    assert result["draft"] == "Paris."
    assert result["grounded"]["passed"] is False


def test_threshold_is_inclusive(make_engine):
    result = make_engine(decision=StubDecision(sufficient=0.5, grounded=0.8)).answer(Q, "geo")
    assert result["answer"] == "Lisboa."
    below = make_engine(decision=StubDecision(sufficient=0.5, grounded=0.79)).answer(Q, "geo")
    assert (below["answer"], below["abstained_at"]) == (None, "grounded")


def test_fail_open_answers_ungated(make_engine):
    chat = StubChat()
    result = make_engine(decision=StubDecision(down=True), chat=chat, top_k=2).answer(Q, "geo")
    assert result["gates"] == "unavailable"
    assert result["answer"] == "Lisboa."
    assert result["grounded"] is None
    assert len(result["evidence"]) == 2
    assert all(p["p_select"] is None for p in result["evidence"])
    assert result["evidence"][0]["text"] == CAPITAL   # the best BM25 hit


def test_fail_closed_raises(make_engine):
    engine = make_engine(decision=StubDecision(down=True), fail_open=False)
    with pytest.raises(DecisionUnavailable):
        engine.answer(Q, "geo")


def test_unknown_collection_raises_key_error(make_engine):
    with pytest.raises(KeyError):
        make_engine().context(Q, "nope")


def test_same_evidence_is_not_asked_twice(make_engine):
    decision = StubDecision(sufficient=[0.2, 0.9, 0.9])
    ctx = make_engine(decision=decision, n_candidates=1, max_rounds=3).context(Q, "geo")
    # only one passage ever passes select, so later rounds bring no new evidence to judge
    assert decision.count("sufficient") == 1
    assert ctx.abstain and ctx.sufficient.p == 0.2


def test_nothing_retrieved_abstains_at_retrieve(make_engine):
    result = make_engine().answer("xyzzy plugh", "geo")
    assert result["abstained_at"] == "retrieve" and result["candidates"] == []


def test_refused_passage_is_skipped_and_gates_stay_on(make_engine):
    """Content that makes the model refuse a request must not switch the gates off, even with fail_open."""
    engine = make_engine(decision=StubDecision(grounded=0.1), fail_open=True)
    engine.collection("geo").add("ENORME capital " * 10, "huge.txt")
    result = engine.answer(Q, "geo")
    assert result["gates"] == "applied"
    assert [p["source"] for p in result["evidence"]] == ["geo.txt"]
    assert result["answer"] is None and result["abstained_at"] == "grounded"


@pytest.mark.parametrize("fail_open", [True, False])
def test_refused_sufficiency_question_abstains(make_engine, fail_open):
    chat = StubChat()
    result = make_engine(decision=StubDecision(reject={"sufficient"}), chat=chat, fail_open=fail_open).answer(Q, "geo")
    assert result["gates"] == "applied" and result["abstained_at"] == "sufficient"
    assert chat.prompts == []


@pytest.mark.parametrize("fail_open", [True, False])
def test_refused_grounding_question_suppresses_the_answer(make_engine, fail_open):
    result = make_engine(decision=StubDecision(reject={"grounded"}), fail_open=fail_open).answer(Q, "geo")
    assert result["answer"] is None and result["abstained_at"] == "grounded"
    assert result["draft"] == "Lisboa." and result["gates"] == "applied"


def test_grounding_backend_down_with_fail_open_is_partial(make_engine):
    result = make_engine(decision=StubDecision(down_for={"grounded"}), fail_open=True).answer(Q, "geo")
    assert result["gates"] == "partial"
    assert result["answer"] == "Lisboa." and result["grounded"] is None


def test_grounding_backend_down_with_fail_closed_raises(make_engine):
    with pytest.raises(DecisionUnavailable):
        make_engine(decision=StubDecision(down_for={"grounded"}), fail_open=False).answer(Q, "geo")


@pytest.mark.parametrize("value", [float("nan"), True, 7.0, -0.1, "0.9"])
def test_probability_outside_unit_interval_is_unavailable(value):
    from evidencia import Gates

    class Bad:
        def ask(self, state, questions):
            return {"select": {"type": "noul", "noul": value}}

    with pytest.raises(DecisionUnavailable):
        Gates(Bad()).select(Q, "Lisboa é a capital.")


def test_collections_survive_concurrent_creation(make_engine):
    import sys
    import threading
    from concurrent.futures import ThreadPoolExecutor
    engine = make_engine()
    old = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    # the threads start once: a thread started under a coverage tracer can die before it runs its target
    with ThreadPoolExecutor(4) as pool:
        try:
            for trial in range(50):
                name = f"c{trial}"
                start = threading.Barrier(4)

                def add(i, name=name, start=start):
                    start.wait()
                    engine.collection(name, create=True).add(f"texto {i}", f"{i}.txt")

                list(pool.map(add, range(4)))
                assert len(engine.collection(name).passages) == 4, trial
        finally:
            sys.setswitchinterval(old)
