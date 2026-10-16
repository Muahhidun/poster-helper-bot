"""Optional furniture archive. No production database or network imports on startup."""

from .config import ReaderSettings


def register_reader(app, accounting_chat_ids):
    from .service import FurnitureReader
    from .routes import create_blueprint

    reader = FurnitureReader(ReaderSettings.from_env(), accounting_chat_ids, app.root_path)
    app.extensions['furniture_reader'] = reader
    app.register_blueprint(create_blueprint())
    return reader
