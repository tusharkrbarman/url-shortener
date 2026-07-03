from .config import Config
from .db import Database
from .http_api import ShortenerServer
from .logging_utils import configure_logging
from .service import LinkService
from .worker import ValidationWorker


def build_app(config: Config):
    db = Database(config.database_path)
    db.initialize()
    service = LinkService(db, config)
    return db, service


def main():
    config = Config.from_env()
    configure_logging(config.log_level)
    db, service = build_app(config)
    if config.service_mode == "worker":
        ValidationWorker(db, config).run_forever()
        return
    server = ShortenerServer(("0.0.0.0", config.port), db, config, service)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

