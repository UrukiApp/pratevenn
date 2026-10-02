"""Local archives and session review use temporary data and fake inference."""

import json
import threading
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from pratevenn.app import create_app
from pratevenn.history import MAX_CHAT_BYTES, ChatStore, validate_messages
from pratevenn.models import (
    INLINE_FEEDBACK_PROMPT,
    Models,
    REVIEW_PROMPT,
    inline_findings,
    model_directory,
    review_findings,
)
from test_conversation import FakeModels


@pytest.mark.parametrize("kind, parent", [("MODEL", ".cache"), ("DATA", ".local/share")])
def test_renamed_storage_keeps_existing_data(kind, parent, tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    for name in (f"PRATEVENN_{kind}_DIR", f"KONVERS_{kind}_DIR"):
        monkeypatch.delenv(name, raising=False)

    def selected():
        return model_directory() if kind == "MODEL" else ChatStore().path.parent

    directory = tmp_path / parent / "pratevenn"
    legacy = directory.with_name("konvers")
    assert selected() == directory
    legacy.mkdir(parents=True)
    assert selected() == legacy
    directory.mkdir()
    assert selected() == directory
    monkeypatch.setenv(f"KONVERS_{kind}_DIR", str(tmp_path / "old-custom"))
    assert selected() == tmp_path / "old-custom"
    monkeypatch.setenv(f"PRATEVENN_{kind}_DIR", str(tmp_path / "new-custom"))
    assert selected() == tmp_path / "new-custom"


def messages(turns=1, source="text"):
    return [
        item
        for _ in range(turns)
        for item in (
            {"role": "user", "content": "I går jeg gikk på jobb.", "source": source},
            {"role": "assistant", "content": "Hvordan var dagen?", "source": "text"},
        )
    ]


def finding(turn=1, **changes):
    return {
        "turn": turn, "original": "I går jeg gikk", "correction": "I går gikk jeg",
        "category": "word_order", "explanation": "Verbet skal stå på andre plass.",
        **changes,
    }


def test_optional_archive_crud_and_http_boundary(tmp_path):
    store = ChatStore(tmp_path / "data/chats.sqlite3")
    identifier = str(uuid4())
    route = f"/api/chats/{identifier}"
    headers = {"X-Pratevenn-Request": "1", "Origin": "http://127.0.0.1"}
    with TestClient(create_app(FakeModels(), store), base_url="http://127.0.0.1") as client:
        assert client.get("/api/chats", headers=headers).json() == []
        assert not store.path.exists()
        assert client.put(route, json=messages()).status_code == 403
        assert client.get("/api/chats").status_code == 403
        assert client.put(route, json=messages(),
                          headers={**headers, "Origin": "http://evil.example"}).status_code == 403
        assert not store.path.exists()
        for invalid in ([], messages()[:1], messages()[::-1],
                        [{"role": "user", "content": "x" * 1001}, messages()[1]]):
            assert client.put(route, json=invalid, headers=headers).status_code == 400
        assert client.put(route, content=b"{}",
                          headers={**headers, "Content-Type": "text/plain"}).status_code == 415
        assert client.put(route, content=b"x" * (MAX_CHAT_BYTES + 1), headers={**headers,
                                                                               "Content-Type": "application/json"}).status_code == 413
        assert client.put(route, json=messages(source="audio"), headers=headers).json() == {
            "saved": True}
        assert store.path.exists()
        assert client.get(route, headers=headers).json()["messages"] == messages(source="audio")
        listed = client.get("/api/chats", headers=headers).json()
        assert listed[0]["id"] == identifier and listed[0]["title"] == messages()[0]["content"]
        assert "messages" not in listed[0]
        assert client.put(route, json=messages(2), headers=headers).status_code == 200
        assert len(ChatStore(store.path).get(identifier)["messages"]) == 4
        assert client.delete(route, headers=headers).status_code == 200
        assert client.get(route, headers=headers).status_code == 404
        assert client.put(route, json=messages(), headers=headers).status_code == 200
        assert client.delete("/api/chats", headers=headers).status_code == 200
        assert store.list_chats() == []
        nonexistent = ChatStore(tmp_path / "absent" / "store.sqlite3")
        assert nonexistent.get(identifier) is None
        nonexistent.delete(identifier)
        nonexistent.delete()


def test_chat_validation_bounds_and_sources():
    for invalid in (None, {}, messages(201), messages(source=[]), messages(source="cloud")):
        with pytest.raises(ValueError):
            validate_messages(invalid)
    assert len(validate_messages(messages(200))) == 400


@pytest.mark.parametrize("character", ["ø", "😀", "\x00"])
def test_full_unicode_chat_save_restore_and_review(tmp_path, character):
    transcript = [
                     {"role": "user", "content": character * 1000, "source": "text"},
                     {"role": "assistant", "content": character * 4000, "source": "text"},
                 ] * 200
    models = FakeModels()

    def review(messages, cancelled, emit, selection, context_tokens=None):
        assert context_tokens == 8192
        assert messages == transcript
        emit({"type": "done", "review": True})

    models.review_turn = review
    headers = {"X-Pratevenn-Request": "1", "Origin": "http://127.0.0.1"}
    route = f"/api/chats/{uuid4()}"
    with TestClient(create_app(models, ChatStore(tmp_path / "chats.sqlite3")),
                    base_url="http://127.0.0.1") as client:
        # Escaped non-BMP characters occupy twelve bytes per character in JSON.
        body = json.dumps(transcript).encode()
        assert len(body) > 1_400_000
        assert client.put(route, content=body, headers={**headers,
                                                        "Content-Type": "application/json"}).status_code == 200
        assert client.get(route, headers=headers).json()["messages"] == transcript
        with client.websocket_connect("ws://127.0.0.1/ws", headers=headers) as ws:
            ws.send_text(
                json.dumps({"type": "review", "messages": transcript, "history": transcript}))
            assert ws.receive_json() == {"type": "done", "review": True}
        with client.websocket_connect("ws://127.0.0.1/ws", headers=headers) as ws:
            ws.send_text(json.dumps({"type": "text", "text": "Hei", "history": transcript}))
            assert [ws.receive_json()["type"] for _ in range(3)] == ["transcript", "text", "done"]
        assert models.histories[-1] == [{"role": m["role"], "content": m["content"]} for m in
                                        transcript]


def test_resume_and_review_are_connection_local(tmp_path):
    models = FakeModels()
    reviewed = []
    review_finished = threading.Event()
    next_request = threading.Event()
    original_selection = models.selection

    def selection(requested):
        chosen = original_selection(requested)
        if review_finished.is_set():
            next_request.set()
        return chosen

    models.selection = selection

    def review(messages, cancelled, emit, selection, context_tokens=None):
        assert context_tokens == 8192
        reviewed.append((messages, selection))
        if messages[0]["content"] == "wait":
            models.entered.set()
            cancelled.wait(5)
            if cancelled.is_set():
                models.cancelled.set()
            return
        emit({"type": "review", "findings": [], "turns": len(messages) // 2})
        review_finished.set()
        emit({"type": "done", "review": True})
        # Keep cleanup pending until the next request reaches the connection handler.
        assert next_request.wait(2)

    models.review_turn = review
    with TestClient(create_app(models, ChatStore(tmp_path / "chats.sqlite3")),
                    base_url="http://127.0.0.1") as client:
        headers = {"Origin": "http://127.0.0.1"}
        with client.websocket_connect("ws://127.0.0.1/ws", headers=headers) as ws:
            ws.send_json({"type": "review", "messages": messages()[:1]})
            assert ws.receive_json()["type"] == "error"
            assert not reviewed
            ws.send_json({"type": "review", "messages": messages(), "history": messages(2)})
            assert ws.receive_json()["type"] == "review"
            assert ws.receive_json() == {"type": "done", "review": True}
            ws.send_json({"type": "text", "text": "Hei igjen"})
            assert [ws.receive_json()["type"] for _ in range(3)] == ["transcript", "text", "done"]
            assert models.histories[0] == [{"role": m["role"], "content": m["content"]} for m in
                                           messages(2)]
            ws.send_json({"type": "text", "text": "Hei", "history": messages()})
            assert "Reopen" in ws.receive_json()["message"]
            ws.send_json(
                {"type": "review", "messages": messages(), "models": {"stt": "stt/second"}})
            assert "Stop" in ws.receive_json()["message"]
        with client.websocket_connect("ws://127.0.0.1/ws", headers=headers) as ws:
            ws.send_json({"type": "text", "text": "Hei"})
            for _ in range(3):
                ws.receive_json()
        assert models.histories[-1] == []
        models.entered.clear()
        with client.websocket_connect("ws://127.0.0.1/ws", headers=headers) as ws:
            pending = messages()
            pending[0]["content"] = "wait"
            ws.send_json({"type": "review", "messages": pending})
            assert models.entered.wait(2)
        assert models.cancelled.wait(2)
    assert reviewed[0][1] == models.defaults


def test_review_batches_evidence_cancellation_and_lock_release():
    model = Models.__new__(Models)
    model.lock = threading.Lock()
    model.loaded = {"llm": "local"}
    prompts = []
    results = [
        [finding(), finding(), finding(2, original="Invented phrase")],
        [finding(4)],
    ]

    def prompt(history, text, system_prompt):
        assert history == [] and system_prompt == REVIEW_PROMPT
        prompts.append(json.loads(text))
        return [1] * 20, SimpleNamespace(stop=[], stopping_criteria=None)

    def completion(*args, **kwargs):
        assert kwargs["stream"] and kwargs["temperature"] == 0
        return iter([{"choices": [{"text": json.dumps(results.pop(0))}]}])

    model.prompt = prompt
    model.llm = SimpleNamespace(create_completion=completion)
    transcript = messages(4, source="audio")
    events = []
    model.review_turn(transcript, threading.Event(), events.append, model.loaded)
    assert transcript == messages(4, source="audio")
    assert events[-2] == {"type": "review", "findings": [finding(), finding(4)], "turns": 4}
    assert events[-1] == {"type": "done", "review": True}
    assert [len(batch) for batch in prompts] == [3, 1]
    assert prompts[0][0]["source"] == "audio"
    assert not model.lock.locked()

    events.clear()
    with model.lock:
        model.review_turn(transcript, threading.Event(), events.append, model.loaded)
    assert events[0]["type"] == "error" and "busy" in events[0]["message"]
    cancelled = threading.Event()
    cancelled.set()
    events.clear()
    model.review_turn(transcript, cancelled, events.append, model.loaded)
    assert not events and not model.lock.locked()

    def cancel_completion(*args, **kwargs):
        yield {"choices": [{"text": "["}]}
        cancelled.set()
        yield {"choices": [{"text": "]"}]}

    cancelled.clear()
    model.llm.create_completion = cancel_completion
    model.review_turn(transcript, cancelled, events.append, model.loaded)
    assert all(event["type"] == "status" for event in events)

    def complete_then_cancel(*args, **kwargs):
        yield {"choices": [{"text": "[]"}]}
        cancelled.set()

    cancelled.clear()
    model.llm.create_completion = complete_then_cancel
    events.clear()
    model.review_turn(transcript, cancelled, events.append, model.loaded)
    assert not any(event.get("type") == "review" for event in events)
    assert not model.lock.locked()

    loaded_selections = []
    model.load = lambda selection, **kwargs: loaded_selections.append(selection)
    model.prompt = lambda history, text, prompt: ([1] * 10,
                                                  SimpleNamespace(stop=[], stopping_criteria=None))
    model.llm.create_completion = lambda *a, **kw: iter([{"choices": [{"text": "[]"}]}])
    events.clear()
    model.review_turn(transcript[:2], threading.Event(), events.append, {"llm": "custom"})
    assert loaded_selections == [{"llm": "custom"}]

    model.prompt = lambda *a, **kw: ([1] * 5000, SimpleNamespace(stop=[], stopping_criteria=None))
    with pytest.raises(ValueError, match="too long to review"):
        model.review_turn(transcript[:4], threading.Event(), events.append, model.loaded)
    assert not model.lock.locked()

    model.prompt = lambda *a, **kw: ([1] * 10, SimpleNamespace(stop=[], stopping_criteria=None))
    model.llm.create_completion = lambda *a, **kw: iter([{"choices": [{"text": "invalid JSON"}]}])
    with pytest.raises(ValueError, match="could not produce"):
        model.review_turn(transcript, threading.Event(), events.append, model.loaded)
    assert not model.lock.locked()
    assert review_findings(
        [finding(True), finding(category="invented"), finding(correction="I går jeg gikk")],
        messages(), 1, 1) == []
    for invalid_review in ("not a list", [finding()] * 4):
        with pytest.raises(ValueError, match="invalid review"):
            review_findings(invalid_review, messages(), 1, 1)
    for invalid_item in (
            ["not a dict"],
            [{"turn": 1, "original": 123, "correction": "b", "category": "other",
              "explanation": "e"}],
            [{"turn": 1, "original": "", "correction": "b", "category": "other",
              "explanation": "e"}],
            [{"turn": 1, "original": "a" * 1001, "correction": "b", "category": "other",
              "explanation": "e"}],
    ):
        with pytest.raises(ValueError, match="invalid suggestion"):
            review_findings(invalid_item, messages(), 1, 1)


def test_inline_findings_and_eval_turn():
    valid = {
        "original": "I går jeg gikk",
        "correction": "I går gikk jeg",
        "category": "word_order",
        "explanation": "Verbet må stå på andreplass.",
    }
    text = "I går jeg gikk på kino."
    assert inline_findings([valid], text) == [valid]
    assert inline_findings(None, text) == []
    assert inline_findings([valid, valid, valid], text) == []
    assert inline_findings(["not a dict"], text) == []
    assert inline_findings([{**valid, "category": "invalid"}], text) == []
    assert inline_findings([{**valid, "original": "ikke i teksten"}], text) == []
    assert inline_findings([{**valid, "correction": valid["original"]}], text) == []
    assert inline_findings([valid, valid], text) == [valid]
    for invalid_detail in (
            {"original": 123, "correction": "a", "category": "word_order", "explanation": "e"},
            {"original": "", "correction": "a", "category": "word_order", "explanation": "e"},
            {"original": "a" * 501, "correction": "a", "category": "word_order",
             "explanation": "e"},
    ):
        assert inline_findings([invalid_detail], text) == []

    model = Models.__new__(Models)
    model.eval_prompt = lambda t: ([1] * 5, SimpleNamespace(stop=[], stopping_criteria=None))
    model.llm = SimpleNamespace(
        create_completion=lambda *a, **kw: iter([{"choices": [{"text": json.dumps([valid])}]}])
    )
    cancelled = threading.Event()
    assert model.eval_turn(text, cancelled) == [valid]

    cancelled.set()
    assert model.eval_turn(text, cancelled) == []

    def cancel_eval_chunks(*args, **kwargs):
        yield {"choices": [{"text": "["}]}
        cancelled.set()
        yield {"choices": [{"text": "]"}]}

    cancelled.clear()
    model.llm.create_completion = cancel_eval_chunks
    assert model.eval_turn(text, cancelled) == []

    def cancel_after_eval_chunks(*args, **kwargs):
        yield {"choices": [{"text": "[]"}]}
        cancelled.set()

    cancelled.clear()
    model.llm.create_completion = cancel_after_eval_chunks
    assert model.eval_turn(text, cancelled) == []

    cancelled.clear()
    model.llm = SimpleNamespace(
        create_completion=lambda *a, **kw: iter([{"choices": [{"text": "not json"}]}])
    )
    assert model.eval_turn(text, cancelled) == []
    assert "Du vurderer én ytring" in INLINE_FEEDBACK_PROMPT


def test_eval_prompt():
    model = Models.__new__(Models)
    model.formatter = lambda messages, enable_thinking: SimpleNamespace(
        prompt="prompt text", stop=["</s>"], stopping_criteria=None, added_special=False
    )
    model.llm = SimpleNamespace(
        tokenize=lambda b, add_bos=True, special=True: [10, 20, 30]
    )
    tokens, formatted = model.eval_prompt("Hei på deg")
    assert tokens == [10, 20, 30]
    assert formatted.prompt == "prompt text"
