"""
Pulumi infrastructure for Nomad Karaoke Decide.

Resources managed:
- BigQuery dataset and tables (karaoke catalog data)
- GCS bucket (data staging)
- Artifact Registry repository (container images)
- Cloud Run service (backend API)
- MusicBrainz weekly refresh (Cloud Run Job, Scheduler, staging dataset, bucket)
- ListenBrainz refresh (Cloud Run Job, Scheduler, staging dataset)
- Song search (karaoke-decide-search Cloud Run service, song-index-build daily Job,
  public offline-index bucket, uptime ping)
- IAM bindings (service account permissions)
- Cloudflare Worker (API proxy)
"""

from pathlib import Path

import pulumi
import pulumi_cloudflare as cloudflare
import pulumi_gcp as gcp

# Configuration
config = pulumi.Config()
gcp_config = pulumi.Config("gcp")
project = gcp_config.require("project")
region = gcp_config.require("region")
environment = config.get("environment") or "production"

# Project number (needed for service account references)
PROJECT_NUMBER = "718638054799"

# =============================================================================
# BigQuery
# =============================================================================

# Dataset for karaoke catalog data
bigquery_dataset = gcp.bigquery.Dataset(
    "karaoke-decide-dataset",
    dataset_id="karaoke_decide",
    project=project,
    description="Karaoke song catalog and metadata",
    max_time_travel_hours="168",
    accesses=[
        {"role": "OWNER", "user_by_email": "admin@nomadkaraoke.com"},
        {"role": "OWNER", "special_group": "projectOwners"},
        {"role": "READER", "special_group": "projectReaders"},
        {"role": "WRITER", "special_group": "projectWriters"},
    ],
    opts=pulumi.ResourceOptions(protect=True),
)

# KaraokeNerds catalog table
karaokenerds_table = gcp.bigquery.Table(
    "karaokenerds-raw-table",
    dataset_id=bigquery_dataset.dataset_id,
    table_id="karaokenerds_raw",
    project=project,
    schema='[{"mode":"NULLABLE","name":"Title","type":"STRING"},{"mode":"NULLABLE","name":"Artist","type":"STRING"},{"mode":"NULLABLE","name":"Brands","type":"STRING"},{"mode":"NULLABLE","name":"Id","type":"INTEGER"}]',
    opts=pulumi.ResourceOptions(protect=True),
)

# Spotify tracks table
spotify_tracks_table = gcp.bigquery.Table(
    "spotify-tracks-table",
    dataset_id=bigquery_dataset.dataset_id,
    table_id="spotify_tracks",
    project=project,
    schema='[{"mode":"NULLABLE","name":"spotify_id","type":"STRING"},{"mode":"NULLABLE","name":"title","type":"STRING"},{"mode":"NULLABLE","name":"isrc","type":"STRING"},{"mode":"NULLABLE","name":"popularity","type":"INTEGER"},{"mode":"NULLABLE","name":"duration_ms","type":"INTEGER"},{"mode":"NULLABLE","name":"explicit","type":"BOOLEAN"},{"mode":"NULLABLE","name":"artist_name","type":"STRING"},{"mode":"NULLABLE","name":"artist_spotify_id","type":"STRING"},{"mode":"NULLABLE","name":"artist_popularity","type":"INTEGER"},{"mode":"NULLABLE","name":"artist_followers","type":"INTEGER"}]',
    opts=pulumi.ResourceOptions(protect=True),
)

# =============================================================================
# Cloud Storage
# =============================================================================

# Data staging bucket
data_bucket = gcp.storage.Bucket(
    "data-bucket",
    name="nomadkaraoke-data",
    project=project,
    location="US-CENTRAL1",
    uniform_bucket_level_access=True,
    public_access_prevention="inherited",
    hierarchical_namespace={"enabled": False},
    # Leftover ETL staging data; archive anything not touched in 30 days.
    lifecycle_rules=[
        {
            "action": {"type": "SetStorageClass", "storage_class": "ARCHIVE"},
            "condition": {
                "age": 30,
                "matches_storage_classes": ["STANDARD", "NEARLINE", "COLDLINE", "REGIONAL"],
            },
        }
    ],
    opts=pulumi.ResourceOptions(protect=True),
)

# =============================================================================
# Artifact Registry
# =============================================================================

# Container image repository
artifact_repo = gcp.artifactregistry.Repository(
    "karaoke-repo",
    repository_id="karaoke-repo",
    project=project,
    location=region,
    format="DOCKER",
    description="Docker repository for karaoke backend images",
)

# =============================================================================
# IAM
# =============================================================================

# BigQuery User role for default compute service account
bigquery_user_binding = gcp.projects.IAMMember(
    "compute-sa-bigquery-user",
    project=project,
    role="roles/bigquery.user",
    member=f"serviceAccount:{PROJECT_NUMBER}-compute@developer.gserviceaccount.com",
    opts=pulumi.ResourceOptions(protect=True),
)

# BigQuery Data Viewer role for default compute service account
bigquery_viewer_binding = gcp.projects.IAMMember(
    "compute-sa-bigquery-viewer",
    project=project,
    role="roles/bigquery.dataViewer",
    member=f"serviceAccount:{PROJECT_NUMBER}-compute@developer.gserviceaccount.com",
    opts=pulumi.ResourceOptions(protect=True),
)

# =============================================================================
# Cloud Tasks
# =============================================================================

# Queue for background music sync jobs
sync_tasks_queue = gcp.cloudtasks.Queue(
    "music-sync-queue",
    name="music-sync-queue",
    project=project,
    location=region,
    rate_limits={
        "max_dispatches_per_second": 10,
        "max_concurrent_dispatches": 5,
    },
    retry_config={
        "max_attempts": 3,
        "min_backoff": "10s",
        "max_backoff": "300s",
        "max_doublings": 3,
    },
    stackdriver_logging_config={
        "sampling_ratio": 1.0,
    },
)

# Allow Cloud Run service account to enqueue tasks
cloud_tasks_enqueuer = gcp.projects.IAMMember(
    "compute-sa-tasks-enqueuer",
    project=project,
    role="roles/cloudtasks.enqueuer",
    member=f"serviceAccount:{PROJECT_NUMBER}-compute@developer.gserviceaccount.com",
)

# Allow Cloud Run service account to view queue metadata (for deep health checks)
cloud_tasks_viewer = gcp.projects.IAMMember(
    "compute-sa-tasks-viewer",
    project=project,
    role="roles/cloudtasks.viewer",
    member=f"serviceAccount:{PROJECT_NUMBER}-compute@developer.gserviceaccount.com",
)

