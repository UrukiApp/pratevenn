"""Local web server and cancellable conversation connections."""

import asyncio
import base64
import binascii
import json
import logging
import threading
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from pratevenn.history import MAX_CHAT_BYTES, MAX_CHAT_MESSAGES, ChatStore, validate_messages
from pratevenn.models import (
    MAX_AUDIO_BYTES,
    MAX_SYSTEM_PROMPT_CHARS,
    SYSTEM_PROMPT,
    Models,
    validate_audio,
)

WEB = Path(__file__).parent / "web"
# A review can include both its messages and restored history on a new connection.
MAX_MESSAGE_BYTES = 2 * MAX_CHAT_BYTES + 1_400_000


def parse_turn(message: dict[str, Any]) -> tuple[str | None, bytes | None, str]:
    system_prompt = message.get("system_prompt", SYSTEM_PROMPT)
    if (
        not isinstance(system_prompt, str)
        or not system_prompt.strip()
        or len(system_prompt) > MAX_SYSTEM_PROMPT_CHARS
    ):
        raise ValueError(
            f"Please enter a system prompt of 1 to {MAX_SYSTEM_PROMPT_CHARS} characters."
        )
    if message.get("type") == "text":
        text = message.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > 1000:
            raise ValueError("Please enter between 1 and 1000 characters.")
        return text.strip(), None, system_prompt
    if message.get("type") != "audio":
        raise ValueError("Unknown message type.")
    encoded = message.get("data")
    if not isinstance(encoded, str) or len(encoded) > MAX_AUDIO_BYTES * 4 // 3 + 4:
        raise ValueError("Invalid or oversized recording.")
    try:
        audio = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as error:
        raise ValueError("Invalid audio encoding.") from error
    validate_audio(audio)
    return None, audio, system_prompt


def archive_request(request: Request, response: Response) -> None:
    """Require a browser header that cross-origin pages cannot send without CORS permission."""
    origin = urlsplit(request.headers.get("origin", ""))
    if request.headers.get("x-pratevenn-request") != "1" or (
        request.headers.get("origin")
        and (origin.scheme not in {"http", "https"} or origin.netloc != request.headers.get("host"))
    ):
        raise HTTPException(
            403, "Chat history is available only from the local Pratevenn interface."
        )
    response.headers["Cache-Control"] = "no-store"


