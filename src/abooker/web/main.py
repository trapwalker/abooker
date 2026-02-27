import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from abooker.core.database import init_db, recover_from_json
from abooker.web.routes import files, publications, rss

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio
    from abooker.cache.manager import periodic_eviction

    log.info("Initialising database...")
    await init_db()
    log.info("Recovering publications from JSON...")
    await recover_from_json()

    eviction_task = asyncio.create_task(periodic_eviction())
    yield
    eviction_task.cancel()
    log.info("Shutting down")


app = FastAPI(title="abooker", lifespan=lifespan)

app.include_router(publications.router)
app.include_router(rss.router)
app.include_router(files.router)


def run():
    from abooker.core.log import setup_logging
    setup_logging()
    uvicorn.run("abooker.web.main:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    run()
