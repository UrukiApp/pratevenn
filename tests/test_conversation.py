"""Protocol and cancellation checks; no models or network downloads required."""

import base64
import io
import json
import struct
import threading
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from pratevenn.app import MAX_MESSAGE_BYTES, create_app, parse_turn
from pratevenn.models import (
    CONTEXT_TOKENS,
    MAX_SYSTEM_PROMPT_CHARS,
    PROMPT_TOKENS,
    SYSTEM_PROMPT,
    Models,
    chat_messages,
    discover_models,
    download_models,
    load_cuda_runtime,
    validate_audio,
)


class FakeModels(Models):
    def __init__(self):
        self.histories = []
        self.speeds = []
        self.entered = threading.Event()
        self.cancelled = threading.Event()
        self.selections = []
        self.prompts = []
        self.context_tokens_list = []
        self.available = {
            "stt": {"stt/first": Path("first"), "stt/second": Path("second")},
            "llm": {"llm/first.gguf": Path("first.gguf")},
            "tts": {"tts/first.onnx": Path("first.onnx")},
        }
        self.defaults = {kind: next(iter(choices)) for kind, choices in self.available.items()}

    def run_turn(
        self,
        text,
        audio,
        history,
        cancelled,
        emit,
        selection=None,
        system_prompt=SYSTEM_PROMPT,
        feedback=False,
        speed=0.85,
        **kwargs,
    ):
        self.entered.set()
        self.selections.append(selection)
        self.prompts.append(system_prompt)
        self.speeds.append(speed)
        self.context_tokens_list.append(kwargs.get("context_tokens"))
        if text == "wait":
            cancelled.wait(5)
            if cancelled.is_set():
                self.cancelled.set()
            return
        if text == "fail":
            raise RuntimeError("test model failure")
        self.histories.append(list(history))
        emit({"type": "transcript", "text": text or "Hei"})
        emit({"type": "text", "text": "Hei! Hvordan har du det?"})
        history.append({"role": "user", "content": text or "Hei"})
        emit({"type": "done"})


def recording(rate=16000, seconds=0.2):
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(bytes(int(rate * seconds) * 2))
    return output.getvalue()


def test_audio_trust_boundary():
    valid = recording()
    validate_audio(valid)
    _, decoded, _ = parse_turn({"type": "audio", "data": base64.b64encode(valid).decode()})
    assert decoded == valid
    for invalid in (b"", valid[:-4], recording(48000), recording(seconds=31)):
        with pytest.raises(ValueError):
            validate_audio(invalid)
    for message in (
        {"type": "audio", "data": "not base64"},
        {"type": "text", "text": " "},
        {"type": "text", "text": "x" * 1001},
    ):
        with pytest.raises(ValueError):
            parse_turn(message)


def test_whisper_decodes_validated_wav_with_locked_dependencies():
    from faster_whisper.audio import decode_audio

    output = io.BytesIO()
    samples = [-32768, -16384, 0, 16384, 32767] * 320
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    data = output.getvalue()
    validate_audio(data)
    decoded = decode_audio(io.BytesIO(data), sampling_rate=16000)
    assert decoded.dtype.name == "float32"
    assert decoded.tolist() == [sample / 32768 for sample in samples]


