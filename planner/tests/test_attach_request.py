"""D61: the user's request reaches the final task; the planner call uses low reasoning."""

from types import SimpleNamespace

from planner.llm import MAX_INSTRUCTION_CHARS, REQUEST_HEADER, GroqPlanner, attach_request
from planner.schema import ExecutionPlan


def _plan(final_instruction="Write the answer."):
    return ExecutionPlan.model_validate({"schema_version": 1, "summary": "s", "tasks": [
        {"id": "t1", "type": "research", "title": "A", "instruction": "Research A.", "depends_on": []},
        {"id": "t2", "type": "research", "title": "B", "instruction": "Research B.", "depends_on": []},
        {"id": "t3", "type": "report", "title": "Answer", "instruction": final_instruction, "depends_on": ["t1", "t2"]}]})


def test_request_goes_to_the_final_task_only():
    req = "Which is deeper? Options: (A) X (B) Y"
    out = attach_request(_plan(), req)
    assert out.tasks[0].instruction == "Research A." and out.tasks[1].instruction == "Research B."
    assert out.tasks[2].instruction == "Write the answer." + REQUEST_HEADER + req
    assert attach_request(out, req) == out  # already there: unchanged


def test_long_request_is_cut_to_the_instruction_limit():
    out = attach_request(_plan(), "word " * 1000)
    assert len(out.tasks[2].instruction) <= MAX_INSTRUCTION_CHARS
    assert out.tasks[2].instruction.endswith("...")
    full = attach_request(_plan("x" * 1990), "question?")
    assert full.tasks[2].instruction == "x" * 1990  # no room: left as the planner wrote it


class _Client:
    def __init__(self, content):
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self._content = content

    def _create(self, **kw):
        self.calls.append(kw)
        msg = SimpleNamespace(content=self._content)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="stop")], usage=None)


def test_groq_planner_attaches_and_uses_low_reasoning(monkeypatch):
    monkeypatch.delenv("ATS_PLANNER_REASONING", raising=False)
    monkeypatch.delenv("ATS_ATTACH_REQUEST", raising=False)
    client = _Client(_plan().model_dump_json())
    plan = GroqPlanner(model="openai/gpt-oss-120b", client=client).generate_plan("Which is deeper?")
    assert plan.tasks[-1].instruction.endswith("Which is deeper?")
    assert client.calls[0]["reasoning_effort"] == "low"


def test_attach_and_reasoning_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("ATS_PLANNER_REASONING", "")
    monkeypatch.setenv("ATS_ATTACH_REQUEST", "0")
    client = _Client(_plan().model_dump_json())
    plan = GroqPlanner(model="openai/gpt-oss-120b", client=client).generate_plan("Which is deeper?")
    assert plan.tasks[-1].instruction == "Write the answer."
    assert "reasoning_effort" not in client.calls[0]
