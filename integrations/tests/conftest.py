"""Shared fixtures: the fake PodderSum API and MCP server launchers (stdio and HTTP)."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from collections.abc import Iterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fake_api import FakeApiServer
from mcp import Client
from mcp.client.stdio import StdioServerParameters

INTEGRATIONS = Path(__file__).resolve().parents[1]
REPO = INTEGRATIONS.parent
SKILLS = INTEGRATIONS / "skills"
CLI_SCRIPT = SKILLS / "poddersum-summarize" / "scripts" / "poddersum.py"
FIXTURES = Path(__file__).parent / "fixtures" / "api"
# Not a secret: proves the Bearer header is forwarded.
TEST_API_KEY = "test-key-not-a-secret"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def fixture_json(name: str):
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture(scope="session")
def fake_api() -> Iterator[str]:
    server = FakeApiServer()
    url = server.start()
    yield url
    server.stop()


@pytest.fixture(autouse=True)
def _reset_fake(request: pytest.FixtureRequest) -> None:
    if "fake_api" not in request.fixturenames:
        return
    url = request.getfixturevalue("fake_api")
    urllib.request.urlopen(urllib.request.Request(f"{url}/_fake/reset", method="POST"), timeout=5).read()


def fake_requests(url: str) -> list[dict]:
    with urllib.request.urlopen(f"{url}/_fake/requests", timeout=5) as resp:
        return json.loads(resp.read())


def server_env(api_url: str, api_key: str | None = None, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("PODDERSUM_")}
    env["PODDERSUM_API_URL"] = api_url
    if api_key:
        env["PODDERSUM_API_KEY"] = api_key
    env.update(extra)
    return env


@pytest.fixture(scope="session")
def http_mcp(fake_api: str) -> Iterator[str]:
    """The MCP server over Streamable HTTP (a subprocess), with an API key configured."""
    port = free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "poddersum_mcp", "--transport", "http", "--port", str(port)],
        env=server_env(fake_api, TEST_API_KEY),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                break
        except OSError:
            if proc.poll() is not None:
                raise RuntimeError(proc.stderr.read().decode() if proc.stderr else "server exited") from None
            time.sleep(0.1)
    else:
        proc.kill()
        raise RuntimeError("MCP HTTP server did not start")
    yield f"http://127.0.0.1:{port}/mcp"
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def set_fake(url: str, **config: bool) -> None:
    """Toggle fake API behaviour, e.g. ``llm_down=True`` or ``transcript_route=False``."""
    req = urllib.request.Request(
        f"{url}/_fake/config",
        data=json.dumps(config).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=5).read()


def stdio_params(api_url: str, api_key: str | None = None, **env: str) -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable, args=["-m", "poddersum_mcp"], env=server_env(api_url, api_key, **env)
    )


@pytest.fixture(params=["stdio", "http"])
def transport(request: pytest.FixtureRequest) -> str:
    return request.param


@pytest.fixture
def connect(transport: str, fake_api: str, request: pytest.FixtureRequest):
    @asynccontextmanager
    async def _connect(**kw):
        target = request.getfixturevalue("http_mcp") if transport == "http" else stdio_params(fake_api)
        async with Client(target, read_timeout_seconds=60, **kw) as client:
            yield client

    return _connect


def text_of(result) -> str:
    return "\n".join(c.text for c in result.content if getattr(c, "type", "") == "text")