def test_websocket_history_errors_and_cancellation():
    models = FakeModels()
    with TestClient(create_app(models), base_url="http://127.0.0.1") as client:
        response = client.get("/")
        assert response.status_code == 200
        assert 'id="themeToggle"' in response.text
        assert client.get("/static/style.css").status_code == 200
        assert client.get("/static/app.js").status_code == 200
        assert client.get("/static/capture.js").status_code == 200
        assert client.get("/api/status").json()["ready"]
        assert client.get("/", headers={"Host": "evil.example"}).status_code == 400
    with TestClient(create_app(models, allowed_hosts=["*"]), base_url="http://127.0.0.1") as client:
        assert client.get("/", headers={"Host": "custom.domain.example"}).status_code == 200
    with TestClient(create_app(models), base_url="http://127.0.0.1") as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                "ws://127.0.0.1/ws", headers={"Origin": "http://evil.example"}
            ):
                pass
        with client.websocket_connect(
            "ws://127.0.0.1/ws", headers={"Origin": "http://127.0.0.1"}
        ) as ws:
            ws.send_text("not json")
            assert ws.receive_json()["type"] == "error"
            ws.send_json(["invalid"])
            assert ws.receive_json()["type"] == "error"
            ws.send_json({"type": "text", "text": "Hei", "speed": 0.8})
            assert [ws.receive_json()["type"] for _ in range(3)] == ["transcript", "text", "done"]
        with client.websocket_connect(
            "ws://127.0.0.1/ws", headers={"Origin": "http://127.0.0.1"}
        ) as ws:
            ws.send_json({"type": "text", "text": "Hei igjen", "speed": "invalid"})
            assert [ws.receive_json()["type"] for _ in range(3)] == ["transcript", "text", "done"]
        assert models.histories == [[], []]
        assert models.speeds == [0.8, 0.85]
        with client.websocket_connect(
            "ws://127.0.0.1/ws", headers={"Origin": "http://127.0.0.1"}
        ) as ws:
            ws.send_json({"type": "text", "text": "fail"})
            assert ws.receive_json()["type"] == "error"
        models.entered.clear()
        with client.websocket_connect(
            "ws://127.0.0.1/ws", headers={"Origin": "http://127.0.0.1"}
        ) as ws:
            ws.send_json({"type": "text", "text": "wait"})
            assert models.entered.wait(2)
        assert models.cancelled.wait(2)


def test_silence_and_inference_failure_release_model_lock():
    models = Models.__new__(Models)
    models.lock = threading.Lock()

    class SilentRecognizer:
        def transcribe(self, *args, **kwargs):
            return iter([]), None

    models.stt = SilentRecognizer()
    events = []
    models.run_turn(None, recording(), [], threading.Event(), events.append)
    assert events[-1] == {"type": "done", "empty": True}
    assert not models.lock.locked()

    class BrokenRecognizer:
        def transcribe(self, *args, **kwargs):
            raise RuntimeError("broken")

    models.stt = BrokenRecognizer()
    with pytest.raises(RuntimeError):
        models.run_turn(None, recording(), [], threading.Event(), events.append)
    assert not models.lock.locked()
    messages = chat_messages([], "Hei")
    assert messages[-1] == {"role": "user", "content": "Hei"}
    assert messages[0] == {"role": "system", "content": SYSTEM_PROMPT}


def test_system_prompt_validation_and_connection_isolation():
    models = FakeModels()
    with TestClient(create_app(models), base_url="http://127.0.0.1") as client:
        info = client.get("/api/status").json()
        assert info["system_prompt"] == SYSTEM_PROMPT
        assert info["max_system_prompt_chars"] == MAX_SYSTEM_PROMPT_CHARS
        with client.websocket_connect(
            "ws://127.0.0.1/ws", headers={"Origin": "http://127.0.0.1"}
        ) as ws:
            for invalid in (None, [], 42, "", "  ", "x" * (MAX_SYSTEM_PROMPT_CHARS + 1)):
                ws.send_json({"type": "text", "text": "Hei", "system_prompt": invalid})
                assert "system prompt" in ws.receive_json()["message"]
            assert not models.entered.is_set()
            for prompt, payload in (
                ("Snakk om mat.", {"type": "text", "text": "Hei"}),
                (
                    "Snakk om været.",
                    {"type": "audio", "data": base64.b64encode(recording()).decode()},
                ),
            ):
                ws.send_json({**payload, "system_prompt": prompt})
                assert [ws.receive_json()["type"] for _ in range(3)] == [
                    "transcript",
                    "text",
                    "done",
                ]
            assert models.prompts == ["Snakk om mat.", "Snakk om været."]
        with client.websocket_connect(
            "ws://127.0.0.1/ws", headers={"Origin": "http://127.0.0.1"}
        ) as ws:
            ws.send_json({"type": "text", "text": "Hei"})
            assert [ws.receive_json()["type"] for _ in range(3)] == ["transcript", "text", "done"]
        assert models.prompts[-1] == SYSTEM_PROMPT
    custom = "Svar på norsk.\nSpør om mat."
    assert (
        parse_turn({"type": "text", "text": "Hei", "system_prompt": "x" * 4000})[-1] == "x" * 4000
    )
    messages = chat_messages([], "Hei", custom)
    assert messages[0] == {"role": "system", "content": custom}