def create_app(
    models: Models,
    chat_store: ChatStore | None = None,
    allowed_hosts: list[str] | None = None,
) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    store = chat_store if chat_store is not None else ChatStore()
    hosts = allowed_hosts if allowed_hosts is not None else ["localhost", "127.0.0.1", "[::1]"]
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(WEB / "index.html")

    @app.get("/api/status")
    async def status() -> dict[str, Any]:
        return {
            "ready": True,
            **models.options(),
            "system_prompt": SYSTEM_PROMPT,
            "max_system_prompt_chars": MAX_SYSTEM_PROMPT_CHARS,
            "max_chat_messages": MAX_CHAT_MESSAGES,
        }

    @app.get("/api/chats", dependencies=[Depends(archive_request)])
    def saved_chats() -> list[dict[str, str]]:
        return store.list_chats()

    @app.get("/api/chats/{identifier}", dependencies=[Depends(archive_request)])
    def saved_chat(identifier: UUID) -> dict[str, Any]:
        chat = store.get(str(identifier))
        if chat is None:
            raise HTTPException(404, "This saved chat no longer exists.")
        return chat

    @app.put("/api/chats/{identifier}", dependencies=[Depends(archive_request)])
    async def save_chat(identifier: UUID, request: Request) -> dict[str, bool]:
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            raise HTTPException(415, "Expected JSON chat messages.")
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > MAX_CHAT_BYTES:
                raise HTTPException(413, "Chat is too large to save.")
        try:
            messages = validate_messages(json.loads(body))
        except (ValueError, UnicodeError) as error:
            raise HTTPException(400, str(error)) from error
        await asyncio.to_thread(store.save, str(identifier), messages)
        return {"saved": True}

    @app.delete("/api/chats/{identifier}", dependencies=[Depends(archive_request)])
    def delete_chat(identifier: UUID) -> dict[str, bool]:
        store.delete(str(identifier))
        return {"deleted": True}

    @app.delete("/api/chats", dependencies=[Depends(archive_request)])
    def delete_chats() -> dict[str, bool]:
        store.delete()
        return {"deleted": True}

    @app.websocket("/ws")
    async def conversation(socket: WebSocket) -> None:
        origin = urlsplit(socket.headers.get("origin", ""))
        if origin.scheme not in {"http", "https"} or origin.netloc != socket.headers.get("host"):
            await socket.close(code=1008)
            return
        await socket.accept()
        history: list[dict[str, str]] = []
        selection: dict[str, str] | None = None
        context_size: int | None = None
        cancelled = threading.Event()
        turn_finished = threading.Event()
        worker: asyncio.Task[None] | None = None
        loop = asyncio.get_running_loop()

        def emit(event: dict[str, Any]) -> None:
            if not cancelled.is_set():
                if event.get("type") == "done":
                    turn_finished.set()
                asyncio.run_coroutine_threadsafe(socket.send_json(event), loop).result(timeout=10)

        async def respond(
            values: tuple[str | None, bytes | None, str] | None,
            review: list[dict[str, str]] | None,
            feedback: bool = False,
            speed: float = 0.85,
            context_tokens: int | None = None,
        ) -> None:
            try:
                kwargs: dict[str, Any] = (
                    {"context_tokens": context_tokens} if context_tokens is not None else {}
                )
                if review is not None:
                    assert selection is not None
                    await asyncio.to_thread(
                        models.review_turn,
                        review,
                        cancelled,
                        emit,
                        selection,
                        **kwargs,
                    )
                else:
                    assert values is not None
                    await asyncio.to_thread(
                        models.run_turn,
                        *values[:2],
                        history,
                        cancelled,
                        emit,
                        selection,
                        system_prompt=values[2],
                        feedback=feedback,
                        speed=speed,
                        **kwargs,
                    )
            except ValueError as error:
                if not cancelled.is_set():
                    await socket.send_json({"type": "error", "message": str(error)})
            except Exception:
                logging.exception("Local conversation turn failed")
                if not cancelled.is_set():
                    await socket.send_json(
                        {
                            "type": "error",
                            "message": (
                                "The model could not finish. Try again or restart Pratevenn."
                            ),
                        }
                    )

        try:
            while True:
                raw = await socket.receive_text()
                if len(raw.encode("utf-8")) > MAX_MESSAGE_BYTES:
                    await socket.close(code=1009)
                    break
                try:
                    message = json.loads(raw)
                    if not isinstance(message, dict):
                        raise ValueError("Expected a conversation message.")
                    review = (
                        validate_messages(message.get("messages"))
                        if message.get("type") == "review"
                        else None
                    )
                    values = None if review is not None else parse_turn(message)
                    requested = models.selection(message.get("models", {}))
                    raw_context = message.get("context_size")
                    requested_context = (
                        models.validate_context_size(raw_context)
                        if raw_context is not None
                        else context_size
                    )
                    if selection is not None and (
                        requested != selection
                        or (requested_context is not None and requested_context != context_size)
                    ):
                        raise ValueError(
                            "Stop the conversation before changing models or context size."
                        )
                    if worker is not None and not worker.done():
                        if not turn_finished.is_set():
                            raise ValueError("Please wait for this turn.")
                        # A delivered completion can precede thread cleanup and lock release.
                        await worker
                    if "history" in message:
                        if selection is not None:
                            raise ValueError("Reopen a saved chat before starting a connection.")
                        restored = validate_messages(message["history"])
                        history[:] = [
                            {"role": item["role"], "content": item["content"]} for item in restored
                        ]
                    selection = requested
                    if requested_context is not None:
                        context_size = requested_context
                except (ValueError, TypeError) as error:
                    await socket.send_json({"type": "error", "message": str(error)})
                    continue
                raw_speed = message.get("speed", 0.85)
                speed = (
                    float(raw_speed)
                    if isinstance(raw_speed, (int, float)) and 0.5 <= raw_speed <= 2.0
                    else 0.85
                )
                turn_finished.clear()
                worker = asyncio.create_task(
                    respond(
                        values,
                        review,
                        bool(message.get("feedback", False)),
                        speed=speed,
                        context_tokens=context_size,
                    )
                )
        except WebSocketDisconnect:
            pass
        finally:
            cancelled.set()
            if worker is not None:
                await worker

    app.mount("/static", StaticFiles(directory=WEB), name="static")
    return app
