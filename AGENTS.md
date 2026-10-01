# AGENTS.md

This file provides guidance to coding agents working on Pratevenn.

## Mission

Pratevenn is a Python application for Norwegian language learners.
It provides a voice interface for practicing conversation with a local AI model interactively.
A Python server runs speech recognition, conversation, and speech synthesis, and serves a bundled browser interface.

Priorities, in order:

1. Local inference and privacy: audio and transcripts stay on the user's computer.
2. Reliable conversation turns, cancellation, and microphone cleanup.
3. Useful Norwegian replies and accessible voice and text interaction.
4. Small, clear changes that fit the existing Python and browser code.

## Core Rules

- Read the affected code and nearby tests before editing. Prefer focused changes, existing helpers, and standard library features.
- Use English for code, comments, documentation, and tests. Keep Norwegian prompts and conversation output in Bokmål.
- Support Python 3.11 through 3.14. Development uses Python 3.14, as specified in `.python-version`. Add type annotations to application functions and
  follow the Ruff and mypy settings in `pyproject.toml`.
- Use uv and `uv.lock` for Python dependencies. Run `uv add PACKAGE` or `uv add --dev PACKAGE`, and update both `pyproject.toml` and `uv.lock` when
  changing dependencies. Use `uv run --locked` for development commands.
- Keep the browser interface in plain HTML, CSS, and JavaScript with no build step. Preserve keyboard access, labels, status feedback, and the mobile
  layout.
- Keep comments focused on behavior that the code does not make clear.
- Pratevenn source is MIT-licensed. Dependencies and model weights keep their own licenses, including the existing GPL-licensed Piper runtime. Keep
  license disclosures accurate and do not commit or bundle model weights.

## Writing Style

- Write in simple, plain English. Use short sentences and everyday words.
- Use Oxford commas in inline lists: "a, b, and c" not "a, b, c".
- Do not use em dashes. Restructure the sentence, or use a colon or semicolon instead.
- Avoid colorful adjectives and adverbs. Write "adjacency query" not "blazing adjacency query".
- Prefer noun phrases for checklist items over imperative verbs. Write "temp directory teardown" not "tear down the temp directory".
- Headings in Markdown files must be in title case: "Build from Source" not "Build from source". Minor words stay lowercase unless they are the first
  word: the articles (a, an, the), the coordinating conjunctions (and, but, or, nor, so, yet, for), and the short prepositions (in, on, at, to, by,
  of,
  up, as, from, with, into, over).
- Do not bold the lead-in of a list item. Write "Vector and set similarity: ..." not "**Vector and set similarity**: ...".
- Use sentence case for the lead-in of a list item. Write "Seed selection: ..." not "Seed Selection: ...". Proper nouns keep their capitals.
- Capitalize only the first part of a hyphenated compound: "Full-text Search" in a heading, "Breadth-first" at the start of a sentence, and
  "breadth-first search" elsewhere. Never write "Breadth-First".
- Start each sentence with a capital letter, capitalize proper nouns (Rust, Cypher, LMDB), and leave common nouns lowercase in the middle of a
  sentence.
- Write correct and complete sentences. Avoid made-up words.
- Do not use a colon in place of a verb. A colon may join two clauses inside a complete sentence, introduce the gloss of a list item, or introduce an
  enumeration. It must not turn a sentence into a label and a definition: write "Merges vector search seeds with text search seeds, then expands via
  BFS" rather than "Hybrid retrieval: merges vector search seeds with text search seeds".
- Use participial phrases and abbreviations sparingly.

## Repository Layout

- `pratevenn/__main__.py` owns the `pratevenn setup` and `pratevenn start` commands, argument validation, and server startup.
- `pratevenn/models.py` owns pinned model downloads, local model discovery and selection, model loading, audio validation, prompts, and the inference
  pipeline.
- `pratevenn/app.py` owns FastAPI routes, WebSocket validation, connection history, cancellation, and serving the bundled interface.
- `pratevenn/web/index.html` and `style.css` own the interface and layout. `app.js` owns conversation state, model settings, microphone capture, and
  audio playback. `capture.js` owns the audio worklet.
