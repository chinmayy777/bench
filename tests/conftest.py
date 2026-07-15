"""Test env must be set BEFORE any preflight import (frozen Settings)."""
import os, sys, pathlib

os.environ.setdefault("ALLOW_LOCAL_TARGETS", "1")
os.environ.setdefault("PAYER_MODE", "mock")
os.environ.setdefault("PAYER_PRIVATE_KEY",
    "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d")
os.environ.setdefault("DB_PATH", "/tmp/preflight_test.db")
os.environ.setdefault("BASE_URL", "http://127.0.0.1:8000")

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import threading, time
import uvicorn
import pytest


class ServerThread:
    def __init__(self, app, port: int):
        cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
        self.server = uvicorn.Server(cfg)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.port = port

    def __enter__(self):
        self.thread.start()
        for _ in range(200):
            if self.server.started:
                return self
            time.sleep(0.05)
        raise RuntimeError("server failed to start")

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(timeout=5)


@pytest.fixture(scope="session")
def server_factory():
    return ServerThread
