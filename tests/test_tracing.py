"""Optional LangSmith tracing of local AI calls."""

import json

from algoverse.ai import budget, tracing
from algoverse.ai.budget import AiLog
from algoverse.ai.tracing import LangSmithSettings, Tracer, build_run

FIELDS = {
    "ts": "2026-11-12T10:00:00+05:30",
    "day": "2026-11-12",
    "kind": "strategy_tuning",
    "key": "orb_call",
    "model": "gpt-oss:20b",
    "status": "ok",
    "prompt": 'You tune strategies.\n\n{"suggest": 4}',
    "output": '{"suggestions": []}',
    "payload": None,
    "input_tokens": 1200,
    "output_tokens": 150,
    "latency_ms": 2500,
}


class FakeClient:
    def __init__(self, fail=False):
        self.runs, self.fail = [], fail

    def create_run(self, **run):
        if self.fail:
            raise ConnectionError("offline")
        self.runs.append(run)

    def flush(self):
        pass


def on(client):
    return Tracer(LangSmithSettings(tracing=True, api_key="test-key", project="tests"), client)


def test_off_unless_switched_on_with_a_key():
    assert not LangSmithSettings(tracing=False, api_key="k").enabled
    assert not LangSmithSettings(tracing=True, api_key="").enabled
    assert LangSmithSettings(tracing=True, api_key="k").enabled
    # The test run itself never traces, whatever .env holds.
    assert not LangSmithSettings().enabled
    client = FakeClient()
    Tracer(LangSmithSettings(tracing=False, api_key="k"), client).submit(FIELDS)
    assert client.runs == []


def test_a_run_carries_prompt_reply_tokens_and_timing():
    run = build_run(FIELDS, "algoverse")
    assert run["name"] == "strategy_tuning" and run["run_type"] == "llm"
    assert run["inputs"] == {"prompt": FIELDS["prompt"]}
    assert run["outputs"]["output"] == FIELDS["output"]
    assert run["outputs"]["usage_metadata"]["total_tokens"] == 1350
    assert (run["end_time"] - run["start_time"]).total_seconds() == 2.5
    assert run["error"] is None
    meta = run["extra"]["metadata"]
    assert meta["ls_model_name"] == "gpt-oss:20b" and meta["ls_provider"] == "ollama"
    assert meta["key"] == "orb_call"


def test_chat_messages_and_failures_are_shown_as_such():
    messages = [{"role": "system", "content": "Explain."}, {"role": "user", "content": "Why?"}]
    run = build_run(
        {
            **FIELDS,
            "kind": "market_chat",
            "prompt": json.dumps(messages),
            "status": "unavailable",
            "payload": json.dumps({"verdict": "hold"}),
        },
        "algoverse",
    )
    assert run["inputs"] == {"messages": messages}
    assert run["error"] == "status: unavailable" and "status:unavailable" in run["tags"]
    assert run["outputs"]["parsed"] == {"verdict": "hold"}


def test_runs_are_sent_in_the_background():
    client = FakeClient()
    tracer = on(client)
    tracer.submit(FIELDS)
    tracer.submit({**FIELDS, "kind": "strategy_design"})
    tracer.flush()
    assert [run["name"] for run in client.runs] == ["strategy_tuning", "strategy_design"]
    assert all(run["project_name"] == "tests" for run in client.runs)


def test_an_upload_failure_never_reaches_the_app():
    tracer = on(FakeClient(fail=True))
    tracer.submit(FIELDS)
    tracer.flush()


def test_every_logged_call_is_traced(tmp_path, monkeypatch):
    client = FakeClient()
    tracer = on(client)
    monkeypatch.setattr(budget, "get_tracer", lambda: tracer)
    AiLog(tmp_path / "ai.sqlite").record(**FIELDS)
    tracer.flush()
    assert len(client.runs) == 1


def test_the_shared_tracer_is_off_in_tests():
    tracing._TRACER = None
    assert tracing.get_tracer().enabled is False