# Allow Cloud Tasks to invoke Cloud Run (for OIDC authentication)
cloud_tasks_invoker = gcp.projects.IAMMember(
    "compute-sa-run-invoker",
    project=project,
    role="roles/run.invoker",
    member=f"serviceAccount:{PROJECT_NUMBER}-compute@developer.gserviceaccount.com",
)

# Allow service account to act as itself (required for Cloud Tasks OIDC)
# This grants iam.serviceAccounts.actAs permission
service_account_user = gcp.serviceaccount.IAMMember(
    "compute-sa-act-as-self",
    service_account_id=f"projects/{project}/serviceAccounts/{PROJECT_NUMBER}-compute@developer.gserviceaccount.com",
    role="roles/iam.serviceAccountUser",
    member=f"serviceAccount:{PROJECT_NUMBER}-compute@developer.gserviceaccount.com",
)

# =============================================================================
# Firestore Indexes
# =============================================================================

# Composite index for sync_jobs filtering by status and created_at
# Required by: GET /api/admin/stats (filtering sync jobs by status within time window)
sync_jobs_status_index = gcp.firestore.Index(
    "sync-jobs-status-created-index",
    project=project,
    database="(default)",
    collection="sync_jobs",
    fields=[
        {"field_path": "status", "order": "ASCENDING"},
        {"field_path": "created_at", "order": "ASCENDING"},
    ],
    opts=pulumi.ResourceOptions(protect=True),
)

# Composite index for decide_users filtering by is_guest and ordering by created_at
# Required by: GET /api/admin/users (filtering verified/guest users with pagination)
decide_users_is_guest_index = gcp.firestore.Index(
    "decide-users-is-guest-created-index",
    project=project,
    database="(default)",
    collection="decide_users",
    fields=[
        {"field_path": "is_guest", "order": "ASCENDING"},
        {"field_path": "created_at", "order": "DESCENDING"},
    ],
    opts=pulumi.ResourceOptions(protect=True),
)

# Composite index for decide_users filtering by user_id
# Required by: GET /api/admin/users (listing all users with pagination)
# Note: decide_users is karaoke-decide's dedicated collection (separate from karaoke-gen's gen_users)
# Index #1: user_id ASC, created_at DESC - for user lookups with ordering
decide_users_user_id_index = gcp.firestore.Index(
    "decide-users-user-id-created-index",
    project=project,
    database="(default)",
    collection="decide_users",
    fields=[
        {"field_path": "user_id", "order": "ASCENDING"},
        {"field_path": "created_at", "order": "DESCENDING"},
    ],
    opts=pulumi.ResourceOptions(protect=True),
)

# Index #2: created_at DESC, user_id DESC - for pagination ordering
decide_users_created_user_id_index = gcp.firestore.Index(
    "decide-users-created-user-id-index",
    project=project,
    database="(default)",
    collection="decide_users",
    fields=[
        {"field_path": "created_at", "order": "DESCENDING"},
        {"field_path": "user_id", "order": "DESCENDING"},
    ],
    opts=pulumi.ResourceOptions(protect=True),
)


# NOTE: Composite index for sync_jobs (user_id ASC, created_at DESC) already exists
# It was created manually/automatically and is required by GET /api/services/sync/status
# Not managed by Pulumi to avoid conflicts with existing index

# =============================================================================
# Cloud Run
# =============================================================================

# kjbox -> decide karaoke-log partner secret (X-Kjbox-Secret on /api/partner/kjbox/*).
# Pulumi owns the secret only; its value (a version) is added by hand with /gcp-admin and
# must exist before the API deploys, because the service mounts it as DECIDE_KJBOX_SECRET.
# Until the value is set on the API, the partner routes answer 503.
decide_kjbox_secret = gcp.secretmanager.Secret(
    "decide-kjbox-secret",
    secret_id="decide-kjbox-secret",
    project=project,
    replication={"auto": {}},
    opts=pulumi.ResourceOptions(protect=True),
)
# Its accessor grant is created here, not in the REQUIRED_SECRETS loop below, so the service
# can depend on it.
decide_kjbox_secret_access = gcp.secretmanager.SecretIamMember(
    "cloud-run-secret-access-decide-kjbox-secret",
    project=project,
    secret_id="decide-kjbox-secret",
    role="roles/secretmanager.secretAccessor",
    member=f"serviceAccount:{PROJECT_NUMBER}-compute@developer.gserviceaccount.com",
    opts=pulumi.ResourceOptions(depends_on=[decide_kjbox_secret]),
)

