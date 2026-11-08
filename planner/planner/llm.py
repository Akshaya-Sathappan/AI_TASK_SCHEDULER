"""Thin provider layer: generate_plan(request) -> ExecutionPlan.

One structured-output call to Groq. Structured output guarantees shape only,
so every response goes through validate_plan; nothing is trusted or repaired
silently. Planner.generate_plan runs the bounded repair loop: one retry with
the issue list passed to propose() as `feedback`, then reject.
"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from importlib import resources
from typing import Any

from . import provider
from .schema import MAX_TASKS, ExecutionPlan, IssueCode, TaskType, ValidationIssue
from .validate import validate_plan

DEFAULT_MODEL = "openai/gpt-oss-120b"  # supports Groq strict structured outputs
MODEL_ENV = "ATS_PLANNER_MODEL"
# Reasoning effort of the planner call (D61). gpt-oss defaults to "medium" when nothing is sent,
# which made the planner the longest step of a short request; planning is a small structured
# task, so "low" is the default. "" sends nothing (the model's default).
REASONING_ENV = "ATS_PLANNER_REASONING"
DEFAULT_REASONING = "low"
# The user's request is copied into the final task's instruction (D61): the planner rewrites the
# question in its own words and drops details (answer options, the wanted answer format), and the
# final task never saw the request, so it answered a different question. ATS_ATTACH_REQUEST=0 turns it off.
ATTACH_REQUEST_ENV = "ATS_ATTACH_REQUEST"
REQUEST_HEADER = "\n\nThe user's original request (your answer must answer exactly this; when it gives options, choose one of them):\n"
MAX_INSTRUCTION_CHARS = 2000

# Warnings that make a valid plan worth one repair (D54).
REPAIR_WARNINGS = frozenset({IssueCode.MULTIPLE_SINKS, IssueCode.EXPAND_FANOUT})


class PlanRejected(Exception):
    """The planner's output failed validation."""

    def __init__(self, issues: list[ValidationIssue], raw: Any = None):
        self.issues = issues
        self.raw = raw
        super().__init__("plan rejected:\n" + "\n".join(f"  {i}" for i in issues))


class Planner(ABC):
    last_attempts: int = 0  # propose() calls made by the last generate_plan

    @abstractmethod
    def propose(self, request: str, feedback: list[ValidationIssue] | None = None) -> Any:
        """Return the provider's raw (unvalidated) plan JSON."""

    def generate_plan(self, request: str) -> ExecutionPlan:
        """Propose, validate, and repair at most once (design doc section 6, D8).

        If the first proposal fails validation (or is not parseable), its issues
        are sent back through propose(feedback=...) once; if that also fails, the
        second attempt's issues are raised. `last_attempts` records 1 or 2 so the
        eval can report how often repair was needed.

        A valid first plan with a REPAIR_WARNINGS warning is also sent back once (D54): rule 5
        (one final task) and rule 2 (no per-item placeholders after an expand) are what makes the
        answer usable. It is still a valid plan, so it is returned if the repair does not help.
        """
        feedback: list[ValidationIssue] | None = None
        fallback: ExecutionPlan | None = None
        for attempt in (1, 2):
            self.last_attempts = attempt
            try:
                raw = self.propose(request, feedback=feedback)
            except PlanRejected as exc:  # unparseable output counts as a failed attempt
                if attempt == 2:
                    if fallback is not None:
                        return fallback
                    raise
                feedback = exc.issues
                continue
            result = validate_plan(raw)
            if result.ok:
                repair = [w for w in result.warnings if w.code in REPAIR_WARNINGS]
                if attempt == 1 and repair:
                    fallback, feedback = result.value, repair
                    continue
                return result.value
            if attempt == 2:
                if fallback is not None:
                    return fallback
                raise PlanRejected(result.issues, raw)
            feedback = result.errors
        raise AssertionError("unreachable")


# --- LLM-facing schema -------------------------------------------------------
# Groq strict mode requires every property to be required and every object to
# set additionalProperties: false; optional values are expressed as nullable.
# Length/pattern/count limits are left to validate_plan, which enforces the
# full schema.py constraints afterwards. sim_subplan is fixture-only and is
# never requested from the model.

_NULLABLE_NUMBER = {"type": ["number", "null"]}
_NULLABLE_INTEGER = {"type": ["integer", "null"]}

