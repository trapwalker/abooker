import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from abooker.core.database import cleanup_orphaned_pending, init_db, recover_from_json
from abooker.web.routes import files, publications, rss

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio
    from abooker.cache.manager import periodic_eviction, periodic_finalize

    log.info("Initialising database...")
    await init_db()
    log.info("Recovering publications from JSON...")
    await recover_from_json()
    await cleanup_orphaned_pending()

    background_tasks = [
        asyncio.create_task(periodic_eviction()),
        asyncio.create_task(periodic_finalize()),
    ]
    yield
    for task in background_tasks:
        task.cancel()
    log.info("Shutting down")


app = FastAPI(title="abooker", lifespan=lifespan)

app.include_router(publications.router)
app.include_router(rss.router)
app.include_router(files.router)


def run():
    from abooker.core.config import settings
    from abooker.core.log import setup_logging
    setup_logging()
    uvicorn.run("abooker.web.main:app", host=settings.WEB_HOST, port=settings.WEB_PORT, reload=False)


if __name__ == "__main__":
    run()