def test_downloaded_models_and_session_selection(tmp_path):
    root = tmp_path / "models"
    for name in (
        "stt/complete/model.bin",
        "stt/complete/config.json",
        "stt/complete/tokenizer.json",
        "stt/incomplete/model.bin",
        "llm/first.gguf",
        "tts/voice.onnx",
        "tts/voice.onnx.json",
        "tts/incomplete.onnx",
    ):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    outside = tmp_path / "outside.gguf"
    outside.touch()
    (root / "llm" / "escape.gguf").symlink_to(outside)
    discovered = discover_models(root)
    assert list(discovered["stt"]) == ["stt/complete"]
    assert list(discovered["llm"]) == ["llm/first.gguf"]
    assert list(discovered["tts"]) == ["tts/voice.onnx"]

    first = root / "llm/split-00001-of-00002.gguf"
    second = root / "llm/split-00002-of-00002.gguf"
    first.touch()
    assert first.relative_to(root).as_posix() not in discover_models(root)["llm"]
    second.symlink_to(outside)
    assert first.relative_to(root).as_posix() not in discover_models(root)["llm"]
    second.unlink()
    second.touch()
    assert list(discover_models(root)["llm"]) == ["llm/first.gguf", first.relative_to(root).as_posix()]

    models = FakeModels()
    for invalid in (
        {"llm": "../../outside.gguf"},
        {"tts": []},
        {"url": "https://example.com"},
        None,
    ):
        with pytest.raises(ValueError):
            models.selection(invalid)
    with TestClient(create_app(models), base_url="http://127.0.0.1") as client:
        catalog = client.get("/api/status").json()
        assert catalog["llm_device"] == "CPU"
        assert len(catalog["models"]["stt"]) == 2
        assert catalog["selected"] == models.defaults
        with client.websocket_connect(
            "ws://127.0.0.1/ws", headers={"Origin": "http://127.0.0.1"}
        ) as ws:
            ws.send_json({"type": "text", "text": "Hei", "models": {"stt": "stt/second"}})
            assert [ws.receive_json()["type"] for _ in range(3)] == ["transcript", "text", "done"]
            ws.send_json({"type": "text", "text": "Bytt", "models": {"stt": "stt/first"}})
            assert "Stop" in ws.receive_json()["message"]
        assert models.selections[0]["stt"] == "stt/second"
        with client.websocket_connect(
            "ws://127.0.0.1/ws", headers={"Origin": "http://127.0.0.1"}
        ) as ws:
            ws.send_json({"type": "text", "text": "Hei", "models": {"stt": "stt/first"}})
            assert [ws.receive_json()["type"] for _ in range(3)] == ["transcript", "text", "done"]
        assert models.selections[-1]["stt"] == "stt/first"


def test_cuda_runtime_library_order_and_optional_install(monkeypatch, tmp_path):
    from importlib.metadata import PackageNotFoundError
    from types import SimpleNamespace

    monkeypatch.setattr("pratevenn.models.sys.platform", "linux")
    monkeypatch.setattr(
        "pratevenn.models.distribution",
        lambda name: SimpleNamespace(locate_file=lambda library: tmp_path / library),
    )
    loaded = []
    monkeypatch.setattr("pratevenn.models.ctypes.CDLL", lambda path, mode: loaded.append(Path(path).name))
    load_cuda_runtime()
    assert loaded == ["libcudart.so.12", "libcublasLt.so.12", "libcublas.so.12"]

    def missing(name):
        raise PackageNotFoundError(name)

    monkeypatch.setattr("pratevenn.models.distribution", missing)
    loaded.clear()
    load_cuda_runtime()
    assert not loaded
    monkeypatch.setattr("pratevenn.models.sys.platform", "darwin")
    load_cuda_runtime()
    assert not loaded


