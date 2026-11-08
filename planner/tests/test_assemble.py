"""D64: assemble, the Python copy of atsd's result, and the planner's handling of an assemble sink."""

from planner.assemble import assemble_output
from planner.llm import REQUEST_HEADER, attach_request
from planner.schema import ExecutionPlan
from planner.validate import validate_plan


def test_same_text_as_atsd():
    # The same inputs as scheduler/tests/test_service.cpp ServiceAssemble.JoinsTheInputsInsideAtsdWithoutDispatching.
    out = assemble_output([
        ("t3", "Comparison", {"content": "NVIDIA grew faster.", "key_points": ["NVIDIA ahead"], "final_answer": "NVIDIA"}),
        ("t1", "Apple", {"content": "Apple makes phones.", "key_points": ["phones"], "final_answer": None}),
        ("t2", "NVIDIA", {"content": "## NVIDIA (chips)\n\nNVIDIA makes GPUs.", "key_points": ["GPUs"]}),
    ])
    assert out == {
        "content": "## Comparison\n\nNVIDIA grew faster.\n\n## Apple\n\nApple makes phones.\n\n## NVIDIA (chips)\n\nNVIDIA makes GPUs.",
        "key_points": ["NVIDIA ahead", "phones", "GPUs"],
        "final_answer": "NVIDIA",
    }


def test_artifacts_and_key_point_cap():
    out = assemble_output([("t1", "Big", {"artifact": "req-t1-1.json", "bytes": 70000}),
                           ("t2", "Many", {"content": "x", "key_points": [f"p{i}" for i in range(40)]})])
    assert "stored as an artifact" in out["content"] and len(out["key_points"]) == 30
    assert out["final_answer"] is None


def _plan():
    return ExecutionPlan.model_validate({"schema_version": 1, "summary": "s", "tasks": [
        {"id": "t1", "type": "research", "title": "Apple", "instruction": "Profile Apple.", "depends_on": []},
        {"id": "t2", "type": "research", "title": "NVIDIA", "instruction": "Profile NVIDIA.", "depends_on": []},
        {"id": "t3", "type": "compare", "title": "Comparison", "instruction": "Compare them.", "depends_on": ["t1", "t2"]},
        {"id": "t4", "type": "assemble", "title": "Full answer", "instruction": "Join.", "depends_on": ["t3", "t1", "t2"]}]})


def test_request_goes_to_the_combining_task_not_the_assemble_sink():
    out = attach_request(_plan(), "Profile Apple and NVIDIA, then compare them.")
    by = {t.id: t.instruction for t in out.tasks}
    assert by["t3"] == "Compare them." + REQUEST_HEADER + "Profile Apple and NVIDIA, then compare them."
    assert by["t4"] == "Join." and by["t1"] == "Profile Apple."


def test_assemble_rules_in_the_validator():
    assert validate_plan(_plan().model_dump(mode="json")).ok
    bad = {"schema_version": 1, "summary": "s", "tasks": [
        {"id": "t1", "type": "assemble", "title": "x", "instruction": "x", "depends_on": []}]}
    r = validate_plan(bad)
    assert not r.ok and [i.code.value for i in r.errors] == ["SCHEMA"] and r.errors[0].task_id == "t1"
    # an assemble task's inputs overlap on purpose: no TRANSITIVE_EDGE warning for it
    assert not [i for i in validate_plan(_plan().model_dump(mode="json")).warnings if i.code.value == "TRANSITIVE_EDGE"]


def test_planner_prompt_asks_for_identical_item_instructions():  # D65
    from planner.llm import load_system_prompt

    text = load_system_prompt()
    assert "SAME instruction text word for word" in text and '"Item: <name>"' in text
