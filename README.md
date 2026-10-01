<div align="center">

<h2>Pratevenn</h2>

[![Tests](https://img.shields.io/github/actions/workflow/status/UrukiApp/pratevenn/tests.yml?label=tests&style=flat&labelColor=282c34&logo=github)](https://github.com/UrukiApp/pratevenn/actions/workflows/tests.yml)
[![Coverage](https://img.shields.io/codecov/c/github/UrukiApp/pratevenn?label=coverage&style=flat&labelColor=282c34&logo=codecov)](https://codecov.io/gh/UrukiApp/pratevenn)
[![License](https://img.shields.io/badge/license-MIT-007ec6?style=flat&labelColor=282c34&logo=open-source-initiative)](LICENSE)
[![Container Images](https://img.shields.io/github/v/tag/UrukiApp/pratevenn?label=ghcr.io&style=flat&labelColor=282c34&logo=docker&color=507ec6&sort=semver)](https://github.com/UrukiApp/pratevenn/pkgs/container/pratevenn)

A private, local conversational AI tool for Norwegian language learners

</div>

---

Pratevenn provides an interactive voice interface for practicing conversation in Norwegian with a local AI model. Audio recordings, transcripts, and model inference stay entirely on your computer.

### Key Features

- Spoken conversation practice: Provides speech recognition with NB-Whisper and natural speech synthesis with Piper TTS.
- Local and private execution: Runs all audio processing, transcription, and language model inference on your machine with no external network requests, accounts, or telemetry.
- Norwegian language partner: Supports interactive conversation in Norwegian Bokmål powered by local Gemma models through llama.cpp.
- Real-time feedback and review: Offers optional grammar suggestions during conversation, as well as turn-by-turn chat reviews.
- Hardware acceleration options: Supports CPU execution and NVIDIA GPU acceleration via CUDA.
- Self-contained web interface: Delivers a clean browser interface with light and dark themes, adjustable speech speeds, and optional local history storage.

---

### Getting Started

> [!IMPORTANT]
> Web browsers require access via `http://localhost:8000`, `http://127.0.0.1:8000`, or an HTTPS origin to grant microphone permissions.
> Pratevenn uses local AI models that must be downloaded once before starting the application.

#### Running with Docker Compose

##### 1. Compose Configuration

Use the provided [docker-compose.yaml](docker-compose.yaml) or save the configuration below:

```yaml
services:
  pratevenn:
    image: ghcr.io/urukiapp/pratevenn:latest-cpu
    environment:
      - PRATEVENN_MODEL_DIR=/models
      - PRATEVENN_HOST=0.0.0.0
      - PRATEVENN_ALLOWED_HOSTS=*
    ports:
      - "8000:8000"
    volumes:
      - models:/models
    restart: unless-stopped

  pratevenn-cuda:
    image: ghcr.io/urukiapp/pratevenn:latest-cuda
    environment:
      - PRATEVENN_MODEL_DIR=/models
      - PRATEVENN_HOST=0.0.0.0
      - PRATEVENN_ALLOWED_HOSTS=*
      - NVIDIA_VISIBLE_DEVICES=all
      - NVIDIA_DRIVER_CAPABILITIES=compute,utility
    ports:
      - "8000:8000"
    volumes:
      - models:/models
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
    profiles:
      - cuda
    restart: unless-stopped

volumes:
  models:
```

##### 2. Download the Models

Download the default models into the shared volume before launching the service:

```sh
docker compose run --rm pratevenn setup
```

##### 3. Start Pratevenn

Start the CPU service in the background:

```sh
docker compose up -d
```

For NVIDIA GPU acceleration with CUDA, start the CUDA profile instead:

```sh
docker compose --profile cuda up -d pratevenn-cuda
```

Open <http://localhost:8000> in your browser and click **Start conversation**.

#### Managing Docker Containers

Use standard Docker Compose commands to manage Pratevenn:

```sh
docker compose up -d                    # Start Pratevenn
docker compose stop                     # Stop Pratevenn (models and data are kept)
docker compose down                     # Remove containers
docker compose down -v                  # Remove containers and downloaded model volumes
docker compose logs -f                  # Follow the log stream
```

---

#### Running from Source with Python

You can also run Pratevenn directly using Python 3.11 through 3.14 and [uv](https://docs.astral.sh/uv/).

##### 1. Clone the Repository

```sh
git clone https://github.com/UrukiApp/pratevenn.git
cd pratevenn
```

##### 2. Install Dependencies

Sync dependencies from the lockfile:

```sh
make install
```

##### 3. Download Models

Fetch the default speech recognition, conversation, and voice models:

```sh
make models
```

To download all bundled options, run `make download-full`.

##### 4. Start the Server

```sh
make run
```

Then navigate to <http://127.0.0.1:8000> in your web browser.

---

### Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for details on how to make a contribution.

### License

Pratevenn is licensed under the MIT License (see [LICENSE](LICENSE)). Dependencies and model weights keep their own licenses.