@pytest.mark.parametrize(
    "supported,gpu_layers,gpu_error",
    [
        (False, -1, None),
        (True, -1, None),
        (True, 4, None),
        (True, 0, None),
        (True, -1, ValueError),
        (True, -1, RuntimeError),
        (True, -1, MemoryError),
        (True, -1, OSError),
    ],
)
def test_model_reuse_replacement_and_failed_load(monkeypatch, supported, gpu_layers, gpu_error):
    load_cuda_runtime()
    import llama_cpp

    created = []
    attempts = []

    class LocalModel:
        def __init__(self, model_path, **kwargs):
            attempts.append((model_path, kwargs["n_gpu_layers"]))
            assert kwargs["offload_kqv"] == kwargs["op_offload"] == (kwargs["n_gpu_layers"] != 0)
            if model_path == "unloadable":
                raise RuntimeError("Cannot load on either device")
            if model_path == "cpu-only" and kwargs["n_gpu_layers"] != 0:
                raise MemoryError("This model does not fit in GPU memory")
            if gpu_error and kwargs["n_gpu_layers"] != 0:
                raise gpu_error("GPU memory or backend unavailable")
            self.closed = False
            self.metadata = (
                {}
                if model_path == "broken"
                else {
                    "tokenizer.chat_template": "{{ enable_thinking }}:{{ messages[0].content }}:{{ messages[-1].content }}"
                }
            )
            created.append(self)

        def token_eos(self):
            return 2

        def token_bos(self):
            return 1

        def detokenize(self, tokens, **kwargs):
            return b"</s>" if tokens == [2] else b"<s>"

        def tokenize(self, text, **kwargs):
            return list(text)

        def close(self):
            self.closed = True

    monkeypatch.setattr(llama_cpp, "Llama", LocalModel)
    monkeypatch.setattr(llama_cpp, "llama_supports_gpu_offload", lambda: supported)
    models = Models.__new__(Models)
    models.threads = 1
    models.gpu_layers = gpu_layers
    models.loaded = {}
    models.available = {
        "llm": {
            name: Path(name) for name in ("first", "broken", "second", "unloadable", "cpu-only")
        }
    }
    models.load({"llm": "first"})
    expected_layers = gpu_layers if supported else 0
    assert attempts == (
        [("first", expected_layers), ("first", 0)]
        if expected_layers and gpu_error
        else [("first", expected_layers)]
    )
    device = "GPU" if expected_layers and not gpu_error else "CPU"
    assert models.llm_device == device
    first = models.llm
    before = len(attempts)
    models.load({"llm": "first"})
    assert len(created) == 1 and len(attempts) == before
    with pytest.raises(ValueError, match="chat template"):
        models.load({"llm": "broken"})
    assert models.llm is first and not first.closed
    assert created[-1].closed and models.loaded == {"llm": "first"}
    assert models.llm_device == device
    with pytest.raises(RuntimeError, match="either device"):
        models.load({"llm": "unloadable"})
    assert models.llm is first and not first.closed
    assert models.loaded == {"llm": "first"} and models.llm_device == device
    models.load({"llm": "second"})
    assert first.closed and models.llm is created[-1]
    models.defaults = {"llm": "first"}
    assert models.options()["llm_device"] == device
    previous = models.llm
    models.load({"llm": "cpu-only"})
    assert previous.closed and models.llm_device == "CPU"
    assert models.options()["llm_device"] == "CPU"
    tokens, formatted = models.prompt([], "Hei", "Snakk om mat.")
    assert bytes(tokens).decode() == formatted.prompt == "False:Snakk om mat.:Hei"


