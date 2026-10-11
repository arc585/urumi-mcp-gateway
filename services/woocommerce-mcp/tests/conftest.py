"""Start the fake WooCommerce API and our MCP server as real processes (nothing mocked in-process)."""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from fake_woocommerce import CONSUMER_KEY, CONSUMER_SECRET

HERE = Path(__file__).parent
SERVICE = HERE.parent
PUBLIC_URL = "http://store.urumi.test:8880"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _start(app: str, app_dir: Path, port: int, env: dict[str, str], log: Path) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", app, "--app-dir", str(app_dir), "--port", str(port), "--log-level", "warning"],
        env={**os.environ, **env}, stdout=log.open("w"), stderr=subprocess.STDOUT,
    )


def _wait_ready(url: str, proc: subprocess.Popen, log: Path) -> None:
    for _ in range(100):
        if proc.poll() is not None:
            raise RuntimeError(f"process exited early:\n{log.read_text()}")
        try:
            if httpx.get(url, timeout=1).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise RuntimeError(f"{url} never became ready:\n{log.read_text()}")


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session")
def stack(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("wc")
    ports = {"wc": _free_port(), "mcp": _free_port()}
    procs: list[subprocess.Popen] = []
    try:
        log = tmp / "fake-wc.log"
        procs.append(_start("fake_woocommerce:app", HERE, ports["wc"], {}, log))
        _wait_ready(f"http://127.0.0.1:{ports['wc']}/_health", procs[-1], log)

        log = tmp / "mcp.log"
        env = {
            "WOOCOMMERCE_URL": f"http://127.0.0.1:{ports['wc']}",
            "WOOCOMMERCE_CONSUMER_KEY": CONSUMER_KEY,
            "WOOCOMMERCE_CONSUMER_SECRET": CONSUMER_SECRET,
            "WOOCOMMERCE_TIMEOUT_SECONDS": "1",
            "STORE_PUBLIC_URL": PUBLIC_URL,
            "STORE_TIMEZONE": "Asia/Kolkata",
            "MCP_ALLOWED_HOSTS": "127.0.0.1:*,localhost:*",
        }
        procs.append(_start("app.server:app", SERVICE, ports["mcp"], env, log))
        _wait_ready(f"http://127.0.0.1:{ports['mcp']}/health/ready", procs[-1], log)
        yield {"mcp": f"http://127.0.0.1:{ports['mcp']}/mcp", "base": f"http://127.0.0.1:{ports['mcp']}",
               "wc": f"http://127.0.0.1:{ports['wc']}", "public": PUBLIC_URL}
    finally:
        for proc in procs:
            proc.terminate()
        for proc in procs:
            proc.wait(timeout=10)
