"""D66: the planner adds the assemble step itself to wide plans."""

from types import SimpleNamespace

from planner.llm import COMBINED_ONLY, ITEM_REQUEST_HEADER, REQUEST_HEADER, GroqPlanner, add_assemble
from planner.schema import ExecutionPlan

REQ = "Write a detailed profile of each of these companies: Apple, NVIDIA and AMD. Then compare them."


def plan(items=("Apple", "NVIDIA", "AMD"), sink_type="compare", extra=()):
    tasks = [{"id": f"t{i}", "type": "research", "title": n, "instruction": f"Profile the company.\nItem: {n}",
              "depends_on": []} for i, n in enumerate(items, 1)]
    tasks.append({"id": f"t{len(items) + 1}", "type": sink_type, "title": "Compare", "instruction": "Compare them.",
                  "depends_on": [t["id"] for t in tasks]})
    return ExecutionPlan.model_validate({"schema_version": 1, "summary": "s", "tasks": tasks + list(extra)})


def test_wide_plan_gets_assemble_and_items_get_the_request_first():
    out = add_assemble(plan(), REQ)
    ids = [t.id for t in out.tasks]
    assert ids == ["t1", "t2", "t3", "t4", "t5"]
    a = out.tasks[-1]
    assert a.type.value == "assemble" and a.depends_on == ["t4", "t1", "t2", "t3"]
    assert out.tasks[3].instruction == "Compare them." + COMBINED_ONLY
    t1, t2 = out.tasks[0].instruction, out.tasks[1].instruction
    assert t1 == ITEM_REQUEST_HEADER + REQ + "\n\nProfile the company.\nItem: Apple"
    assert t1[: t1.index("Item:")] == t2[: t2.index("Item:")]  # the same prefix for every item (D65)


def test_other_shapes_are_unchanged():
    assert add_assemble(plan(items=("Apple", "NVIDIA")), REQ) == plan(items=("Apple", "NVIDIA"))  # < 3 items
    assert add_assemble(plan(sink_type="analyze"), REQ) == plan(sink_type="analyze")
    two_sinks = plan(extra=[{"id": "t9", "type": "research", "title": "x", "instruction": "x", "depends_on": []}])
    assert add_assemble(two_sinks, REQ) == two_sinks
    once = add_assemble(plan(), REQ)
    assert add_assemble(once, REQ) == once  # already has assemble


class _Client:
    def __init__(self, content):
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self._content = content

    def _create(self, **kw):
        self.calls.append(kw)
        msg = SimpleNamespace(content=self._content)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="stop")], usage=None)


def test_planner_adds_it_and_the_request_goes_to_the_combining_task(monkeypatch):
    monkeypatch.delenv("ATS_AUTO_ASSEMBLE", raising=False)
    out = GroqPlanner(model="openai/gpt-oss-120b", client=_Client(plan().model_dump_json())).generate_plan(REQ)
    assert out.tasks[-1].type.value == "assemble"
    assert out.tasks[3].instruction.endswith(REQUEST_HEADER + REQ)  # D61 on the compare task
    assert REQUEST_HEADER not in out.tasks[-1].instruction


def test_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("ATS_AUTO_ASSEMBLE", "0")
    out = GroqPlanner(model="openai/gpt-oss-120b", client=_Client(plan().model_dump_json())).generate_plan(REQ)
    assert all(t.type.value != "assemble" for t in out.tasks)
