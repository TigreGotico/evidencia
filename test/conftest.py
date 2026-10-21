import pytest

from evidencia import Config, DecisionUnavailable, Evidencia
from evidencia.decision import DecisionRejected

DOC = ("Lisboa é a capital de Portugal desde o século XIII.\n\n"
       "O Porto fica no norte do país e é conhecido pelo vinho do Porto.\n\n"
       "A Torre de Belém foi construída no século XVI, junto ao rio Tejo.\n\n"
       "Coimbra tem uma das universidades mais antigas da Europa.")


class StubDecision:
    """Answers by rule: select passes passages containing ``keyword``; the other gates return fixed values.

    ``sufficient`` may be a list, consumed one value per call, to script the widening rounds.
    """

    def __init__(self, keyword="capital", sufficient=0.9, grounded=0.9, down=False, reject=(), down_for=()):
        self.keyword = keyword
        self.sufficient = sufficient
        self.grounded = grounded
        self.down = down
        self.reject = set(reject)       # gates whose request the model refuses, as for an overlong state
        self.down_for = set(down_for)   # gates for which the backend is unreachable
        self.calls = []

    def ask(self, state, questions):
        if self.down:
            raise DecisionUnavailable("stub is down")
        (gate,) = questions
        self.calls.append((gate, state))
        if gate in self.reject or state.get("passage", "").startswith("ENORME"):
            raise DecisionRejected(f"{gate}: state too long")
        if gate in self.down_for:
            raise DecisionUnavailable(f"stub is down for {gate}")
        if gate == "select":
            p = 0.9 if self.keyword in state["passage"] else 0.1
        elif gate == "sufficient":
            p = self.sufficient.pop(0) if isinstance(self.sufficient, list) else self.sufficient
        else:
            p = self.grounded
        return {gate: {"type": "noul", "noul": p}}

    def describe(self):
        return {"backend": "stub", "reachable": not self.down}

    def count(self, gate):
        return sum(1 for g, _ in self.calls if g == gate)


class StubChat:
    def __init__(self, reply="Lisboa."):
        self.reply = reply
        self.prompts = []

    def continue_chat(self, messages, session_id="default", lang=None, units=None, tools=None):
        from ovos_plugin_manager.templates.agents import AgentMessage, MessageRole
        self.prompts.append(messages[-1].content)
        return AgentMessage(MessageRole.ASSISTANT, self.reply)


@pytest.fixture
def make_engine():
    def make(decision=None, chat=None, **cfg):
        engine = Evidencia(Config(**cfg), decision=decision or StubDecision(), chat=chat or StubChat())
        engine.collection("geo", create=True).add(DOC, "geo.txt")
        return engine
    return make