# Backend API service
cloud_run_service = gcp.cloudrunv2.Service(
    "karaoke-decide-api",
    name="karaoke-decide",
    project=project,
    location=region,
    ingress="INGRESS_TRAFFIC_ALL",
    launch_stage="GA",
    template={
        "containers": [
            {
                "image": f"{region}-docker.pkg.dev/{project}/karaoke-repo/karaoke-decide:latest",
                "ports": {
                    "container_port": 8000,
                    "name": "http1",
                },
                "envs": [
                    # Plain environment variables
                    {"name": "ENVIRONMENT", "value": environment},
                    {"name": "GOOGLE_CLOUD_PROJECT", "value": project},
                    {"name": "GOOGLE_CLOUD_PROJECT_NUMBER", "value": PROJECT_NUMBER},
                    {"name": "CLOUD_RUN_URL", "value": f"https://karaoke-decide-{PROJECT_NUMBER}.{region}.run.app"},
                    {"name": "FRONTEND_URL", "value": "https://decide.nomadkaraoke.com"},
                    {
                        "name": "SPOTIFY_REDIRECT_URI",
                        "value": f"https://karaoke-decide-{PROJECT_NUMBER}.{region}.run.app/api/services/spotify/callback",
                    },
                    # Phase 3b: ListenBrainz connect through MetaBrainz OAuth (live; the capability still
                    # hides itself unless the MetaBrainz secrets and DECIDE_TOKEN_KEY are all present).
                    {"name": "LISTENBRAINZ_OAUTH_ENABLED", "value": "true"},
                    {"name": "PUBLIC_API_BASE_URL", "value": "https://decide.nomadkaraoke.com"},
                    # Secrets from Secret Manager
                    {
                        "name": "JWT_SECRET",
                        "value_source": {
                            "secret_key_ref": {
                                "secret": "karaoke-decide-jwt-secret",
                                "version": "latest",
                            }
                        },
                    },
                    {
                        "name": "SPOTIFY_CLIENT_ID",
                        "value_source": {
                            "secret_key_ref": {
                                "secret": "spotipy-client-id",
                                "version": "latest",
                            }
                        },
                    },
                    {
                        "name": "SPOTIFY_CLIENT_SECRET",
                        "value_source": {
                            "secret_key_ref": {
                                "secret": "spotipy-client-secret",
                                "version": "latest",
                            }
                        },
                    },
                    {
                        "name": "LASTFM_API_KEY",
                        "value_source": {
                            "secret_key_ref": {
                                "secret": "lastfm-api-key",
                                "version": "latest",
                            }
                        },
                    },
                    {
                        "name": "POSTMARK_SERVER_TOKEN",
                        "value_source": {
                            "secret_key_ref": {
                                "secret": "postmark-server-token",
                                "version": "latest",
                            }
                        },
                    },
                    {
                        "name": "DECIDE_KJBOX_SECRET",
                        "value_source": {
                            "secret_key_ref": {
                                "secret": "decide-kjbox-secret",
                                "version": "latest",
                            }
                        },
                    },
                    {
                        "name": "APPLE_MUSICKIT_PRIVATE_KEY",
                        "value_source": {
                            "secret_key_ref": {
                                "secret": "apple-musickit-private-key",
                                "version": "latest",
                            }
                        },
                    },
                    {
                        "name": "APPLE_MUSICKIT_KEY_ID",
                        "value_source": {
                            "secret_key_ref": {
                                "secret": "apple-musickit-key-id",
                                "version": "latest",
                            }
                        },
                    },
                    {
                        "name": "APPLE_TEAM_ID",
                        "value_source": {
                            "secret_key_ref": {
                                "secret": "apple-team-id",
                                "version": "latest",
                            }
                        },
                    },
                    # Phase 3b secrets (created by hand; grants in REQUIRED_SECRETS).
                    {
                        "name": "GEMINI_API_KEY",
                        "value_source": {
                            "secret_key_ref": {
                                "secret": "gemini-api-key",
                                "version": "latest",
                            }
                        },
                    },
                    {
                        "name": "METABRAINZ_CLIENT_ID",
                        "value_source": {
                            "secret_key_ref": {
                                "secret": "metabrainz-client-id",
                                "version": "latest",
                            }
                        },
                    },
                    {
                        "name": "METABRAINZ_CLIENT_SECRET",
                        "value_source": {
                            "secret_key_ref": {
                                "secret": "metabrainz-client-secret",
                                "version": "latest",
                            }
                        },
                    },
                    {
                        "name": "DECIDE_TOKEN_KEY",
                        "value_source": {
                            "secret_key_ref": {
                                "secret": "decide-token-key",
                                "version": "latest",
                            }
                        },
                    },
                ],
                "resources": {
                    "limits": {
                        "cpu": "1",
                        # 2Gi: availability.db (~213 MB) + catalog.db (<=150 MB) are decompressed into
                        # /tmp, which is in-memory on Cloud Run, on top of the app itself. Request-billed.
                        # Must match CI --memory (tests/unit/test_infra_ci_parity.py).
                        "memory": "2Gi",
                    },
                    "cpu_idle": True,
                    "startup_cpu_boost": True,
                },
                # /api/ready answers once the lifespan (catalog + availability load) has finished;
                # until then uvicorn isn't listening and each probe is refused at once. Probe every
                # 1 s (Cloud Run's minimum) so a ready instance takes traffic within a second rather
                # than up to 10 s later; 240 x 1 s keeps the 240 s window. timeout_seconds can't
                # exceed period_seconds. Keep in step with CI --startup-probe
                # (tests/unit/test_infra_ci_parity.py).
                "startup_probe": {
                    "http_get": {"path": "/api/ready", "port": 8000},
                    "initial_delay_seconds": 0,
                    "timeout_seconds": 1,
                    "period_seconds": 1,
                    "failure_threshold": 240,
                },
            }
        ],
        "scaling": {
            "max_instance_count": 10,
        },
        "max_instance_request_concurrency": 80,
        "timeout": "1800s",  # 30 minutes for large Last.fm sync operations
        "service_account": f"{PROJECT_NUMBER}-compute@developer.gserviceaccount.com",
    },
    traffics=[
        {
            "percent": 100,
            "type": "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST",
        }
    ],
    scaling={
        "min_instance_count": 0,  # Scale to zero (~$10/mo saved); must match CI --min-instances 0
    },
    # DECIDE_KJBOX_SECRET is mounted: the secret and its grant must exist first. Its version
    # (the value) is added by hand before the deploy (see decide_kjbox_secret above).
    opts=pulumi.ResourceOptions(protect=True, depends_on=[decide_kjbox_secret, decide_kjbox_secret_access]),
)

# Allow unauthenticated access to Cloud Run service
cloud_run_invoker = gcp.cloudrunv2.ServiceIamMember(
    "karaoke-decide-invoker",
    name=cloud_run_service.name.apply(lambda name: f"projects/{project}/locations/{region}/services/{name}"),
    project=project,
    location=region,
    role="roles/run.invoker",
    member="allUsers",
    opts=pulumi.ResourceOptions(protect=True),
)

# =============================================================================
# Secret Manager Access
# =============================================================================

# Secrets that Cloud Run needs access to
REQUIRED_SECRETS = [
    "karaoke-decide-jwt-secret",
    "spotipy-client-id",
    "spotipy-client-secret",
    "lastfm-api-key",
    "postmark-server-token",
    # Apple MusicKit (Apple Music connect), created by hand 2026-10-22 from the Apple Developer account.
    "apple-musickit-private-key",
    "apple-musickit-key-id",
    "apple-team-id",
    # Phase 3b, created by hand (no Secret resources here): the AI Studio key for screenshot
    # import, the MetaBrainz OAuth app (ListenBrainz connect), and the Fernet key that encrypts
    # stored service tokens.
    "gemini-api-key",
    "metabrainz-client-id",
    "metabrainz-client-secret",
    "decide-token-key",
    # decide-kjbox-secret's grant is decide_kjbox_secret_access (above the service).
]

# Grant Cloud Run service account access to secrets
for secret_name in REQUIRED_SECRETS:
    gcp.secretmanager.SecretIamMember(
        f"cloud-run-secret-access-{secret_name}",
        project=project,
        secret_id=secret_name,
        role="roles/secretmanager.secretAccessor",
        member=f"serviceAccount:{PROJECT_NUMBER}-compute@developer.gserviceaccount.com",
    )

# =============================================================================
# MusicBrainz weekly refresh (Cloud Run Job + Cloud Scheduler)
# =============================================================================
# Loads the latest MusicBrainz full dump into BigQuery and rebuilds the mb_*
# tables + karaoke_recording_links. See karaoke_decide/etl/musicbrainz_refresh.py.

MB_REFRESH_JOB_NAME = "mb-refresh"

