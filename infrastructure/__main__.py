"""
Pulumi infrastructure for Nomad Karaoke Decide.

Resources managed:
- BigQuery dataset and tables (karaoke catalog data)
- GCS bucket (data staging)
- Artifact Registry repository (container images)
- Cloud Run service (backend API)
- IAM bindings (service account permissions)
"""

import pulumi
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

# Allow Cloud Tasks to invoke Cloud Run (for OIDC authentication)
cloud_tasks_invoker = gcp.projects.IAMMember(
    "compute-sa-run-invoker",
    project=project,
    role="roles/run.invoker",
    member=f"serviceAccount:{PROJECT_NUMBER}-compute@developer.gserviceaccount.com",
)

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
                        "name": "SENDGRID_API_KEY",
                        "value_source": {
                            "secret_key_ref": {
                                "secret": "sendgrid-api-key",
                                "version": "latest",
                            }
                        },
                    },
                ],
                "resources": {
                    "limits": {
                        "cpu": "1",
                        "memory": "512Mi",
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
        "timeout": "300s",
        "service_account": f"{PROJECT_NUMBER}-compute@developer.gserviceaccount.com",
    },
    traffics=[
        {
            "percent": 100,
            "type": "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST",
        }
    ],
    scaling={
        "min_instance_count": 0,
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
    "sendgrid-api-key",
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
# Outputs
# =============================================================================

pulumi.export("cloud_run_url", cloud_run_service.uri)
pulumi.export("bigquery_dataset", bigquery_dataset.dataset_id)
pulumi.export("data_bucket", data_bucket.name)
pulumi.export("artifact_repo", artifact_repo.name)