- `pratevenn/__init__.py` holds the package version; keep it consistent with `pyproject.toml` when changing the version.
- `tests/test_conversation.py` covers validation, WebSocket behavior, cancellation, model discovery, downloads, and model reuse with fake or mocked
  models.
- `scripts/check_browser.py` exercises the real voice loop with Playwright and a synthetic Norwegian microphone recording.
- `pyproject.toml` defines packaging, dependencies, and quality tool settings. `uv.lock` pins the Python dependency environment.
- `Makefile` provides the development commands. `.pre-commit-config.yaml` defines Git hooks, and `.github/workflows/` contains test and publishing
  workflows.
- `flake.nix` provides optional Nix development tools. Python dependencies remain managed by uv.
- `README.md` documents current behavior and setup. `docs/index.md`, when present, describes the wider design and planned work; it is currently
  ignored by Git.

## Behavior to Preserve

- Model downloads happen only during explicit setup. Serving uses local model files without cloud inference, accounts, or API keys. Keep download
  revisions pinned and completed files reusable after an interrupted setup.
- The command-line interface binds to `127.0.0.1`. Preserve the HTTP host allowlist and same-origin WebSocket checks.
- Validate input before inference. Text messages are limited to 1000 characters. Audio must be complete, uncompressed mono, 16 kHz, 16-bit WAV,
  lasting 0.1 to 30 seconds. Keep encoded message size limits consistent with audio limits.
- Accept only complete model files inside the configured model directory, including resolved symlink targets. Validate selections against discovered
  models.
- One `Models` instance serves one turn at a time across browser tabs. Preserve the inference lock, its release on failure, and the busy response. Run
  blocking inference through `asyncio.to_thread` so the server can detect disconnections.
- History and cancellation belong to each WebSocket connection. Keep history bounded in RAM, and do not save audio or transcripts. Browser model
  preferences may use local storage.
- Model selections are fixed for a connection. Stop ends the session; starting again creates a fresh connection and allows another selection. Reuse
  loaded weights when possible, including when changing speakers from the same Piper model.
- Stop and page navigation release microphone tracks, end playback, close the connection, and cancel the current turn. Preserve session checks around
  async work so late output cannot enter a restarted session.
- Pause the microphone while processing and speaking. Resume only after both inference and queued playback finish. Keep pause and microphone threshold
  settings adjustable.

## Development Workflow

Use the existing Make targets:

```bash
make install       # Sync application and development dependencies from uv.lock.
make format        # Format Python with Ruff.
make test          # Run pytest with coverage; no model download is needed.
make build         # Build the source distribution and wheel.
```

Run lint checks with `uv run --locked ruff check --fix` and type checks with `uv run --locked mypy .`.

For manual use, `make models` downloads the default models, `make download-full` downloads the full bundled collection, and `make run` starts the
server.
These commands use `.models` by default; override it with `MODEL_DIR=/path/to/models`.
The command-line interface uses `PRATEVENN_MODEL_DIR` or `~/.cache/pratevenn` by default.

For Nix development, run `nix develop`, then `make install`.
Entering the shell must not install Python dependencies, download models, or start the server.
Format Nix changes with `nix fmt`, and check flake changes with `nix flake check --no-build`.

Run checks relevant to the change. For application code changes, run formatting, linting, type checking, and the relevant tests before declaring the
work done.
Run `make build` for packaging changes and update `README.md` when behavior or setup changes. Report any checks that could not run.

## Testing Expectations

- Keep normal tests deterministic and independent of model weights, network access, microphones, and GPUs. Use the existing fake models and pytest
  monkeypatching.
- Use temporary directories for model discovery and download tests. Mock downloads and native model loading rather than fetching or loading real
  weights.
- Cover validation, inference lock release, connection history, model selection, and cancellation when changing those behaviors.
- The optional browser check requires downloaded models, a running server, and Playwright Chromium. Follow `README.md` for setup; screenshots go in
  `.cache/`. Use it when verifying changes to voice capture, playback, Stop, or restart.
- Keep `.models/`, `.venv/`, `.cache/`, recordings, transcripts, and build artifacts out of commits and package distributions.