# Bucket was created by hand for the original one-off ETL; adopted here.
musicbrainz_bucket = gcp.storage.Bucket(
    "musicbrainz-data-bucket",
    name="nomadkaraoke-musicbrainz-data",
    project=project,
    location="US-CENTRAL1",
    uniform_bucket_level_access=True,
    public_access_prevention="inherited",
    # Staging TSVs are deleted after every successful run; this catches failed runs.
    lifecycle_rules=[
        {
            "action": {"type": "Delete"},
            "condition": {"age": 3, "matches_prefixes": ["staging/"]},
        }
    ],
    # Everything here is re-derivable from the public dump; don't pay 7 days of
    # soft-delete retention on ~7 GB of staging data every week.
    soft_delete_policy={"retention_duration_seconds": 0},
    opts=pulumi.ResourceOptions(protect=True),
)

musicbrainz_staging_dataset = gcp.bigquery.Dataset(
    "musicbrainz-staging-dataset",
    dataset_id="musicbrainz_staging",
    project=project,
    location="US",
    description="Scratch space for the weekly MusicBrainz refresh (raw dump tables + candidate builds)",
    opts=pulumi.ResourceOptions(protect=True),
)

# Full MusicBrainz mirror: every table in mbdump + mbdump-derived, with real
# column names and types, replaced weekly by mb-refresh (musicbrainz_mirror.py).
musicbrainz_dataset = gcp.bigquery.Dataset(
    "musicbrainz-dataset",
    dataset_id="musicbrainz",
    project=project,
    location="US",
    description="Full MusicBrainz mirror (core + derived dump tables, typed), refreshed weekly by mb-refresh",
    opts=pulumi.ResourceOptions(protect=True),
)

mb_refresh_sa = gcp.serviceaccount.Account(
    "mb-refresh-sa",
    account_id="mb-refresh",
    display_name="MusicBrainz Refresh Job",
    description="Runs the weekly mb-refresh Cloud Run Job",
    project=project,
)
mb_refresh_member = mb_refresh_sa.email.apply(lambda email: f"serviceAccount:{email}")

gcp.projects.IAMMember(
    "mb-refresh-bq-job-user",
    project=project,
    role="roles/bigquery.jobUser",
    member=mb_refresh_member,
)
for _name, _dataset in [
    ("prod", bigquery_dataset),
    ("staging", musicbrainz_staging_dataset),
    ("mirror", musicbrainz_dataset),
]:
    gcp.bigquery.DatasetIamMember(
        f"mb-refresh-bq-editor-{_name}",
        project=project,
        dataset_id=_dataset.dataset_id,
        role="roles/bigquery.dataEditor",
        member=mb_refresh_member,
    )
gcp.storage.BucketIAMMember(
    "mb-refresh-bucket-admin",
    bucket=musicbrainz_bucket.name,
    role="roles/storage.objectAdmin",
    member=mb_refresh_member,
)

mb_refresh_job = gcp.cloudrunv2.Job(
    "mb-refresh-job",
    name=MB_REFRESH_JOB_NAME,
    project=project,
    location=region,
    deletion_protection=False,
    template={
        "template": {
            "containers": [
                {
                    # CI pins this to the deployed commit SHA (gcloud run jobs update --image).
                    "image": f"{region}-docker.pkg.dev/{project}/karaoke-repo/karaoke-decide:latest",
                    "commands": ["python", "-m", "karaoke_decide.etl.musicbrainz_refresh"],
                    "args": ["run"],
                    "resources": {"limits": {"cpu": "4", "memory": "4Gi"}},
                }
            ],
            "service_account": mb_refresh_sa.email,
            "timeout": "10800s",
            # A failed run logs an ERROR (error monitor alerts); next week's run retries.
            "max_retries": 0,
        },
    },
    opts=pulumi.ResourceOptions(ignore_changes=["template.template.containers[0].image"]),
)

mb_refresh_invoker = gcp.cloudrunv2.JobIamMember(
    "mb-refresh-scheduler-invoker",
    project=project,
    location=region,
    name=mb_refresh_job.name,
    role="roles/run.invoker",
    member=mb_refresh_member,
)

# MusicBrainz publishes full dumps Wed + Sat (~00:20 UTC, files complete by ~05:00).
gcp.cloudscheduler.Job(
    "mb-refresh-scheduler",
    name="mb-refresh-weekly",
    description="Weekly MusicBrainz dump -> BigQuery refresh",
    project=project,
    region=region,
    schedule="0 10 * * 0",
    time_zone="UTC",
    attempt_deadline="60s",
    http_target={
        "uri": mb_refresh_job.name.apply(
            lambda name: f"https://run.googleapis.com/v2/projects/{project}/locations/{region}/jobs/{name}:run"
        ),
        "http_method": "POST",
        "oauth_token": {
            "service_account_email": mb_refresh_sa.email,
            "scope": "https://www.googleapis.com/auth/cloud-platform",
        },
    },
    opts=pulumi.ResourceOptions(depends_on=[mb_refresh_invoker]),
)

# =============================================================================
# ListenBrainz refresh (Cloud Run Job + Cloud Scheduler)
# =============================================================================
# Loads the latest ListenBrainz statistics dump (artist/recording listen stats)
# into lb_* popularity tables. Shares the MusicBrainz bucket for GCS staging
# (staging/listenbrainz/, same 3-day lifecycle rule).
# See karaoke_decide/etl/listenbrainz_refresh.py.

LB_REFRESH_JOB_NAME = "lb-refresh"

listenbrainz_staging_dataset = gcp.bigquery.Dataset(
    "listenbrainz-staging-dataset",
    dataset_id="listenbrainz_staging",
    project=project,
    location="US",
    description="Scratch space for the ListenBrainz refresh (raw statistics + candidate builds)",
    opts=pulumi.ResourceOptions(protect=True),
)

lb_refresh_sa = gcp.serviceaccount.Account(
    "lb-refresh-sa",
    account_id="lb-refresh",
    display_name="ListenBrainz Refresh Job",
    description="Runs the lb-refresh Cloud Run Job",
    project=project,
)
lb_refresh_member = lb_refresh_sa.email.apply(lambda email: f"serviceAccount:{email}")

gcp.projects.IAMMember(
    "lb-refresh-bq-job-user",
    project=project,
    role="roles/bigquery.jobUser",
    member=lb_refresh_member,
)
for _name, _dataset in [("prod", bigquery_dataset), ("staging", listenbrainz_staging_dataset)]:
    gcp.bigquery.DatasetIamMember(
        f"lb-refresh-bq-editor-{_name}",
        project=project,
        dataset_id=_dataset.dataset_id,
        role="roles/bigquery.dataEditor",
        member=lb_refresh_member,
    )