def test_turn_keeps_custom_prompt_when_trimming_history():
    from types import SimpleNamespace

    models = Models.__new__(Models)
    models.lock = threading.Lock()
    prompts = []

    def prompt(history, text, system_prompt):
        prompts.append(system_prompt)
        return (
            [0] * ((PROMPT_TOKENS + 1) if history else 10),
            SimpleNamespace(stop=[], stopping_criteria=None),
        )

    models.prompt = prompt
    models.llm = SimpleNamespace(
        create_completion=lambda *args, **kwargs: iter([{"choices": [{"text": "Hei!"}]}]),
        n_tokens=12,
    )
    models.speak = lambda text: recording()
    history = [{"role": "user", "content": "Hei"}, {"role": "assistant", "content": "Hei!"}]
    events = []
    models.run_turn(
        "Hei igjen",
        None,
        history,
        threading.Event(),
        events.append,
        system_prompt="Snakk om mat.",
    )
    assert prompts == ["Snakk om mat.", "Snakk om mat."]
    assert history[0]["content"] == "Hei igjen"
    assert events[-1]["type"] == "done"
    assert events[0]["llm_device"] == "CPU"
    assert not models.lock.locked()
    usage = [event for event in events if event["type"] == "context"]
    assert usage == [
        {"type": "context", "tokens": 10, "capacity": CONTEXT_TOKENS, "omitted": 2, "messages": 1},
        {"type": "context", "tokens": 12, "capacity": CONTEXT_TOKENS, "omitted": 2, "messages": 2},
    ]


def test_download_profiles_and_full_cli(tmp_path, monkeypatch):
    import huggingface_hub

    from pratevenn import __main__ as cli

    requests = []

    def local_download(repo, filename, *, revision, local_dir):
        assert len(revision) == 40
        requests.append((repo, filename, local_dir))
        path = local_dir / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(repo + filename)
        return str(path)

    monkeypatch.setattr(huggingface_hub, "hf_hub_download", local_download)
    for full, semantic, counts in (
        (False, False, (1, 1, 1)),
        (True, False, (3, 2, 2)),
        (False, True, (4, 1, 1)),
    ):
        root = tmp_path / f"{full}-{semantic}"
        download_models(root, full=full, semantic=semantic)
        catalog = discover_models(root)
        assert tuple(len(catalog[kind]) for kind in ("stt", "llm", "tts")) == counts
        assert all("tiny" not in path and "base" not in path for path in catalog["stt"])
        for path in catalog["stt"].values():
            assert (path / "tokenizer.json").read_text() == (
                path.parent / "tokenizer.json"
            ).read_text()
        models = Models.__new__(Models)
        models.available = catalog
        models.defaults = {kind: next(iter(choices)) for kind, choices in catalog.items()}
        assert len({choice["label"] for choice in models.options()["models"]["stt"]}) == counts[0]
        (root / "llm" / "AAA-old-model.gguf").touch()
        monkeypatch.setattr(Models, "load", lambda self, selection: None)
        assert Models(root).defaults["llm"] == "llm/gemma-4-E2B/gemma-4-E2B-it-Q4_0.gguf"
        assert Models(root).defaults["stt"] == (
            "stt/nb-whisper-large-verbatim/ct2" if full else "stt/ct2"
        )
    assert any(repo == "ggml-org/gemma-4-E2B-it-GGUF" for repo, _, _ in requests)
    assert any(repo == "ggml-org/gemma-4-E4B-it-GGUF" for repo, _, _ in requests)
    assert any(repo == "NbAiLab/nb-whisper-small-verbatim" for repo, _, _ in requests)
    assert any(repo == "NbAiLab/nb-whisper-large-verbatim" for repo, _, _ in requests)
    assert any(repo == "NbAiLab/nb-whisper-large" for repo, _, _ in requests)
    calls = []
    monkeypatch.setattr(
        cli,
        "download_models",
        lambda directory, full=False, semantic=False: calls.append((directory, full, semantic)),
    )
    monkeypatch.setattr(
        "sys.argv",
        ["pratevenn", "setup", "--full", "--semantic", "--model-dir", str(tmp_path)],
    )
    cli.main()
    assert calls == [(tmp_path, True, True)]


