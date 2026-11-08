"""The assemble task's result, in Python (D64). atsd computes it in C++ (Service::assemble) when the
plan runs on the stack; E7's sequential mode and the tests use this copy, which must stay identical.

Inputs in depends_on order, each a committed result:
  {"content", "key_points", "final_answer"}  -> its content under "## <title>" (unless the content
                                                already starts with "#"); key points concatenated
                                                (at most 30); the first non-empty final_answer
  {"artifact": ...}                          -> a note that the result is stored as an artifact
  anything else                              -> its JSON
"""

from __future__ import annotations

import json
from typing import Any

MAX_KEY_POINTS = 30


def assemble_output(inputs: list[tuple[str, str, Any]]) -> dict[str, Any]:
    """inputs: (task id, title, committed result) per input, in depends_on order."""
    parts: list[str] = []
    key_points: list[str] = []
    final_answer: str | None = None
    for task_id, title, value in inputs:
        if isinstance(value, dict) and "artifact" in value:
            text = f"_(The result of {task_id} is stored as an artifact: {json.dumps(value['artifact'])})_"
        elif isinstance(value, dict) and isinstance(value.get("content"), str):
            text = value["content"]
            for point in value.get("key_points") or []:
                if isinstance(point, str) and len(key_points) < MAX_KEY_POINTS:
                    key_points.append(point)
            fa = value.get("final_answer")
            if final_answer is None and isinstance(fa, str) and fa:
                final_answer = fa
        elif isinstance(value, str):
            text = value
        else:
            text = json.dumps(value, indent=2)
        parts.append(text if text.startswith("#") else f"## {title}\n\n{text}")
    return {"content": "\n\n".join(parts), "key_points": key_points, "final_answer": final_answer}


__all__ = ["assemble_output"]