gcp.storage.BucketIAMMember(
    "lb-refresh-bucket-admin",
    bucket=musicbrainz_bucket.name,
    role="roles/storage.objectAdmin",
    member=lb_refresh_member,
)

lb_refresh_job = gcp.cloudrunv2.Job(
    "lb-refresh-job",
    name=LB_REFRESH_JOB_NAME,
    project=project,
    location=region,
    deletion_protection=False,
    template={
        "template": {
            "containers": [
                {
                    # CI pins this to the deployed commit SHA (gcloud run jobs update --image).
                    "image": f"{region}-docker.pkg.dev/{project}/karaoke-repo/karaoke-decide:latest",
                    "commands": ["python", "-m", "karaoke_decide.etl.listenbrainz_refresh"],
                    "args": ["run"],
                    "resources": {"limits": {"cpu": "4", "memory": "4Gi"}},
                }
            ],
            "service_account": lb_refresh_sa.email,
            "timeout": "10800s",
            # One retry covers a network blip during the ~22 GB stream; a failed
            # attempt logs an ERROR (error monitor alerts) and leaves prod untouched.
            "max_retries": 1,
        },
    },
    opts=pulumi.ResourceOptions(ignore_changes=["template.template.containers[0].image"]),
)

lb_refresh_invoker = gcp.cloudrunv2.JobIamMember(
    "lb-refresh-scheduler-invoker",
    project=project,
    location=region,
    name=lb_refresh_job.name,
    role="roles/run.invoker",
    member=lb_refresh_member,
)

# Full exports are dated the 1st and 15th and finish uploading ~2 days later.
# Checking twice a week keeps the lag under ~4 days; runs with no new export
# exit after a directory listing.
gcp.cloudscheduler.Job(
    "lb-refresh-scheduler",
    name="lb-refresh-twice-weekly",
    description="ListenBrainz statistics dump -> BigQuery refresh (no-op unless a new export exists)",
    project=project,
    region=region,
    schedule="0 11 * * 1,4",
    time_zone="UTC",
    attempt_deadline="60s",
    http_target={
        "uri": lb_refresh_job.name.apply(
            lambda name: f"https://run.googleapis.com/v2/projects/{project}/locations/{region}/jobs/{name}:run"
        ),
        "http_method": "POST",
        "oauth_token": {
            "service_account_email": lb_refresh_sa.email,
            "scope": "https://www.googleapis.com/auth/cloud-platform",
        },
    },
    opts=pulumi.ResourceOptions(depends_on=[lb_refresh_invoker]),
)

# =============================================================================
# ListenBrainz poller (Cloud Run Job + Cloud Scheduler)
# =============================================================================
# Daily: folds each ListenBrainz-connected user's new listens into their profile, marks
# stale connections, and checks pending Spotify-export imports handed to ListenBrainz.
# A Job (not a request on the API service) so the long run never loads API instances.
# See backend/services/listenbrainz_poller.py and backend/jobs/lb_poll.py.

LB_POLL_JOB_NAME = "lb-poll"
# Pending-import checks refresh MetaBrainz OAuth tokens, which are encrypted at rest.
LB_POLL_SECRETS = {
    "DECIDE_TOKEN_KEY": "decide-token-key",
    "METABRAINZ_CLIENT_ID": "metabrainz-client-id",
    "METABRAINZ_CLIENT_SECRET": "metabrainz-client-secret",
}

lb_poll_sa = gcp.serviceaccount.Account(
    "lb-poll-sa",
    account_id="lb-poll",
    display_name="ListenBrainz Poll Job",
    description="Runs the daily lb-poll Cloud Run Job",
    project=project,
)
lb_poll_member = lb_poll_sa.email.apply(lambda email: f"serviceAccount:{email}")

# Firestore: music_services, user_songs, user_artists.
gcp.projects.IAMMember(
    "lb-poll-datastore-user",
    project=project,
    role="roles/datastore.user",
    member=lb_poll_member,
)
# catalog.db (musicbrainz-data/catalog/) and availability.db / song index (kn-data).
for _name, _bucket in [("musicbrainz-data", musicbrainz_bucket.name), ("kn-data", "nomadkaraoke-kn-data")]:
    gcp.storage.BucketIAMMember(
        f"lb-poll-{_name}-reader",
        bucket=_bucket,
        role="roles/storage.objectViewer",
        member=lb_poll_member,
    )
# TrackMatcher's BigQuery fallback (only if catalog.db doesn't load; the job then exits 1 instead).
gcp.projects.IAMMember(
    "lb-poll-bq-job-user",
    project=project,
    role="roles/bigquery.jobUser",
    member=lb_poll_member,
)
gcp.bigquery.DatasetIamMember(
    "lb-poll-bq-viewer-prod",
    project=project,
    dataset_id=bigquery_dataset.dataset_id,
    role="roles/bigquery.dataViewer",
    member=lb_poll_member,
)
lb_poll_secret_access = [
    gcp.secretmanager.SecretIamMember(
        f"lb-poll-secret-access-{_secret}",
        project=project,
        secret_id=_secret,
        role="roles/secretmanager.secretAccessor",
        member=lb_poll_member,
    )
    for _secret in LB_POLL_SECRETS.values()
]

lb_poll_job = gcp.cloudrunv2.Job(
    "lb-poll-job",
    name=LB_POLL_JOB_NAME,
    project=project,
    location=region,
    deletion_protection=False,
    template={
        "template": {
            "containers": [
                {
                    # CI pins this to the deployed commit SHA (gcloud run jobs update --image).
                    "image": f"{region}-docker.pkg.dev/{project}/karaoke-repo/karaoke-decide:latest",
                    "commands": ["python", "-m", "backend.jobs.lb_poll"],
                    "envs": [
                        {"name": "ENVIRONMENT", "value": environment},
                        {"name": "GOOGLE_CLOUD_PROJECT", "value": project},
                        {"name": "GOOGLE_CLOUD_PROJECT_NUMBER", "value": PROJECT_NUMBER},
                        {"name": "CATALOG_BUCKET", "value": "nomadkaraoke-musicbrainz-data"},
                        {"name": "SONG_INDEX_BUCKET", "value": "nomadkaraoke-kn-data"},
                        {"name": "LISTENBRAINZ_OAUTH_ENABLED", "value": "true"},
                        {"name": "PUBLIC_API_BASE_URL", "value": "https://decide.nomadkaraoke.com"},
                        *[
                            {
                                "name": _env,
                                "value_source": {"secret_key_ref": {"secret": _secret, "version": "latest"}},
                            }
                            for _env, _secret in LB_POLL_SECRETS.items()
                        ],
                    ],
                    # 2Gi: catalog.db is decompressed into in-memory /tmp.
                    "resources": {"limits": {"cpu": "1", "memory": "2Gi"}},
                }
            ],
            "service_account": lb_poll_sa.email,
            # The poll budget (LB_POLL_BUDGET_SECONDS, 1500 s) stays inside this.
            "timeout": "1800s",
            # No retry: a failed run logs one ERROR; tomorrow's run picks up (a retry doubles LB load).
            "max_retries": 0,
        },
    },
    opts=pulumi.ResourceOptions(
        ignore_changes=["template.template.containers[0].image"],
        depends_on=lb_poll_secret_access,
    ),
)

