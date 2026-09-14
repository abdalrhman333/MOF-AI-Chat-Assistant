"""The HTTP layer. `app.py` holds the routes; this makes it runnable.

    .venv/Scripts/python -m Application.backend.api
"""
from __future__ import annotations


def main() -> None:
    import uvicorn

    from pipeline.config import get_settings

    config = get_settings().api
    # One process. Conversations live in Redis, so more would be safe —
    # but with API_CONVERSATION_STORE=memory a second worker would not see
    # the first's, and a demo gains nothing from the extra processes.
    uvicorn.run("Application.backend.api.app:app",
                host=config.host, port=config.port, workers=1)


if __name__ == "__main__":
    main()
