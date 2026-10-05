from __future__ import annotations

import logging
import sys

import uvicorn

from .config import Settings
from .main import create_app
from .updates import RESTART_EXIT_CODE


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    settings = Settings()
    app = create_app(settings)
    config = uvicorn.Config(
        app,
        host=settings.host,
        port=settings.port,
        proxy_headers=True,
        forwarded_allow_ips="*",
        log_level="info",
    )
    server = uvicorn.Server(config)
    # Lets the updater stop the server cleanly before restarting.
    app.state.server = server
    server.run()
    if app.state.restart_requested:
        logging.getLogger("reelvault").info("exiting for restart after upgrade")
        sys.exit(RESTART_EXIT_CODE)


if __name__ == "__main__":
    main()