lb_poll_invoker = gcp.cloudrunv2.JobIamMember(
    "lb-poll-scheduler-invoker",
    project=project,
    location=region,
    name=lb_poll_job.name,
    role="roles/run.invoker",
    member=lb_poll_member,
)

gcp.cloudscheduler.Job(
    "lb-poll-scheduler",
    name="lb-poll-daily",
    description="ListenBrainz new listens -> user profiles (daily poll)",
    project=project,
    region=region,
    schedule="30 6 * * *",
    time_zone="UTC",
    attempt_deadline="60s",
    http_target={
        "uri": lb_poll_job.name.apply(
            lambda name: f"https://run.googleapis.com/v2/projects/{project}/locations/{region}/jobs/{name}:run"
        ),
        "http_method": "POST",
        "oauth_token": {
            "service_account_email": lb_poll_sa.email,
            "scope": "https://www.googleapis.com/auth/cloud-platform",
        },
    },
    opts=pulumi.ResourceOptions(depends_on=[lb_poll_invoker]),
)

# =============================================================================
# Song search (Cloud Run service + daily index build job)
# =============================================================================
# karaoke-decide-search serves /api/search* for the Nomad Karaoke app (same image
# as the API, different command). It loads song_id.db + availability.db for the run
# named by SONG_INDEX_RUN (empty/absent -> song-index/latest.json). The
# song-index-build job builds the indexes daily, publishes them, and rolls the
# service by setting SONG_INDEX_RUN (a new revision); nothing else may set it.
# See backend/search/app.py, karaoke_decide/etl/song_index_build.py and
# docs/ARCHITECTURE.md § Song search.
#
# The CI deploy step (.github/workflows/ci.yml) repeats these settings with
# `gcloud run deploy --update-env-vars` (never --set-env-vars, which would wipe
# SONG_INDEX_RUN); tests/unit/test_infra_ci_parity.py keeps the two in step.
#
# First rollout: the service can't pass its /api/search/ready startup probe until an
# index exists, so bootstrap with searchServiceEnabled=false, run the job once with
# --no-deploy, then enable the service (docs/ARCHITECTURE.md § Song search, ops).

SEARCH_SERVICE_NAME = "karaoke-decide-search"
SONG_INDEX_JOB_NAME = "song-index-build"
# Created and owned by karaoke-gen (kn-data-sync writes song-id/, full/, community/).
KN_DATA_BUCKET = "nomadkaraoke-kn-data"
SONG_INDEX_PUBLIC_BUCKET = "nomadkaraoke-song-index-public"
# The secret resource is owned by karaoke-gen's Pulumi (modules/secrets.py); decide
# only reads it.
DECIDE_GEN_PARTNER_SECRET = "decide-gen-partner-secret"
SEARCH_HOST = "decide.nomadkaraoke.com"
SEARCH_OFFLINE_URL_TEMPLATE = "https://decide.nomadkaraoke.com/offline-index/{run}/offline.db.gz"
SEARCH_GEN_API_URL = "https://api.nomadkaraoke.com"

search_service_enabled = config.get_bool("searchServiceEnabled")
if search_service_enabled is None:
    search_service_enabled = True

# Public, get-only home of offline.db.gz (the app downloads it through the Worker's
# /offline-index/ passthrough). allUsers may read an object by its exact name but
# can't list, and run dirs carry an unguessable token. Server DBs stay in kn-data.
song_index_public_bucket = gcp.storage.Bucket(
    "song-index-public-bucket",
    name=SONG_INDEX_PUBLIC_BUCKET,
    project=project,
    location="US-CENTRAL1",
    uniform_bucket_level_access=True,
    public_access_prevention="inherited",
    # No age-based lifecycle: the served offline.db.gz may be any age (a stalled
    # KN sync means no new run), and the manifest keeps advertising it. The job
    # prunes to the live runs + the previous 3.
    soft_delete_policy={"retention_duration_seconds": 0},
)
gcp.storage.BucketIAMMember(
    "song-index-public-object-reader",
    bucket=song_index_public_bucket.name,
    # objects.get only (no objects.list, unlike objectViewer).
    role="roles/storage.legacyObjectReader",
    member="allUsers",
)

# --- Search service runtime identity ---------------------------------------
song_search_sa = gcp.serviceaccount.Account(
    "song-search-sa",
    account_id="song-search",
    display_name="Song Search Service",
    description="Runtime identity of the karaoke-decide-search Cloud Run service",
    project=project,
)
song_search_member = song_search_sa.email.apply(lambda email: f"serviceAccount:{email}")

song_search_index_reader = gcp.storage.BucketIAMMember(
    "song-search-kn-data-reader",
    bucket=KN_DATA_BUCKET,
    role="roles/storage.objectViewer",
    member=song_search_member,
)
song_search_secret_access = gcp.secretmanager.SecretIamMember(
    "song-search-gen-partner-secret-access",
    project=project,
    secret_id=DECIDE_GEN_PARTNER_SECRET,
    role="roles/secretmanager.secretAccessor",
    member=song_search_member,
)

# --- Index build job identity ----------------------------------------------
song_index_build_sa = gcp.serviceaccount.Account(
    "song-index-build-sa",
    account_id="song-index-build",
    display_name="Song Index Build Job",
    description="Runs the daily song-index-build Cloud Run Job and rolls karaoke-decide-search",
    project=project,
)
song_index_build_member = song_index_build_sa.email.apply(lambda email: f"serviceAccount:{email}")

