"""Offline test entry point: clean env, temporary data, no dotenv, no network.

Run with .venv/bin/python scripts/run_furniture_tests.py [pytest paths/options].
Never invokes start_server or loads deployment credentials.
"""
import os
from pathlib import Path
import socket
import sys
import tempfile
import shutil


def main():
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    os.environ.clear()
    os.environ.update({
        'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1',
        'PYTHON_DOTENV_DISABLED': '1',
        'USE_WEBHOOK': 'false',
        'FLASK_SECRET_KEY': 'offline-test-key-not-a-deployment-credential',
    })

    def no_network(*args, **kwargs):
        raise AssertionError('External networking is forbidden in this test run')

    socket.create_connection = no_network
    socket.getaddrinfo = no_network
    socket.socket.connect = no_network
    socket.socket.connect_ex = no_network
    import dotenv
    dotenv.load_dotenv = lambda *args, **kwargs: False
    with tempfile.TemporaryDirectory(prefix='poster-furniture-tests-') as temp:
        os.environ['RAILWAY_VOLUME_MOUNT_PATH'] = temp
        import pytest
        args = sys.argv[1:] or ['tests/furniture_reader', '-q']
        # Legacy WhatsApp regression assumes a small ingredient/product catalog.
        # Copy ONLY explicit synthetic fixtures into this run's temp directory;
        # never read the user's existing CSVs or fetch a live Poster catalog.
        if '--synthetic-whatsapp-catalog' in args:
            args.remove('--synthetic-whatsapp-catalog')
            fixture_root = root / 'tests' / 'fixtures' / 'synthetic_whatsapp_catalog'
            for name in ('poster_ingredients.csv', 'poster_products.csv'):
                target = Path(temp) / name
                shutil.copyfile(fixture_root / name, target)
                target.chmod(0o600)
        return pytest.main(args)


if __name__ == '__main__':
    raise SystemExit(main())