LLM_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "schema_version": {"type": "integer", "enum": [1]},
        "summary": {"type": "string"},
        "tasks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "type": {"type": "string", "enum": [t.value for t in TaskType]},
                    "title": {"type": "string"},
                    "instruction": {"type": "string"},
                    "depends_on": {"type": "array", "items": {"type": "string"}},
                    "hints": {
                        "type": "object",
                        "properties": {
                            "estimated_seconds": _NULLABLE_NUMBER,
                            "expected_fanout": _NULLABLE_INTEGER,
                        },
                        "required": ["estimated_seconds", "expected_fanout"],
                        "additionalProperties": False,
                    },
                },
                "required": ["id", "type", "title", "instruction", "depends_on", "hints"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["schema_version", "summary", "tasks"],
    "additionalProperties": False,
}


def normalize_llm_output(raw: Any) -> Any:
    """Map strict-mode nulls back to the plan schema's optional fields."""
    if not isinstance(raw, dict) or not isinstance(raw.get("tasks"), list):
        return raw
    for task in raw["tasks"]:
        if not isinstance(task, dict) or not isinstance(task.get("hints"), dict):
            continue
        hints = {k: v for k, v in task["hints"].items() if v is not None}
        if hints:
            task["hints"] = hints
        else:
            del task["hints"]
    return raw


def attach_request(plan: ExecutionPlan, request: str) -> ExecutionPlan:
    """The plan with the user's request appended to the instruction of every final task (one that no
    other task depends on), cut so the instruction stays within MAX_INSTRUCTION_CHARS (D61).
    An assemble sink makes no LLM call (D64), so the request goes to the tasks it joins that combine
    others (those with dependencies of their own), or to its first input if none does."""
    request = request.strip()
    if not request:
        return plan
    used = {d for t in plan.tasks for d in t.depends_on}
    by_id = {t.id: t for t in plan.tasks}
    targets: set[str] = set()
    for t in plan.tasks:
        if t.id in used:
            continue
        if t.type != TaskType.assemble:
            targets.add(t.id)
            continue
        combining = [d for d in t.depends_on if d in by_id and by_id[d].depends_on]
        targets.update(combining or t.depends_on[:1])
    tasks = []
    for t in plan.tasks:
        # before D64:
        # if t.id not in used and request not in t.instruction:
        if t.id in targets and t.type != TaskType.assemble and request not in t.instruction:
            room = MAX_INSTRUCTION_CHARS - len(t.instruction) - len(REQUEST_HEADER)
            if room >= 40:  # too little room left: keep the planner's instruction as it is
                text = request if len(request) <= room else request[: room - 3].rstrip() + "..."
                t = t.model_copy(update={"instruction": t.instruction + REQUEST_HEADER + text})
        tasks.append(t)
    return plan.model_copy(update={"tasks": tasks})


# The planner adds the assemble step itself (D66) instead of relying on the prompt: in E7 the model
# ended only 2 of 4 wide plans with assemble, and the other two answers were 3-4k character summaries
# against the single call's 17-26k. ATS_AUTO_ASSEMBLE=0 turns it off.
AUTO_ASSEMBLE_ENV = "ATS_AUTO_ASSEMBLE"
AUTO_ASSEMBLE_MIN_ITEMS = 3
ITEM_TYPES = (TaskType.research, TaskType.summarize, TaskType.analyze)
COMBINE_TYPES = (TaskType.compare, TaskType.report)
ITEM_REQUEST_HEADER = "The user's request (write only this item's part of it, in full, as the request asks):\n"
COMBINED_ONLY = ("\n\nWrite only the combined part the request asks for (the comparison, ranking or summary). "
                 "Do not repeat the per-item sections: they are added to the answer in full after yours.")


def add_assemble(plan: ExecutionPlan, request: str = "") -> ExecutionPlan:
    """A wide plan ending in one compare/report task over at least AUTO_ASSEMBLE_MIN_ITEMS per-item tasks
    gets an assemble task after it (D64), so the answer carries every item's full section without a model
    rewriting them; the final task is told to write only the combined part. Each item task's instruction
    starts with the user's request, so its section covers what the request asks for even when the
    planner's instruction is brief; the request is the same for every item, so it is part of the prefix
    the provider can cache (D65). Plans with an expand or assemble task, or any other shape, are
    returned unchanged, as is any result that would not validate."""
    by_id = {t.id: t for t in plan.tasks}
    if any(t.type in (TaskType.expand, TaskType.assemble) for t in plan.tasks):
        return plan
    used = {d for t in plan.tasks for d in t.depends_on}
    sinks = [t for t in plan.tasks if t.id not in used]
    if len(sinks) != 1 or sinks[0].type not in COMBINE_TYPES or len(plan.tasks) >= MAX_TASKS:
        return plan
    sink = sinks[0]
    items = [d for d in sink.depends_on if d in by_id and by_id[d].type in ITEM_TYPES]
    if len(items) < AUTO_ASSEMBLE_MIN_ITEMS:
        return plan
    instruction = sink.instruction
    if len(instruction) + len(COMBINED_ONLY) <= MAX_INSTRUCTION_CHARS:
        instruction += COMBINED_ONLY
    next_id = 1 + max(int(t.id[1:]) for t in plan.tasks if t.id[1:].isdigit())
    request = request.strip()

    def with_request(t):
        text = ITEM_REQUEST_HEADER + request + "\n\n" + t.instruction
        if not request or request in t.instruction or len(text) > MAX_INSTRUCTION_CHARS:
            return t
        return t.model_copy(update={"instruction": text})

    tasks = [t.model_copy(update={"instruction": instruction}) if t.id == sink.id
             else with_request(t) if t.id in items else t for t in plan.tasks]
    raw = plan.model_dump(mode="json")
    raw["tasks"] = [t.model_dump(mode="json") for t in tasks] + [{
        "id": f"t{next_id}", "type": "assemble", "title": "Full answer",
        "instruction": "Join the combined part and every item's full section.",
        "depends_on": [sink.id, *items], "hints": {"estimated_seconds": 0}}]
    result = validate_plan(raw)
    return result.value if result.ok else plan


