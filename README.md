## Pratevenn

[![Tests](https://img.shields.io/github/actions/workflow/status/UrukiApp/pratevenn/tests.yml?label=tests&style=flat&labelColor=282c34&logo=github)](https://github.com/UrukiApp/pratevenn/actions/workflows/tests.yml)
[![Code Coverage](https://img.shields.io/codecov/c/github/UrukiApp/pratevenn?label=coverage&style=flat&labelColor=282c34&logo=codecov)](https://codecov.io/gh/UrukiApp/pratevenn)
[![License](https://img.shields.io/badge/license-MIT-007ec6?label=license&style=flat&labelColor=282c34&logo=open-source-initiative)](https://github.com/UrukiApp/pratevenn/blob/main/LICENSE)
[![Container Images](https://img.shields.io/github/v/tag/UrukiApp/pratevenn?label=ghcr.io&style=flat&labelColor=282c34&logo=docker&color=507ec6&sort=semver)](https://github.com/UrukiApp/pratevenn/pkgs/container/pratevenn)

---

Pratevenn is a private, local conversational AI tool for Norwegian language learners.

### Features

To be added.

---

### Quickstart

To be added.

---

### Documentation

Docker ports are published only on `127.0.0.1`, and the local Host allowlist remains enabled.
Rebuild existing images to apply these changes.
The Docker Make targets save chats in the named `pratevenn-data` volume at `/data`.
Compose uses a separate named `data` volume. These volumes survive container removal.
Before replacing an older container, copy its `/home/pratevenn/.local/share/pratevenn/chats.sqlite3`
to the data volume to keep existing saved chats.
`PRATEVENN_DATA_DIR` sets the chat database directory; Docker images use `/data`.

Each conversation connection keeps its context size until you stop it.
The `--context-size` option overrides `PRATEVENN_CONTEXT_SIZE`.

---

### Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for details on how to make a contribution.

### License

Pratevenn is licensed under the MIT License (see [LICENSE](LICENSE)).
