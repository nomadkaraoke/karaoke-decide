"""
Pulumi infrastructure for Nomad Karaoke Decide.

Resources managed:
- BigQuery dataset and tables (karaoke catalog data)
- GCS bucket (data staging)
- Artifact Registry repository (container images)
- Cloud Run service (backend API)
- MusicBrainz weekly refresh (Cloud Run Job, Scheduler, staging dataset, bucket)
- ListenBrainz refresh (Cloud Run Job, Scheduler, staging dataset)
- IAM bindings (service account permissions)
- Cloudflare Worker (API proxy)
"""

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
                ],
                "resources": {
                    "limits": {
                        "cpu": "1",
                        "memory": "1Gi",  # Increased from 512Mi to handle collaborative filtering queries
                    },
                    "cpu_idle": True,
                    "startup_cpu_boost": True,
                },
                "startup_probe": {
                    "tcp_socket": {"port": 8000},
                    "timeout_seconds": 240,
                    "period_seconds": 240,
                    "failure_threshold": 1,
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
    opts=pulumi.ResourceOptions(protect=True),
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

# Worker script content
API_PROXY_WORKER_SCRIPT = """
const DEFAULT_BACKEND_URL = "https://karaoke-decide-718638054799.us-central1.run.app";

export default {
  async fetch(request, env, ctx) {
    const backendBaseUrl = env.BACKEND_URL || DEFAULT_BACKEND_URL;
    const url = new URL(request.url);

    // Only proxy /api/* requests
    if (!url.pathname.startsWith("/api")) {
      // Pass through to origin (GitHub Pages)
      return fetch(request);
    }

    // Build the backend URL
    const backendUrl = new URL(url.pathname + url.search, backendBaseUrl);

    // Clone headers, removing Cloudflare-specific ones
    const headers = new Headers(request.headers);
    headers.delete("cf-connecting-ip");
    headers.delete("cf-ipcountry");
    headers.delete("cf-ray");
    headers.delete("cf-visitor");

    // Forward the request to Cloud Run
    const backendRequest = new Request(backendUrl.toString(), {
      method: request.method,
      headers: headers,
      body: request.body,
      redirect: "follow",
    });

    try {
      const response = await fetch(backendRequest);

      // Clone response and remove CORS headers (not needed for same-origin)
      const newHeaders = new Headers(response.headers);
      newHeaders.delete("access-control-allow-origin");
      newHeaders.delete("access-control-allow-credentials");
      newHeaders.delete("access-control-allow-methods");
      newHeaders.delete("access-control-allow-headers");

      return new Response(response.body, {
        status: response.status,
        statusText: response.statusText,
        headers: newHeaders,
      });
    } catch (error) {
      return new Response(
        JSON.stringify({
          error: "Backend unavailable",
          message: error.message,
        }),
        {
          status: 502,
          headers: { "Content-Type": "application/json" },
        }
      );
    }
  },
};
"""

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
    )

    # Route to trigger Worker for /api/* requests
    api_proxy_route = cloudflare.WorkersRoute(
        "api-proxy-route",
        zone_id=cloudflare_zone_id,
        pattern="decide.nomadkaraoke.com/api/*",
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
