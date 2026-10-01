"""Optional local text archives, separate from the model's working context."""

import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

# ponytail: 200 turns per chat bounds request size and RAM; paginate for longer chats.
MAX_CHAT_MESSAGES = 400
# JSON can use two six-byte Unicode escapes for each character, plus message metadata.
MAX_CHAT_BYTES = MAX_CHAT_MESSAGES // 2 * (1000 + 4000) * 12 + 100_000


def validate_messages(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value or len(value) > MAX_CHAT_MESSAGES:
        raise ValueError("Expected a chat with 1 to 200 completed turns.")
    if len(value) % 2:
        raise ValueError("Only completed conversation turns can be saved or reviewed.")
    messages = []
    for index, item in enumerate(value):
        role = "user" if index % 2 == 0 else "assistant"
        if not isinstance(item, dict) or item.get("role") != role:
            raise ValueError("Chat messages must alternate between learner and Pratevenn.")
        text = item.get("content")
        if (
            not isinstance(text, str)
            or not text.strip()
            or len(text) > (1000 if index % 2 == 0 else 4000)
        ):
            raise ValueError("Invalid or oversized chat message.")
        source = item.get("source", "text")
        if source not in ("text", "audio"):
            raise ValueError("Invalid message source.")
        messages.append({"role": role, "content": text, "source": source})
    return messages


class ChatStore:
    def __init__(self, path: Path | None = None) -> None:
        configured = os.environ.get("PRATEVENN_DATA_DIR") or os.environ.get("KONVERS_DATA_DIR")
        directory = Path.home() / ".local/share/pratevenn"
        legacy = directory.with_name("konvers")
        if configured:
            directory = Path(configured)
        elif not directory.exists() and legacy.is_dir():
            directory = legacy
        self.path = path if path is not None else directory / "chats.sqlite3"

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.touch(mode=0o600, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA secure_delete = ON")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS chats "
            "(id TEXT PRIMARY KEY, title TEXT NOT NULL, "
            "messages TEXT NOT NULL, updated TEXT NOT NULL)"
        )
        return connection

    def list_chats(self) -> list[dict[str, str]]:
        if not self.path.exists():
            return []
        with closing(self.connect()) as connection:
            rows = connection.execute("SELECT id, title, updated FROM chats ORDER BY updated DESC")
            return [dict(zip(("id", "title", "updated"), row, strict=True)) for row in rows]

    def get(self, identifier: str) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT messages FROM chats WHERE id = ?", (identifier,)
            ).fetchone()
            return {"id": identifier, "messages": json.loads(row[0])} if row else None

    def save(self, identifier: str, messages: list[dict[str, str]]) -> None:
        with closing(self.connect()) as connection, connection:
            connection.execute(
                "INSERT INTO chats VALUES (?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')) "
                "ON CONFLICT(id) DO UPDATE SET title=excluded.title, "
                "messages=excluded.messages, updated=excluded.updated",
                (identifier, messages[0]["content"][:60], json.dumps(messages, ensure_ascii=False)),
            )

    def delete(self, identifier: str | None = None) -> None:
        if not self.path.exists():
            return
        with closing(self.connect()) as connection, connection:
            if identifier is None:
                connection.execute("DELETE FROM chats")
            else:
                connection.execute("DELETE FROM chats WHERE id = ?", (identifier,))
