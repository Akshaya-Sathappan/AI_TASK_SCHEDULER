"""D63: atsd's event log as attempt bars for the monitor's Gantt chart."""

import json

from api.timeline import EventLog, build_timeline


def ev(ts, event, task=None, attempt=None, **kw):
    e = {"ts_ms": ts, "event": event, "request": "req_a"}
    if task:
        e["task"] = task
    if attempt:
        e["attempt"] = attempt
    return {**e, **kw}


EVENTS = [
    ev(1000, "request_accepted"),
    ev(1010, "dispatched", "t1", 1), ev(1010, "dispatched", "t2", 1),
    ev(1050, "started", "t1", 1, worker="w1"), ev(1060, "started", "t2", 1, worker="w2"),
    ev(1500, "retry_scheduled", "t2", 1, detail="429 (in 2000 ms)"), ev(1500, "capability_paused", "t2", 1),
    ev(2000, "completed", "t1", 1, worker="w1"),
    ev(3500, "dispatched", "t2", 2), ev(3520, "started", "t2", 2), ev(4000, "completed", "t2", 2),
    ev(4010, "dispatched", "t3", 1),
]


def test_attempt_bars_outcomes_and_pauses():
    tl = build_timeline(EVENTS)
    bars = {(a["task"], a["attempt"]): a for a in tl["attempts"]}
    assert tl["available"] and tl["start_ms"] == 1000
    assert bars[("t1", 1)] | {} == {**bars[("t1", 1)], "dispatched": 10, "started": 50, "end": 1000,
                                    "outcome": "completed", "worker": "w1"}
    assert bars[("t2", 1)]["outcome"] == "retried" and bars[("t2", 1)]["detail"] == "429 (in 2000 ms)"
    assert (bars[("t2", 2)]["started"], bars[("t2", 2)]["end"]) == (2520, 3000)
    assert bars[("t3", 1)]["outcome"] == "running" and bars[("t3", 1)]["end"] is None
    assert tl["pauses"] == [{"at": 500, "detail": None}]
    assert tl["finished_ms"] == 3000 and tl["end_ms"] == 3010


def test_no_events_is_not_available():
    assert build_timeline([])["available"] is False


def test_event_log_reads_new_lines_only_and_skips_a_partial_line(tmp_path):
    run = tmp_path / "20261107-000000"
    run.mkdir()
    f = run / "events.jsonl"
    f.write_text("\n".join(json.dumps(e) for e in EVENTS[:3]) + "\n" + '{"ts_ms": 1, "event": "half')
    log = EventLog(tmp_path)
    assert len(log.events("req_a")) == 3
    with f.open("a") as out:
        out.write('-line", "request": "req_a"}\n' + json.dumps(EVENTS[3]) + "\n")
    names = [e["event"] for e in log.events("req_a")]
    assert len(names) == 5 and "half-line" in names and names[-1] == "started"  # sorted by ts_ms
    assert log.events("req_other") == []
    assert EventLog(tmp_path / "missing").events("req_a") == []


def test_a_task_completed_inside_atsd_is_a_point():  # D64: assemble, never dispatched
    tl = build_timeline(EVENTS + [ev(4100, "assembled", "t9", None), ev(4100, "completed", "t9", None)])
    assert {"task": "t9", "at": 3100} in tl["joins"]
    assert all(a["task"] != "t9" for a in tl["attempts"])
