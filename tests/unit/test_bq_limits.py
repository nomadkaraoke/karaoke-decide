"""Tests for BigQuery bytes-billed guards."""

from unittest.mock import MagicMock, patch

from karaoke_decide.services import bq_limits


def test_caps_are_ordered_sensibly() -> None:
    """Class caps sit below the client default except deliberate batch scans."""
    assert bq_limits.MAX_BYTES_RECOMMENDATION < bq_limits.MAX_BYTES_RECORDING_SEARCH
    assert bq_limits.MAX_BYTES_RECORDING_SEARCH < bq_limits.MAX_BYTES_DEFAULT
    assert bq_limits.MAX_BYTES_DEFAULT < bq_limits.MAX_BYTES_BATCH
    # Must stay above the pre-pruning estimates measured with --dry_run
    # (mb_recordings_enriched ~6.3 GiB, spotify_popularity_by_artist_title ~1.7 GiB).
    assert bq_limits.MAX_BYTES_RECORDING_SEARCH > 7 * bq_limits.GIB
    assert bq_limits.MAX_BYTES_RECOMMENDATION > 2 * bq_limits.GIB


def test_default_query_job_config_sets_cap() -> None:
    assert bq_limits.default_query_job_config().maximum_bytes_billed == bq_limits.MAX_BYTES_DEFAULT


@patch("karaoke_decide.services.bq_limits.bigquery.Client")
def test_make_client_applies_default_cap(mock_client_class: MagicMock) -> None:
    client = bq_limits.make_client("proj")

    assert client is mock_client_class.return_value
    kwargs = mock_client_class.call_args.kwargs
    assert kwargs["project"] == "proj"
    assert kwargs["default_query_job_config"].maximum_bytes_billed == bq_limits.MAX_BYTES_DEFAULT


def test_default_cap_merges_into_per_query_config() -> None:
    """A real Client merges the default cap into job configs that don't set one."""
    from google.auth.credentials import AnonymousCredentials
    from google.cloud import bigquery

    client = bigquery.Client(
        project="proj",
        credentials=AnonymousCredentials(),
        default_query_job_config=bq_limits.default_query_job_config(),
    )
    merged = bigquery.QueryJobConfig(use_query_cache=False)._fill_from_default(client.default_query_job_config)
    assert merged.maximum_bytes_billed == bq_limits.MAX_BYTES_DEFAULT
