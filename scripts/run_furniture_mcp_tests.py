"""Run MCP tests with a clean environment, temporary files and no real network."""
import os
from pathlib import Path
import socket
import sys
import tempfile


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    os.environ.clear()
    os.environ.update(PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTHON_DOTENV_DISABLED="1")

    def no_network(*args, **kwargs):
        raise AssertionError("External networking is forbidden in this test run")

    socket.create_connection = no_network
    socket.getaddrinfo = no_network
    socket.socket.connect = no_network
    socket.socket.connect_ex = no_network
    with tempfile.TemporaryDirectory(prefix="furniture-mcp-tests-") as temp:
        os.environ["TMPDIR"] = temp
        import pytest
        return pytest.main(sys.argv[1:] or ["tests/furniture_mcp", "-q"])


if __name__ == "__main__":
    raise SystemExit(main())