def load_system_prompt() -> str:
    return resources.files("planner").joinpath("prompts/planner_system.md").read_text()


def _feedback_message(issues: list[ValidationIssue]) -> str:
    lines = "\n".join(f"- {i}" for i in issues)
    return (
        "Your previous plan was rejected by the validator with these issues:\n"
        f"{lines}\n"
        "Return a corrected plan for the same request that fixes every issue."
    )


class GroqPlanner(Planner):
    def __init__(self, model: str | None = None, client: Any = None, max_retries: int = 2):
        # before D60:
        # self.model = model or os.environ.get(MODEL_ENV, DEFAULT_MODEL)
        self.model = provider.model_name(model or os.environ.get(MODEL_ENV, DEFAULT_MODEL))
        if client is None:
            # before D60:
            # from groq import Groq  # imported lazily so tests never need the SDK configured
            #
            # client = Groq(max_retries=max_retries)  # reads GROQ_API_KEY
            client = provider.sync_client(max_retries)  # Groq or Hugging Face, by ATS_LLM_PROVIDER (D60)
        self.client = client
        self.system_prompt = load_system_prompt()
        reasoning = os.environ.get(REASONING_ENV, DEFAULT_REASONING)
        self.reasoning_effort = reasoning if reasoning and "gpt-oss" in self.model else None  # D61
        self.last_usage: Any = None
        self._last_content: str | None = None  # previous answer, replayed on a repair call

    def generate_plan(self, request: str) -> ExecutionPlan:
        plan = super().generate_plan(request)
        if os.environ.get(AUTO_ASSEMBLE_ENV, "1") != "0":
            plan = add_assemble(plan, request)  # D66, before D61 so the request goes to the combining task
        if os.environ.get(ATTACH_REQUEST_ENV, "1") == "0":
            return plan
        return attach_request(plan, request)  # D61

    def propose(self, request: str, feedback: list[ValidationIssue] | None = None) -> Any:
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": request},
        ]
        if feedback:
            if self._last_content:
                messages.append({"role": "assistant", "content": self._last_content})
            messages.append({"role": "user", "content": _feedback_message(feedback)})

        # before D61:
        # response = self.client.chat.completions.create(
        #     model=self.model,
        #     messages=messages,
        #     response_format={
        #         "type": "json_schema",
        #         "json_schema": {"name": "execution_plan", "strict": True, "schema": LLM_PLAN_SCHEMA},
        #     },
        # )
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "execution_plan", "strict": True, "schema": LLM_PLAN_SCHEMA},
            },
        }
        if self.reasoning_effort:
            kwargs["reasoning_effort"] = self.reasoning_effort  # D61
        response = self.client.chat.completions.create(**kwargs)
        self.last_usage = getattr(response, "usage", None)
        choice = response.choices[0]
        content = choice.message.content or ""
        self._last_content = content
        try:
            raw = json.loads(content)
        except json.JSONDecodeError as exc:
            raise PlanRejected(
                [
                    ValidationIssue(
                        code=IssueCode.SCHEMA,
                        severity="error",
                        message=f"model output is not valid JSON ({exc.msg}; finish_reason={choice.finish_reason})",
                    )
                ],
                content,
            ) from exc
        return normalize_llm_output(raw)


__all__ = [
    "DEFAULT_MODEL",
    "GroqPlanner",
    "LLM_PLAN_SCHEMA",
    "PlanRejected",
    "Planner",
    "add_assemble",
    "attach_request",
]
