"""Start real processes for end-to-end gateway tests: two demo upstreams, a slow one, and the gateway.

Nothing is mocked: the gateway talks MCP over HTTP to the demo servers, exactly as in the cluster.
"""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "services" / "gateway"))  # `from app... import` in unit tests
SHIPPING_TOKEN = "test-shipping-token"
ADMIN_KEY = "urm_test_bootstrap_admin_key_0123456789abcdef"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _start(app: str, app_dir: Path, port: int, env: dict[str, str], log: Path) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", app, "--app-dir", str(app_dir), "--port", str(port), "--log-level", "warning"],
        env={**os.environ, **env},
        stdout=log.open("w"),
        stderr=subprocess.STDOUT,
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
    tmp = tmp_path_factory.mktemp("stack")
    demo_dir = ROOT / "services" / "demo-mcp"
    ports = {name: _free_port() for name in ("store", "shipping", "slow", "dead", "llm", "gateway")}
    procs: list[subprocess.Popen] = []
    try:
        upstreams = {
            "store": ("app.server:app", {}),
            "shipping": ("app.shipping:app", {"REQUIRED_BEARER_TOKEN": SHIPPING_TOKEN}),
            "slow": ("app.server:app", {"DEMO_DELAY_SECONDS": "6"}),
        }
        for name, (app, env) in upstreams.items():
            log = tmp / f"{name}.log"
            proc = _start(app, demo_dir, ports[name], env, log)
            procs.append(proc)
            _wait_ready(f"http://127.0.0.1:{ports[name]}/health/ready", proc, log)

        log = tmp / "llm.log"
        proc = _start("fake_llm:app", Path(__file__).parent, ports["llm"], {}, log)
        procs.append(proc)
        _wait_ready(f"http://127.0.0.1:{ports['llm']}/health", proc, log)

        def url(name: str) -> str:
            return f"http://127.0.0.1:{ports[name]}/mcp"

        defaults = [
            {"id": "store-info", "name": "Store Info", "url": url("store")},
            {"id": "shipping-info", "name": "Shipping Info", "url": url("shipping"),
             "auth_header": "Authorization", "credential_env": "SHIPPING_INFO_CREDENTIAL"},
            {"id": "slow-info", "name": "Slow Info", "url": url("slow")},
            {"id": "dead-info", "name": "Dead Info", "url": url("dead")},  # nothing listens here
        ]
        gateway_env = {
            "DATABASE_URL": f"sqlite+aiosqlite:///{tmp / 'gateway.db'}",
            "API_KEY_PEPPER": "test-pepper",
            "UPSTREAM_ENCRYPTION_KEY": Fernet.generate_key().decode(),
            "BOOTSTRAP_ADMIN_EMAIL": "admin@example.test",
            "BOOTSTRAP_ADMIN_KEY": ADMIN_KEY,
            "DEFAULT_UPSTREAMS": json.dumps(defaults),
            "SHIPPING_INFO_CREDENTIAL": f"Bearer {SHIPPING_TOKEN}",
            "INTERNAL_UPSTREAM_HOSTS": "127.0.0.1",
            "UPSTREAM_DISCOVERY_TIMEOUT_SECONDS": "2",
            "UPSTREAM_CALL_TIMEOUT_SECONDS": "3",
            "UPSTREAM_TOOL_CACHE_SECONDS": "0",
            "UPSTREAM_FAILURE_CACHE_SECONDS": "0",
            # Chat uses the fake OpenAI-compatible model server (tests/fake_llm.py).
            "LLM_BASE_URL": f"http://127.0.0.1:{ports['llm']}/v1",
            "LLM_API_KEY": "test-llm-key",
            # The primary model is "out of quota", so every chat test also exercises the fallback.
            "LLM_MODEL": "exhausted-model",
            "LLM_FALLBACK_MODELS": "fake-model",
            # Hardening: a seeded admin-only tool, and low limits so the tests can hit them quickly.
            "DEFAULT_ADMIN_ONLY_TOOLS": json.dumps({"store-info": ["list_store_info_topics"]}),
            "RATE_LIMIT_TOOL_CALLS_PER_MINUTE": "25",
            "RATE_LIMIT_CHAT_MESSAGES_PER_MINUTE": "3",
            # Audit retention: prune rows older than a day, checking every second (tests insert an old row).
            "AUDIT_RETENTION_DAYS": "1",
            "MCP_GET_KEEPALIVE_SECONDS": "1",
            "AUDIT_PRUNE_INTERVAL_SECONDS": "1",
        }
        log = tmp / "gateway.log"
        proc = _start("app.main:app", ROOT / "services" / "gateway", ports["gateway"], gateway_env, log)
        procs.append(proc)
        _wait_ready(f"http://127.0.0.1:{ports['gateway']}/health/ready", proc, log)
        yield {
            "base": f"http://127.0.0.1:{ports['gateway']}",
            "mcp": f"http://127.0.0.1:{ports['gateway']}/mcp",
            "shipping_direct": url("shipping"),
            "admin_key": ADMIN_KEY,
            "gateway_log": log,
            "gateway_env": gateway_env,
            "db_path": tmp / "gateway.db",
            "tmp": tmp,
        }
    finally:
        for proc in procs:
            proc.terminate()
        for proc in procs:
            proc.wait(timeout=10)
