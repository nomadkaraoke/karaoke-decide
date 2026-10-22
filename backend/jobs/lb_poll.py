"""``lb-poll`` Cloud Run Job: fold each connected ListenBrainz user's new listens into their profile.

Scheduled daily by Cloud Scheduler (same image as the API, like ``lb-refresh``):

    python -m backend.jobs.lb_poll

Runs ``ListenBrainzPoller.run(settings.lb_poll_budget_seconds)`` (1500 s, under the job's 1800 s
task timeout) and logs its one INFO summary line. catalog.db is loaded first so imported listens
land on the same ``user_songs`` rows the API writes; if it can't load, matching falls back to
BigQuery and rows use the legacy ids. Exit code 1 only when the run itself fails.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from backend.config import BackendSettings, get_backend_settings
from backend.logging_config import configure_logging

logger = logging.getLogger("lb_poll")


async def _run(settings: BackendSettings) -> None:
    from backend.services.catalog_store import get_catalog_store
    from backend.services.firestore_service import FirestoreService
    from backend.services.import_ingest_service import ImportIngestService
    from backend.services.listenbrainz_poller import ListenBrainzPoller
    from backend.services.music_service_service import MusicServiceService
    from karaoke_decide.services.listenbrainz import ListenBrainzClient

    await asyncio.to_thread(get_catalog_store().load, settings)
    firestore = FirestoreService(settings)
    client = ListenBrainzClient(settings)
    try:
        music_service = MusicServiceService(settings, firestore, listenbrainz_client=client)
        poller = ListenBrainzPoller(settings, firestore, music_service, ImportIngestService(firestore), client)
        await poller.run(settings.lb_poll_budget_seconds)
    finally:
        await client.close()


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    try:
        asyncio.run(_run(get_backend_settings()))
    except Exception:
        logger.exception("ListenBrainz poll failed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