def test_start_cli_and_argument_validation(tmp_path, monkeypatch):
    import uvicorn

    from pratevenn import __main__ as cli

    loads, servers = [], []

    def load_models(directory, threads, gpu_layers, context_tokens=8192):
        loads.append((directory, threads, gpu_layers, context_tokens))
        return FakeModels()

    monkeypatch.setattr("pratevenn.models.Models", load_models)
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: servers.append(kwargs))
    monkeypatch.setattr(
        "sys.argv",
        ["pratevenn", "start", "--model-dir", str(tmp_path), "--port", "9000", "--threads", "2"],
    )
    cli.main()
    assert loads == [(tmp_path, 2, -1, 8192)]
    assert servers == [{"host": "127.0.0.1", "port": 9000, "ws_max_size": MAX_MESSAGE_BYTES}]
    for flag, value in (
        ("--port", "0"),
        ("--port", "65536"),
        ("--threads", "0"),
        ("--gpu-layers", "-2"),
        ("--context-size", "512"),
        ("--context-size", "70000"),
    ):
        monkeypatch.setattr("sys.argv", ["pratevenn", "start", flag, value])
        with pytest.raises(SystemExit) as error:
            cli.main()
        assert error.value.code == 2
    assert len(loads) == len(servers) == 1
    for layers in (0, 20):
        monkeypatch.setattr(
            "sys.argv",
            ["pratevenn", "start", "--model-dir", str(tmp_path), "--gpu-layers", str(layers)],
        )
        cli.main()
        assert loads[-1] == (tmp_path, 8, layers, 8192)

    monkeypatch.setattr(
        "sys.argv",
        ["pratevenn", "start", "--model-dir", str(tmp_path), "--context-size", "4096"],
    )
    cli.main()
    assert loads[-1] == (tmp_path, 8, -1, 4096)

    monkeypatch.setenv("PRATEVENN_CONTEXT_SIZE", "16384")
    monkeypatch.setattr("sys.argv", ["pratevenn", "start", "--model-dir", str(tmp_path)])
    cli.main()
    assert loads[-1] == (tmp_path, 8, -1, 16384)

    monkeypatch.setenv("PRATEVENN_CONTEXT_SIZE", "invalid")
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2
    monkeypatch.setattr("sys.argv", ["pratevenn", "start", "--context-size", "4096"])
    cli.main()
    assert loads[-1][-1] == 4096

    downloads = []
    monkeypatch.setattr(cli, "download_models", lambda *args, **kwargs: downloads.append(args))
    monkeypatch.setattr("sys.argv", ["pratevenn", "setup", "--model-dir", str(tmp_path)])
    cli.main()
    assert downloads == [(tmp_path,)]


def test_default_context_size_is_fixed_across_connections(monkeypatch):
    models = FakeModels()
    original_run = models.run_turn

    def run(*args, **kwargs):
        models.context_tokens = kwargs.get("context_tokens", models.context_tokens)
        original_run(*args, **kwargs)

    def turn(socket, **settings):
        socket.send_json({"type": "text", "text": "Hei", **settings})
        assert [socket.receive_json()["type"] for _ in range(3)] == ["transcript", "text", "done"]

    monkeypatch.setattr(models, "run_turn", run)
    headers = {"Origin": "http://127.0.0.1"}
    with TestClient(create_app(models), base_url="http://127.0.0.1") as client:
        with client.websocket_connect("ws://127.0.0.1/ws", headers=headers) as first:
            turn(first)
            with client.websocket_connect("ws://127.0.0.1/ws", headers=headers) as second:
                turn(second, context_size=2048)
                assert models.context_tokens == 2048
                turn(first)
                assert models.context_tokens == 8192
                turn(second)
                assert models.context_tokens == 2048
            # New connections also use the server's default, not the last loaded size.
            with client.websocket_connect("ws://127.0.0.1/ws", headers=headers) as third:
                turn(third)
    assert models.context_tokens_list == [8192, 2048, 8192, 2048, 8192]