# Read the sources (song-id/, full/, community/) and list song-index/ for pruning.
gcp.storage.BucketIAMMember(
    "song-index-build-kn-data-reader",
    bucket=KN_DATA_BUCKET,
    role="roles/storage.objectViewer",
    member=song_index_build_member,
)
# Write/overwrite/delete only under song-index/ (KN exports stay read-only).
gcp.storage.BucketIAMMember(
    "song-index-build-kn-data-writer",
    bucket=KN_DATA_BUCKET,
    role="roles/storage.objectAdmin",
    member=song_index_build_member,
    condition={
        "title": "song-index prefix only",
        "description": "song-index-build may only write and prune song-index/",
        "expression": (f'resource.name.startsWith("projects/_/buckets/{KN_DATA_BUCKET}/objects/song-index/")'),
    },
)
gcp.storage.BucketIAMMember(
    "song-index-build-public-admin",
    bucket=song_index_public_bucket.name,
    role="roles/storage.objectAdmin",
    member=song_index_build_member,
)
# Read revisions: pruning keeps every run a live revision serves, via
# RevisionsClient.get_revision. Revisions are separate IAM-checked resources and
# Cloud Run documents no service-to-revision inheritance of a service-level grant,
# so run.developer on the service alone isn't relied on; run.viewer is read-only.
gcp.projects.IAMMember(
    "song-index-build-run-viewer",
    project=project,
    role="roles/run.viewer",
    member=song_index_build_member,
)
# Deploying a revision that runs as song-search requires actAs on it.
gcp.serviceaccount.IAMMember(
    "song-index-build-act-as-song-search",
    service_account_id=song_search_sa.name,
    role="roles/iam.serviceAccountUser",
    member=song_index_build_member,
)
# Cloud Run checks that whoever deploys a revision can pull its image, so rolling
# the service needs read on karaoke-repo (without it every rollout 403s with
# artifactregistry.repositories.downloadArtifacts denied).
gcp.artifactregistry.RepositoryIamMember(
    "song-index-build-karaoke-repo-reader",
    project=project,
    location=region,
    repository=artifact_repo.name,
    role="roles/artifactregistry.reader",
    member=song_index_build_member,
)

song_index_build_job = gcp.cloudrunv2.Job(
    "song-index-build-job",
    name=SONG_INDEX_JOB_NAME,
    project=project,
    location=region,
    deletion_protection=False,
    template={
        "template": {
            "containers": [
                {
                    # CI pins this to the deployed commit SHA (gcloud run jobs update --image).
                    "image": f"{region}-docker.pkg.dev/{project}/karaoke-repo/karaoke-decide:latest",
                    "commands": ["python", "-m", "karaoke_decide.etl.song_index_build"],
                    "args": ["run"],
                    # The job disk is in-memory and shares these 8 GiB with the build.
                    "resources": {"limits": {"cpu": "2", "memory": "8Gi"}},
                }
            ],
            "service_account": song_index_build_sa.email,
            "timeout": "3600s",
            # A failed run (or failed eval gate) logs one ERROR (error monitor alerts)
            # and leaves the served index untouched; tomorrow's run retries.
            "max_retries": 0,
        },
    },
    opts=pulumi.ResourceOptions(ignore_changes=["template.template.containers[0].image"]),
)

song_index_build_invoker = gcp.cloudrunv2.JobIamMember(
    "song-index-build-scheduler-invoker",
    project=project,
    location=region,
    name=song_index_build_job.name,
    role="roles/run.invoker",
    member=song_index_build_member,
)

# kn-data-sync exports song-id/ + the KN full export from ~04:30 ET; 13:30 UTC is
# well after both. A run with an unchanged source exits after reading two JSON files.
gcp.cloudscheduler.Job(
    "song-index-build-scheduler",
    name="song-index-build-daily",
    description="Daily song-search index build + eval gate + rollout (no-op unless the source changed)",
    project=project,
    region=region,
    schedule="30 13 * * *",
    time_zone="UTC",
    attempt_deadline="60s",
    http_target={
        "uri": song_index_build_job.name.apply(
            lambda name: f"https://run.googleapis.com/v2/projects/{project}/locations/{region}/jobs/{name}:run"
        ),
        "http_method": "POST",
        "oauth_token": {
            "service_account_email": song_index_build_sa.email,
            "scope": "https://www.googleapis.com/auth/cloud-platform",
        },
    },
    opts=pulumi.ResourceOptions(depends_on=[song_index_build_invoker]),
)

search_service = None
if search_service_enabled:
    search_service = gcp.cloudrunv2.Service(
        "karaoke-decide-search-api",
        name=SEARCH_SERVICE_NAME,
        project=project,
        location=region,
        ingress="INGRESS_TRAFFIC_ALL",
        launch_stage="GA",
        deletion_protection=False,
        template={
            "containers": [
                {
                    "image": f"{region}-docker.pkg.dev/{project}/karaoke-repo/karaoke-decide:latest",
                    "commands": ["uvicorn"],
                    "args": ["backend.search.app:search_app", "--host", "0.0.0.0", "--port", "8000"],
                    "ports": {"container_port": 8000, "name": "http1"},
                    # SONG_INDEX_RUN is deliberately absent: the song-index-build job
                    # adds and owns it (absent/empty -> the loader reads latest.json).
                    # envs are ignore_changes'd below so Pulumi never resets it.
                    "envs": [
                        {"name": "SONG_INDEX_BUCKET", "value": KN_DATA_BUCKET},
                        {"name": "SONG_INDEX_OFFLINE_URL_TEMPLATE", "value": SEARCH_OFFLINE_URL_TEMPLATE},
                        {"name": "GEN_API_URL", "value": SEARCH_GEN_API_URL},
                        {
                            "name": "DECIDE_GEN_PARTNER_SECRET",
                            "value_source": {
                                "secret_key_ref": {
                                    "secret": DECIDE_GEN_PARTNER_SECRET,
                                    "version": "latest",
                                }
                            },
                        },
                    ],
                    "resources": {
                        # The index lives in the in-memory /tmp alongside the process.
                        "limits": {"cpu": "1", "memory": "4Gi"},
                        "cpu_idle": True,
                        "startup_cpu_boost": True,
                    },
                    # uvicorn listens only after the index has loaded, so the probe is
                    # refused until then: poll every 10 s for up to 240 s (Cloud Run's
                    # cap on failure_threshold x period_seconds). /ready is 503 if the
                    # load failed, so a revision without an index never takes traffic.
                    "startup_probe": {
                        "http_get": {"path": "/api/search/ready", "port": 8000},
                        "timeout_seconds": 5,
                        "period_seconds": 10,
                        "failure_threshold": 24,
                    },
                }
            ],
            "scaling": {
                "min_instance_count": 0,  # scale to zero; must match CI --min-instances 0
                "max_instance_count": 3,
            },
            "max_instance_request_concurrency": 80,
            "service_account": song_search_sa.email,
        },
        traffics=[
            {
                "percent": 100,
                "type": "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST",
            }
        ],
        opts=pulumi.ResourceOptions(
            depends_on=[song_search_index_reader, song_search_secret_access],
            # CI owns the image (pinned to the commit SHA) and, after creation, the env
            # (--update-env-vars); the build job owns SONG_INDEX_RUN; operators own
            # traffic (a rollback pins it; Pulumi must never un-pin it). Run
            # `pulumi refresh` before an `up` that changes this service, so the
            # template Pulumi sends carries the live SONG_INDEX_RUN.
            ignore_changes=[
                "template.containers[0].image",
                "template.containers[0].envs",
                "traffics",
                "client",
                "client_version",
            ],
        ),
    )

    gcp.cloudrunv2.ServiceIamMember(
        "karaoke-decide-search-invoker",
        name=search_service.name.apply(lambda name: f"projects/{project}/locations/{region}/services/{name}"),
        project=project,
        location=region,
        role="roles/run.invoker",
        member="allUsers",
    )

    # The job rolls the service (update + wait); scoped to this one service.
    gcp.cloudrunv2.ServiceIamMember(
        "song-index-build-search-developer",
        name=search_service.name.apply(lambda name: f"projects/{project}/locations/{region}/services/{name}"),
        project=project,
        location=region,
        role="roles/run.developer",
        member=song_index_build_member,
    )

    # 60 s ping through the Worker from the 3-location minimum: keeps an instance
    # warm (cpu_idle, so idle time costs ~nothing) and exercises the Worker route.
    # No alert policy: a hard-down search service shows up in the app's own
    # fallbacks and in ERROR logs, which already page #alerts.
    gcp.monitoring.UptimeCheckConfig(
        "search-health-uptime",
        display_name="Song Search - /api/search/health",
        project=project,
        timeout="10s",
        period="60s",
        selected_regions=["USA_OREGON", "USA_IOWA", "USA_VIRGINIA"],
        monitored_resource={
            "type": "uptime_url",
            "labels": {"project_id": project, "host": SEARCH_HOST},
        },
        http_check={
            "path": "/api/search/health",
            "port": 443,
            "use_ssl": True,
            "validate_ssl": True,
            "request_method": "GET",
            "accepted_response_status_codes": [{"status_value": 200}],
        },
        content_matchers=[{"content": '"ok":true', "matcher": "CONTAINS_STRING"}],
    )

    pulumi.export("search_service_url", search_service.uri)

