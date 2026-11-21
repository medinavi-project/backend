import shutil
import socket
import subprocess
import time

import pytest
from fastapi.testclient import TestClient

from app.ai.providers.mock_provider import MockAIProvider
from app.core.config import Settings
from app.main import create_app


@pytest.fixture(scope="session")
def redis_url(tmp_path_factory):
    executable = shutil.which("redis-server")
    if not executable:
        pytest.fail("redis-server must be installed for Redis integration tests")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    directory = tmp_path_factory.mktemp("redis")
    process = subprocess.Popen(
        [
            executable,
            "--bind",
            "127.0.0.1",
            "--port",
            str(port),
            "--save",
            "",
            "--appendonly",
            "no",
            "--dir",
            str(directory),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(100):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    break
            except OSError:
                time.sleep(0.03)
        else:
            pytest.fail("test Redis did not start")
        yield f"redis://127.0.0.1:{port}/0"
    finally:
        process.terminate()
        process.wait(timeout=5)


@pytest.fixture
def settings(redis_url):
    return Settings(
        _env_file=None,
        APP_ENV="test",
        MEDICATION_AI_ENABLED=False,
        AI_PROVIDER="mock",
        AI_MOCK_ENABLED=True,
        REDIS_URL=redis_url,
    )


@pytest.fixture
def client(settings):
    with TestClient(
        create_app(settings, MockAIProvider()), raise_server_exceptions=False
    ) as client:
        yield client