def test_context_size_selection_and_validation():
    models = FakeModels()
    assert models.validate_context_size(2048) == 2048
    assert models.validate_context_size(65536) == 65536
    for invalid in (512, 1023, 65537, "8192", True, False, None, 4096.0):
        with pytest.raises(ValueError):
            models.validate_context_size(invalid)

    models.context_tokens = 8192
    assert models.prompt_tokens == 8192 - 592
    models.context_tokens = 600
    assert models.prompt_tokens == 256
    models.context_tokens = 8192

    opts = models.options()
    assert opts["context_size"] == 8192
    assert opts["context_sizes"] == [2048, 4096, 8192, 16384, 32768]

    with TestClient(create_app(models), base_url="http://127.0.0.1") as client:
        with client.websocket_connect(
            "ws://127.0.0.1/ws", headers={"Origin": "http://127.0.0.1"}
        ) as ws:
            ws.send_json({"type": "text", "text": "Hei", "context_size": 4096})
            assert [ws.receive_json()["type"] for _ in range(3)] == ["transcript", "text", "done"]
            assert models.context_tokens_list[-1] == 4096
            ws.send_json({"type": "text", "text": "Hei", "context_size": 16384})
            msg = ws.receive_json()
            assert msg["type"] == "error"
            assert "Stop" in msg["message"]

        for invalid in ("invalid", 500, 70000, True):
            with client.websocket_connect(
                "ws://127.0.0.1/ws", headers={"Origin": "http://127.0.0.1"}
            ) as ws:
                ws.send_json({"type": "text", "text": "Hei", "context_size": invalid})
                msg = ws.receive_json()
                assert msg["type"] == "error"
                assert "context size" in msg["message"].lower()


def test_voice_speakers_and_model_reuse(tmp_path, monkeypatch):
    from piper import PiperVoice

    voice = tmp_path / "tts" / "voice.onnx"
    voice.parent.mkdir()
    voice.touch()
    config = Path(str(voice) + ".json")
    config.write_text(
        json.dumps(
            {
                "num_speakers": 3,
                "default_speaker_id": 2,
                "speaker_id_map": {
                    "KNN": 0,
                    "KSV": 1,
                    "Default": 2,
                    "Invalid": 3,
                    "WrongType": True,
                },
            }
        )
    )
    catalog = discover_models(tmp_path)
    assert list(catalog["tts"]) == [
        "tts/voice.onnx",
        "tts/voice.onnx#speaker=0:KNN",
        "tts/voice.onnx#speaker=1:KSV",
    ]
    loads, speakers, length_scales = [], [], []

    class Voice:
        def synthesize_wav(self, text, audio, syn_config):
            speakers.append(syn_config.speaker_id)
            length_scales.append(syn_config.length_scale)
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(22050)
            audio.writeframes(bytes(4410))

    def load(path):
        loads.append(path)
        return Voice()

    monkeypatch.setattr(PiperVoice, "load", load)
    models = Models.__new__(Models)
    models.available, models.loaded = catalog, {}
    for identifier in (*catalog["tts"], "tts/voice.onnx"):
        models.load({"tts": identifier})
        assert models.speak("Hei")
    assert models.speak("Hei sakte", length_scale=1.25)
    assert len(loads) == 1
    assert speakers == [None, 0, 1, None, None]
    assert length_scales == [1.176, 1.176, 1.176, 1.176, 1.25]
    models.defaults = {"tts": "tts/voice.onnx"}
    assert models.options()["models"]["tts"][1]["label"] == "voice · KNN"
    with pytest.raises(ValueError):
        models.selection({"tts": "tts/voice.onnx#speaker=99:invalid"})
    config.write_text("invalid JSON")
    assert list(discover_models(tmp_path)["tts"]) == ["tts/voice.onnx"]