# 60 s ping of the main API through the Worker. With request-based billing and
# cpu_idle an idle instance costs ~nothing, and a warm instance means recommendations
# v2 rarely pays the catalog-load cold start. Like search-health-uptime, no alert policy:
# a hard-down API shows up in ERROR logs, which already page #alerts.
gcp.monitoring.UptimeCheckConfig(
    "decide-api-health-uptime",
    display_name="Decide API - /api/health",
    project=project,
    timeout="10s",
    period="60s",
    selected_regions=["USA_OREGON", "USA_IOWA", "USA_VIRGINIA"],
    monitored_resource={
        "type": "uptime_url",
        "labels": {"project_id": project, "host": SEARCH_HOST},
    },
    http_check={
        "path": "/api/health",
        "port": 443,
        "use_ssl": True,
        "validate_ssl": True,
        "request_method": "GET",
        "accepted_response_status_codes": [{"status_value": 200}],
    },
    content_matchers=[{"content": '"status"', "matcher": "CONTAINS_STRING"}],
)

# Bucket access for the catalog / song-index reads: the main service runs as the default
# compute SA, which already holds project-level roles/storage.admin (verified 2026-10-21
# via the project IAM policy), so it can read gs://nomadkaraoke-musicbrainz-data/catalog/
# and gs://nomadkaraoke-kn-data/song-index/ without extra bucket grants. If that project
# role is ever narrowed, add roles/storage.objectViewer BucketIAMMembers (with prefix
# conditions) for {PROJECT_NUMBER}-compute@developer.gserviceaccount.com on both buckets.

pulumi.export("song_index_public_bucket", song_index_public_bucket.name)

# =============================================================================
# Cloudflare Worker (API Proxy)
# =============================================================================
# Proxies /api/* requests from decide.nomadkaraoke.com to Cloud Run backend.
# This eliminates CORS issues by keeping everything same-origin.
#
# Required config:
#   pulumi config set cloudflare:apiToken <token> --secret
#   pulumi config set cloudflareAccountId <account_id>
#   pulumi config set cloudflareZoneId <zone_id>

cloudflare_account_id = config.get("cloudflareAccountId") or ""
cloudflare_zone_id = config.get("cloudflareZoneId") or ""

# Worker script: the same file a manual setup pastes (cloudflare-worker/api-proxy.js),
# read here so the two can't drift.
API_PROXY_WORKER_SCRIPT = (Path(__file__).parent / "cloudflare-worker" / "api-proxy.js").read_text()

# Only create Cloudflare resources if account and zone IDs are configured
if cloudflare_account_id and cloudflare_zone_id:
    # API proxy Worker script
    api_proxy_worker = cloudflare.WorkersScript(
        "api-proxy-worker",
        account_id=cloudflare_account_id,
        script_name="karaoke-decide-api-proxy",
        content=API_PROXY_WORKER_SCRIPT,
        main_module="worker.js",
        compatibility_date="2024-01-01",
        # /api/search* goes to the search service (the script has the same URL as
        # its default, for the bootstrap phase before the service exists).
        bindings=(
            [{"name": "SEARCH_BACKEND_URL", "type": "plain_text", "text": search_service.uri}]
            if search_service is not None
            else []
        ),
    )

    # Route to trigger Worker for /api/* requests
    api_proxy_route = cloudflare.WorkersRoute(
        "api-proxy-route",
        zone_id=cloudflare_zone_id,
        pattern="decide.nomadkaraoke.com/api/*",
        script=api_proxy_worker.script_name,
    )

    # Edge-cached passthrough for the app's offline.db.gz download
    # (/offline-index/<run-token>/offline.db.gz -> the public song-index bucket).
    cloudflare.WorkersRoute(
        "offline-index-route",
        zone_id=cloudflare_zone_id,
        pattern="decide.nomadkaraoke.com/offline-index/*",
        script=api_proxy_worker.script_name,
    )

    pulumi.export("cloudflare_worker_name", api_proxy_worker.script_name)
    pulumi.export("cloudflare_route_pattern", api_proxy_route.pattern)
else:
    pulumi.log.warn(
        "Cloudflare config not set. Skipping Worker creation. "
        "Set cloudflareAccountId and cloudflareZoneId to enable."
    )

# =============================================================================
# Outputs
# =============================================================================

pulumi.export("cloud_run_url", cloud_run_service.uri)
pulumi.export("bigquery_dataset", bigquery_dataset.dataset_id)
pulumi.export("data_bucket", data_bucket.name)
pulumi.export("artifact_repo", artifact_repo.name)
