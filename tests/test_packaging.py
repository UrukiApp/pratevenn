"""Check backend selection from the locked dependencies without downloads."""

import subprocess
import tomllib
from pathlib import Path

import pytest
from packaging.markers import Marker


def test_cuda_compose_target_selects_only_cuda_service():
    result = subprocess.run(
        ["make", "--no-print-directory", "-n", "compose-up-cuda", "COMPOSE=docker compose",
         "CUDA=0"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "docker compose --profile cuda up -d pratevenn-cuda"


@pytest.mark.parametrize("extra", [None, "cpu", "cuda"])
def test_locked_backends_are_portable(extra, tmp_path):
    command = [
        "uv",
        "export",
        "--locked",
        "--offline",
        "--format",
        "pylock.toml",
        "--cache-dir",
        str(tmp_path),
        "--no-emit-project",
    ]
    if extra:
        command.extend(["--extra", extra])
    result = subprocess.run(
        command,
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=True,
    )
    packages = tomllib.loads(result.stdout)["packages"]
    for version in ("3.11", "3.12", "3.13", "3.14"):
        for platform, machine in (
                ("linux", "x86_64"),
                ("linux", "aarch64"),
                ("darwin", "arm64"),
                ("darwin", "x86_64"),
                ("win32", "AMD64"),
        ):
            environment = {
                "sys_platform": platform,
                "platform_machine": machine,
                "python_version": version,
                "python_full_version": f"{version}.0",
            }
            selected = [
                package
                for package in packages
                if Marker(package.get("marker", "python_version >= '3.11'")).evaluate(environment)
            ]
            backends = [package for package in selected if package["name"] == "llama-cpp-python"]
            cuda = extra == "cuda" and platform == "linux" and machine == "x86_64"
            assert len(backends) == 1
            if cuda:
                assert "index" not in backends[0]
                assert "/v0.3.35-cu125/" in backends[0]["archive"]["url"]
                assert backends[0]["archive"]["hashes"]["sha256"]
            else:
                assert backends[0]["index"] == "https://pypi.org/simple"
            nvidia = {
                package["name"] for package in selected if package["name"].startswith("nvidia-")
            }
            assert nvidia == ({"nvidia-cublas-cu12", "nvidia-cuda-runtime-cu12"} if cuda else set())
