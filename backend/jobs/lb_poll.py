"""``lb-poll`` Cloud Run Job: fold each connected ListenBrainz user's new listens into their profile.

Scheduled daily by Cloud Scheduler (same image as the API, like ``lb-refresh``):

    python -m backend.jobs.lb_poll

Runs ``ListenBrainzPoller.run(settings.lb_poll_budget_seconds)`` (1500 s, under the job's 1800 s
task timeout, less the time the catalog took) and logs its one INFO summary line. catalog.db must
load first so imported listens land on the same ``user_songs`` rows the API writes: a batch job
never matches through the BigQuery fallback into legacy row ids. If the catalog isn't ready within
``CATALOG_WAIT_S`` the run fails (ERROR, exit 1) before touching any profile.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time

from backend.config import BackendSettings, get_backend_settings
from backend.logging_config import configure_job_logging

logger = logging.getLogger("lb_poll")

# The 1800 s job timeout holds the catalog wait plus the poll budget (reduced by the wait).
CATALOG_WAIT_S = 240.0


class CatalogNotReadyError(RuntimeError):
    pass


async def _run(settings: BackendSettings) -> None:
    from backend.services.catalog_store import get_catalog_store
    from backend.services.firestore_service import FirestoreService
    from backend.services.import_ingest_service import ImportIngestService
    from backend.services.listenbrainz_poller import ListenBrainzPoller
    from backend.services.music_service_service import MusicServiceService
    from karaoke_decide.services.listenbrainz import ListenBrainzClient

    started = time.monotonic()
    store = get_catalog_store()
    await asyncio.to_thread(store.load, settings.model_copy(update={"catalog_load_timeout_s": CATALOG_WAIT_S}))
    if store.status != "ready":
        raise CatalogNotReadyError(f"catalog.db not loaded after {CATALOG_WAIT_S:.0f}s; not polling")
    budget = max(0, settings.lb_poll_budget_seconds - int(time.monotonic() - started))
    firestore = FirestoreService(settings)
    client = ListenBrainzClient(settings)
    try:
        music_service = MusicServiceService(settings, firestore, listenbrainz_client=client)
        poller = ListenBrainzPoller(settings, firestore, music_service, ImportIngestService(firestore), client)
        await poller.run(budget)
    finally:
        await client.close()


def main(argv: list[str] | None = None) -> int:
    configure_job_logging()
    try:
        asyncio.run(_run(get_backend_settings()))
    except Exception:
        logger.exception("ListenBrainz poll failed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
