"""Run ``pratevenn setup`` one time and then run ``pratevenn start``."""

import argparse
import os
from pathlib import Path

from pratevenn.models import download_models, model_directory


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pratevenn: a local Norwegian conversation practice tool"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("setup", help="Download local models")
    setup.add_argument("--full", action="store_true", help="Download all bundled options")
    setup.add_argument(
        "--semantic",
        action="store_true",
        help="Download optional semantic (normalized) speech recognition models",
    )
    start = commands.add_parser("start", help="Start the local web interface")
    for command in (setup, start):
        command.add_argument("--model-dir", type=Path, default=model_directory())
    start.add_argument(
        "--host",
        type=str,
        default=os.environ.get("PRATEVENN_HOST", "127.0.0.1"),
        help="Host address to bind the server (default: 127.0.0.1)",
    )
    start.add_argument("--port", type=int, default=8000)
    start.add_argument("--threads", type=int, default=8)
    start.add_argument(
        "--gpu-layers",
        type=int,
        default=-1,
        help="LLM layers to offload: -1 for all available, 0 for CPU, or a positive limit",
    )
    start.add_argument(
        "--context-size",
        type=int,
        default=os.environ.get("PRATEVENN_CONTEXT_SIZE", "8192"),
        help="LLM context window size in tokens (default: 8192)",
    )
    start.add_argument(
        "--allowed-hosts",
        type=str,
        default=os.environ.get("PRATEVENN_ALLOWED_HOSTS"),
        help=(
            "Comma-separated allowed Host headers, or * to allow all "
            "(default: localhost,127.0.0.1,[::1])"
        ),
    )
    args = parser.parse_args()
    if args.command == "setup":
        download_models(args.model_dir, full=args.full, semantic=args.semantic)
        return
    if (
        not 1 <= args.port <= 65535
        or args.threads < 1
        or args.gpu_layers < -1
        or not 1024 <= args.context_size <= 65536
    ):
        parser.error(
            "Use a valid port, a positive thread count, GPU layers of -1 or greater, "
            "and a context size between 1024 and 65536."
        )
    import uvicorn

    from pratevenn.app import MAX_MESSAGE_BYTES, create_app
    from pratevenn.models import Models

    print("Loading local models …", flush=True)
    try:
        models = Models(
            args.model_dir,
            args.threads,
            gpu_layers=args.gpu_layers,
            context_tokens=args.context_size,
        )
    except (OSError, ValueError, RuntimeError) as error:
        parser.error(
            f"Could not load models: {error}\nRun pratevenn setup --model-dir {args.model_dir}"
        )
    allowed_hosts = (
        [host.strip() for host in args.allowed_hosts.split(",") if host.strip()]
        if args.allowed_hosts
        else None
    )
    print(f"Conversation model running on {models.llm_device}.", flush=True)
    print(f"Open http://{args.host}:{args.port} and click Start conversation.", flush=True)
    uvicorn.run(
        create_app(models, allowed_hosts=allowed_hosts),
        host=args.host,
        port=args.port,
        ws_max_size=MAX_MESSAGE_BYTES,
    )


if __name__ == "__main__":
    main()
