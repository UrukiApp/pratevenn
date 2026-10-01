UV          ?= uv
MODEL_DIR   ?= .models
CUDA        ?= $(shell if [ "$$(uname -sm)" = "Linux x86_64" ] && nvidia-smi -L >/dev/null 2>&1; then echo 1; else echo 0; fi)
UV_EXTRA     = --extra $(if $(filter 1,$(CUDA)),cuda,cpu) --extra dev

CACHE_DIRS  = .mypy_cache .pytest_cache .ruff_cache
COVERAGE    = .coverage htmlcov coverage.xml
DIST_DIRS   = dist junit
TMP_DIRS   = site

.DEFAULT_GOAL := help

.PHONY: help
help: ## Show help messages for all available targets
	@grep -E '^[a-zA-Z_-]+:.*## .*$$' Makefile | \
	awk 'BEGIN {FS = ":.*## "}; {printf "\033[36m%-30s\033[0m %s\n", $$1, $$2}'

# Setup and Installation
.PHONY: setup
setup: install ## Create the environment and install dependencies (alias for install)

.PHONY: install
install: ## Sync Python dependencies from uv.lock
	$(UV) sync --locked $(UV_EXTRA)

.PHONY: models
models: ## Download the default local AI models
	$(UV) run --locked $(UV_EXTRA) pratevenn setup --model-dir "$(MODEL_DIR)"

.PHONY: download-full
download-full: ## Download all options including NB-Whisper verbatim medium and large
	$(UV) run --locked $(UV_EXTRA) pratevenn setup --full --model-dir "$(MODEL_DIR)"

.PHONY: download-semantic
download-semantic: ## Download optional semantic (normalized) speech recognition models
	$(UV) run --locked $(UV_EXTRA) pratevenn setup --semantic --model-dir "$(MODEL_DIR)"

.PHONY: run
run: ## Start Pratevenn with the local models
	$(UV) run --locked $(UV_EXTRA) pratevenn start --model-dir "$(MODEL_DIR)"

# Quality and Testing
.PHONY: test
test:
	$(UV) run --locked $(UV_EXTRA) pytest

.PHONY: format
format: ## Format code
	$(UV) run --locked $(UV_EXTRA) ruff format

.PHONY: setup-hooks
setup-hooks: ## Install Git hooks (pre-commit and pre-push)
	$(UV) run --locked $(UV_EXTRA) pre-commit install --hook-type pre-commit
	$(UV) run --locked $(UV_EXTRA) pre-commit install --hook-type pre-push
	$(UV) run --locked $(UV_EXTRA) pre-commit install-hooks

.PHONY: test-hooks
test-hooks: ## Test Git hooks on all files
	$(UV) run --locked $(UV_EXTRA) pre-commit run --all-files

# Documentation
.PHONY: docs
docs: ## Build documentation
	$(UV) run --locked $(UV_EXTRA) mkdocs build

# Build and Publish
.PHONY: build
build: ## Build distributions
	$(UV) build

.PHONY: publish
publish: build ## Build and publish to PyPI (needs UV_PUBLISH_TOKEN)
	$(UV) publish

# Containers
COMPOSE     ?= $(shell docker compose version >/dev/null 2>&1 && echo "docker compose" || echo "docker-compose")

.PHONY: docker-build-cpu
docker-build-cpu: ## Build the CPU Docker image
	docker build --target cpu -t pratevenn:cpu .

.PHONY: docker-build-cuda
docker-build-cuda: ## Build the CUDA Docker image
	docker build -f Dockerfile.cuda -t pratevenn:cuda .

.PHONY: docker-run-cpu
docker-run-cpu: ## Run Pratevenn CPU in Docker with local models
	docker run --rm -it -p 127.0.0.1:8000:8000 -v $(CURDIR)/$(MODEL_DIR):/models -v pratevenn-data:/data pratevenn:cpu

.PHONY: docker-run-cuda
docker-run-cuda: ## Run Pratevenn CUDA in Docker with local models
	docker run --rm -it --gpus all -p 127.0.0.1:8000:8000 -v $(CURDIR)/$(MODEL_DIR):/models -v pratevenn-data:/data pratevenn:cuda

.PHONY: compose-up
compose-up: ## Start Pratevenn CPU with Docker Compose
	$(COMPOSE) up -d

.PHONY: compose-up-cuda
compose-up-cuda: ## Start Pratevenn CUDA with Docker Compose
	$(COMPOSE) --profile cuda up -d pratevenn-cuda

.PHONY: compose-down
compose-down: ## Stop Docker Compose containers
	$(COMPOSE) --profile cuda down

# Maintenance
.PHONY: clean
clean: ## Remove caches and build artifacts
	find . -type f -name '*.pyc' -delete
	find . -type d -name '__pycache__' -exec rm -rf {} +
	rm -rf $(CACHE_DIRS) $(COVERAGE) $(DIST_DIRS) $(TMP_DIRS)
