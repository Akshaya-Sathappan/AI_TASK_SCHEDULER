"""A request's timeline from atsd's event log, for the monitor's Gantt chart (D63).

atsd appends one JSON line per scheduling event to <runs>/<run_id>/events.jsonl (ATSD_RUNS_DIR;
docker-compose mounts ./runs into atsd, and read-only into the API). This module reads those
files incrementally (each file from where the last read stopped) and turns one request's events
into attempt bars:

    dispatched / speculated  -> the attempt is sent to a worker (a speculated one is a backup, D56)
    started                  -> the worker picked it up
    completed                -> the attempt's result was committed
    retry_scheduled          -> it failed and the task will be retried (a 429 or another retryable error)
    failed / backup_failed   -> it failed for good (or the backup failed)
    attempt_lost             -> its deadline passed
    late_result / duplicate_completion -> it finished after another attempt had already won
    join_completed           -> a join task (D53): no attempt, one point in time

An attempt without an end event is still running. capability_paused lines (after a 429) are
returned as pauses.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from pathlib import Path
from typing import Any

RUNS_ENV = "ATS_RUNS_DIR"
END_EVENTS = {
    "completed": "completed",
    "retry_scheduled": "retried",
    "failed": "failed",
    "backup_failed": "failed",
    "attempt_lost": "lost",
    "late_result": "late",
    "duplicate_completion": "late",
}


def runs_dir() -> Path:
    return Path(os.environ.get(RUNS_ENV, "runs"))


class EventLog:
    """atsd's event lines grouped by request, read incrementally from every events.jsonl under `root`."""

    def __init__(self, root: Path):
        self.root = root
        self._offsets: dict[Path, int] = {}
        self._by_request: dict[str, list[dict[str, Any]]] = defaultdict(list)

    def refresh(self) -> None:
        if not self.root.is_dir():
            return
        for path in sorted(self.root.glob("*/events.jsonl")):
            try:
                size = path.stat().st_size
            except OSError:
                continue
            start = self._offsets.get(path, 0)
            if size < start:  # the file was replaced: read it again
                start = 0
            if size == start:
                continue
            with path.open("rb") as f:
                f.seek(start)
                data = f.read(size - start)
            end = data.rfind(b"\n") + 1  # a line still being written is read next time
            for line in data[:end].splitlines():
                try:
                    event = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if isinstance(event, dict) and event.get("request"):
                    self._by_request[event["request"]].append(event)
            self._offsets[path] = start + end

    def events(self, request_id: str) -> list[dict[str, Any]]:
        self.refresh()
        return sorted(self._by_request.get(request_id, []), key=lambda e: e.get("ts_ms", 0))


def build_timeline(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Attempt bars, join points and 429 pauses for one request, times in ms since its first event."""
    if not events:
        return {"available": False, "attempts": [], "joins": [], "pauses": [], "start_ms": None, "end_ms": None}
    start = next((e["ts_ms"] for e in events if e.get("event") in ("request_accepted", "request_resumed")),
                 events[0]["ts_ms"])
    attempts: dict[tuple[str, int], dict[str, Any]] = {}
    joins, pauses = [], []
    finished = None

    def attempt(e: dict[str, Any]) -> dict[str, Any]:
        key = (e["task"], int(e.get("attempt") or 0))
        if key not in attempts:
            attempts[key] = {"task": key[0], "attempt": key[1], "backup": False, "dispatched": None,
                             "started": None, "end": None, "outcome": "running", "worker": None, "detail": None}
        return attempts[key]

    for e in events:
        kind, ts = e.get("event"), e.get("ts_ms", start) - start
        if kind in ("dispatched", "speculated") and e.get("task"):
            a = attempt(e)
            a["dispatched"] = ts if a["dispatched"] is None else a["dispatched"]
            a["backup"] = a["backup"] or kind == "speculated"
        elif kind == "started" and e.get("task"):
            a = attempt(e)
            a["started"] = ts if a["started"] is None else a["started"]
            a["worker"] = e.get("worker")
        elif kind in END_EVENTS and e.get("task"):
            a = attempt(e)
            if a["end"] is None:
                a["end"], a["outcome"], a["detail"] = ts, END_EVENTS[kind], e.get("detail")
        elif kind == "join_completed" and e.get("task"):
            joins.append({"task": e["task"], "at": ts})
        elif kind == "capability_paused":
            pauses.append({"at": ts, "detail": e.get("detail")})
        if kind in ("completed", "join_completed", "failed", "cancelled"):
            finished = ts if finished is None else max(finished, ts)

    bars = sorted(attempts.values(), key=lambda a: (a["dispatched"] if a["dispatched"] is not None else 1e18))
    last = max([e.get("ts_ms", start) - start for e in events] + [0])
    return {"available": True, "start_ms": start, "end_ms": last, "finished_ms": finished,
            "attempts": bars, "joins": joins, "pauses": pauses}
