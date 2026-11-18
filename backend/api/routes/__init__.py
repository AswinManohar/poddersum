"""HTTP routes. ``ROUTERS`` is registered by ``api.main.create_app``."""

from api.routes import artifacts, ask, chat, episodes, ingest, jobs, podcasts, search

ROUTERS = (
    search.router,
    podcasts.router,
    episodes.router,
    ingest.router,
    jobs.router,
    ask.router,
    artifacts.router,
    chat.router,
)
