"""Anti-drift: the search service's Cloud Run settings live in BOTH Pulumi
(infrastructure/__main__.py) and the CI deploy step (.github/workflows/ci.yml), and
CI's flags win on every deploy. These tests parse both files and fail when they
disagree, or when CI could reset SONG_INDEX_RUN (the song-index-build job owns it).
"""

from __future__ import annotations

import ast
import re
import shlex
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
PULUMI = ROOT / "infrastructure" / "__main__.py"
CI = ROOT / ".github" / "workflows" / "ci.yml"
PULUMI_STACK = ROOT / "infrastructure" / "Pulumi.prod.yaml"

SERVICE = "karaoke-decide-search"
JOB = "song-index-build"
PULUMI_SERVICE_RESOURCE = "karaoke-decide-search-api"
PULUMI_JOB_RESOURCE = "song-index-build-job"
PULUMI_MAIN_RESOURCE = "karaoke-decide-api"
JOB_OWNED_ENV = "SONG_INDEX_RUN"


# --------------------------------------------------------------------------
# Pulumi side
# --------------------------------------------------------------------------


def _pulumi_tree() -> ast.Module:
    return ast.parse(PULUMI.read_text())


def _resource_call(tree: ast.Module, cls: str, resource_name: str) -> ast.Call:
    """The ``gcp.cloudrunv2.<cls>("<resource_name>", ...)`` call."""
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == cls
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == resource_name
        ):
            return node
    raise AssertionError(f"no {cls}({resource_name!r}, ...) in {PULUMI}")


def _module_constants(tree: ast.Module) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            try:
                out[node.targets[0].id] = ast.literal_eval(node.value)
            except ValueError:
                continue
    return out


def _value(node: ast.AST, consts: dict[str, Any]) -> Any:
    """A literal, a module constant, or (recursively) a dict/list of them; else the AST."""
    if isinstance(node, ast.Name) and node.id in consts:
        return consts[node.id]
    if isinstance(node, ast.Dict):
        return {
            _value(k, consts): _value(v, consts) for k, v in zip(node.keys, node.values, strict=True) if k is not None
        }
    if isinstance(node, ast.List | ast.Tuple):
        return [_value(e, consts) for e in node.elts]
    try:
        return ast.literal_eval(node)
    except ValueError:
        return node


def _kwargs(call: ast.Call, consts: dict[str, Any]) -> dict[str, Any]:
    return {kw.arg: _value(kw.value, consts) for kw in call.keywords if kw.arg}


def _ignore_changes(call: ast.Call) -> list[str]:
    for kw in call.keywords:
        if kw.arg == "opts" and isinstance(kw.value, ast.Call):
            for inner in kw.value.keywords:
                if inner.arg == "ignore_changes":
                    return list(ast.literal_eval(inner.value))
    return []


@pytest.fixture(scope="module")
def pulumi_service() -> dict[str, Any]:
    tree = _pulumi_tree()
    call = _resource_call(tree, "Service", PULUMI_SERVICE_RESOURCE)
    kw = _kwargs(call, _module_constants(tree))
    kw["_ignore_changes"] = _ignore_changes(call)
    return kw


@pytest.fixture(scope="module")
def pulumi_main() -> dict[str, Any]:
    tree = _pulumi_tree()
    return _kwargs(_resource_call(tree, "Service", PULUMI_MAIN_RESOURCE), _module_constants(tree))


@pytest.fixture(scope="module")
def pulumi_job() -> dict[str, Any]:
    tree = _pulumi_tree()
    call = _resource_call(tree, "Job", PULUMI_JOB_RESOURCE)
    kw = _kwargs(call, _module_constants(tree))
    kw["_ignore_changes"] = _ignore_changes(call)
    return kw


def _container(service: dict[str, Any]) -> dict[str, Any]:
    containers = service["template"]["containers"]
    assert len(containers) == 1
    return containers[0]


def _plain_envs(container: dict[str, Any]) -> dict[str, str]:
    return {e["name"]: e["value"] for e in container["envs"] if "value" in e}


def _secret_envs(container: dict[str, Any]) -> dict[str, str]:
    out = {}
    for e in container["envs"]:
        if "value_source" in e:
            ref = e["value_source"]["secret_key_ref"]
            out[e["name"]] = f"{ref['secret']}:{ref['version']}"
    return out


# --------------------------------------------------------------------------
# CI side
# --------------------------------------------------------------------------


def _ci_steps() -> list[dict[str, Any]]:
    wf = yaml.safe_load(CI.read_text())
    return wf["jobs"]["deploy-backend"]["steps"]


def _command_line(script: str, prefix: str) -> list[str]:
    """The shell command starting with ``prefix`` in ``script``, continuation lines
    joined, ``${{ ... }}`` expressions replaced by a placeholder, split shell-style."""
    joined = re.sub(r"\\\n", " ", script)
    joined = re.sub(r"\$\{\{[^}]*\}\}", "EXPR", joined)
    for line in joined.splitlines():
        if line.strip().startswith(prefix):
            return shlex.split(line.strip())
    raise AssertionError(f"no `{prefix}` command in the CI step")


def _flags(argv: list[str]) -> dict[str, str | bool]:
    """``--a b``, ``--a=b`` and bare ``--flag`` → ``{"--a": "b", "--flag": True}``."""
    out: dict[str, str | bool] = {}
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok.startswith("--"):
            if "=" in tok:
                k, v = tok.split("=", 1)
                out[k] = v
            elif i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                out[tok] = argv[i + 1]
                i += 1
            else:
                out[tok] = True
        i += 1
    return out


def _kv(value: str) -> dict[str, str]:
    return dict(item.split("=", 1) for item in value.split(","))


@pytest.fixture(scope="module")
def ci_deploy() -> dict[str, str | bool]:
    for step in _ci_steps():
        if f"gcloud run deploy {SERVICE}" in step.get("run", ""):
            return _flags(_command_line(step["run"], f"gcloud run deploy {SERVICE}"))
    raise AssertionError(f"no `gcloud run deploy {SERVICE}` step in deploy-backend")


@pytest.fixture(scope="module")
def ci_main_deploy() -> dict[str, str | bool]:
    for step in _ci_steps():
        if "gcloud run deploy ${{ env.IMAGE_NAME }}" in step.get("run", ""):
            return _flags(_command_line(step["run"], "gcloud run deploy"))
    raise AssertionError("no main-service `gcloud run deploy ${{ env.IMAGE_NAME }}` step in deploy-backend")


@pytest.fixture(scope="module")
def ci_job_update() -> dict[str, str | bool]:
    for step in _ci_steps():
        if f"gcloud run jobs update {JOB}" in step.get("run", ""):
            return _flags(_command_line(step["run"], f"gcloud run jobs update {JOB}"))
    raise AssertionError(f"no `gcloud run jobs update {JOB}` step in deploy-backend")


# --------------------------------------------------------------------------
# Parity
# --------------------------------------------------------------------------


def test_memory_and_cpu_match(pulumi_service: dict[str, Any], ci_deploy: dict[str, Any]) -> None:
    limits = _container(pulumi_service)["resources"]["limits"]
    assert limits["memory"] == ci_deploy["--memory"] == "4Gi"
    assert limits["cpu"] == ci_deploy["--cpu"] == "1"


def test_instance_counts_match(pulumi_service: dict[str, Any], ci_deploy: dict[str, Any]) -> None:
    scaling = pulumi_service["template"]["scaling"]
    assert str(scaling["min_instance_count"]) == ci_deploy["--min-instances"] == "0"
    assert str(scaling["max_instance_count"]) == ci_deploy["--max-instances"] == "3"


def test_concurrency_matches(pulumi_service: dict[str, Any], ci_deploy: dict[str, Any]) -> None:
    assert str(pulumi_service["template"]["max_instance_request_concurrency"]) == ci_deploy["--concurrency"] == "80"


def test_cpu_idle_and_boost_match(pulumi_service: dict[str, Any], ci_deploy: dict[str, Any]) -> None:
    resources = _container(pulumi_service)["resources"]
    assert resources["cpu_idle"] is True and ci_deploy.get("--cpu-throttling") is True
    assert resources["startup_cpu_boost"] is True and ci_deploy.get("--cpu-boost") is True


def test_command_matches(pulumi_service: dict[str, Any], ci_deploy: dict[str, Any]) -> None:
    c = _container(pulumi_service)
    assert c["commands"] == ci_deploy["--command"].split(",") == ["uvicorn"]
    assert c["args"] == ci_deploy["--args"].split(",")
    assert c["args"][0] == "backend.search.app:search_app"
    assert str(c["ports"]["container_port"]) == ci_deploy["--port"] == "8000"


def test_startup_probe_matches(pulumi_service: dict[str, Any], ci_deploy: dict[str, Any]) -> None:
    probe = _container(pulumi_service)["startup_probe"]
    ci = _kv(str(ci_deploy["--startup-probe"]))
    assert probe["http_get"]["path"] == ci["httpGet.path"] == "/api/search/ready"
    assert str(probe["http_get"]["port"]) == ci["httpGet.port"] == "8000"
    assert str(probe["timeout_seconds"]) == ci["timeoutSeconds"]
    assert str(probe["period_seconds"]) == ci["periodSeconds"]
    assert str(probe["failure_threshold"]) == ci["failureThreshold"]
    # Cloud Run caps a startup probe's window (failureThreshold x periodSeconds) at 240 s.
    assert probe["failure_threshold"] * probe["period_seconds"] == 240
    assert probe["timeout_seconds"] <= probe["period_seconds"]


def test_service_account_matches(pulumi_service: dict[str, Any], ci_deploy: dict[str, Any]) -> None:
    assert ast.unparse(pulumi_service["template"]["service_account"]) == "song_search_sa.email"
    tree = _pulumi_tree()
    account = _kwargs(_resource_call(tree, "Account", "song-search-sa"), _module_constants(tree))
    project = yaml.safe_load(PULUMI_STACK.read_text())["config"]["gcp:project"]
    assert ci_deploy["--service-account"] == f"{account['account_id']}@{project}.iam.gserviceaccount.com"


def test_plain_env_vars_match(pulumi_service: dict[str, Any], ci_deploy: dict[str, Any]) -> None:
    assert _plain_envs(_container(pulumi_service)) == _kv(str(ci_deploy["--update-env-vars"]))


def test_secret_env_vars_match(pulumi_service: dict[str, Any], ci_deploy: dict[str, Any]) -> None:
    secrets = _secret_envs(_container(pulumi_service))
    assert secrets == _kv(str(ci_deploy["--update-secrets"]))
    assert secrets == {"DECIDE_GEN_PARTNER_SECRET": "decide-gen-partner-secret:latest"}


def test_offline_url_template_goes_through_the_worker(pulumi_service: dict[str, Any]) -> None:
    template = _plain_envs(_container(pulumi_service))["SONG_INDEX_OFFLINE_URL_TEMPLATE"]
    assert template == "https://decide.nomadkaraoke.com/offline-index/{run}/offline.db.gz"


# --------------------------------------------------------------------------
# Main API service (karaoke-decide): Pulumi vs CI deploy flags
# --------------------------------------------------------------------------


def test_main_memory_cpu_and_scaling_match(pulumi_main: dict[str, Any], ci_main_deploy: dict[str, Any]) -> None:
    limits = _container(pulumi_main)["resources"]["limits"]
    # 2Gi: availability.db + catalog.db are decompressed into in-memory /tmp.
    assert limits["memory"] == ci_main_deploy["--memory"] == "2Gi"
    assert limits["cpu"] == ci_main_deploy["--cpu"] == "1"
    template = pulumi_main["template"]
    assert str(template["scaling"]["max_instance_count"]) == ci_main_deploy["--max-instances"] == "10"
    assert str(pulumi_main["scaling"]["min_instance_count"]) == ci_main_deploy["--min-instances"] == "0"
    assert str(template["max_instance_request_concurrency"]) == ci_main_deploy["--concurrency"] == "80"


def test_main_secret_env_vars_match(pulumi_main: dict[str, Any], ci_main_deploy: dict[str, Any]) -> None:
    # CI's --set-secrets replaces the service's secret envs on every deploy: both lists must agree.
    secrets = _secret_envs(_container(pulumi_main))
    assert secrets == _kv(str(ci_main_deploy["--set-secrets"]))
    assert secrets["DECIDE_KJBOX_SECRET"] == "decide-kjbox-secret:latest"


def test_kjbox_secret_is_managed_and_readable() -> None:
    tree = _pulumi_tree()
    secret = _kwargs(_resource_call(tree, "Secret", "decide-kjbox-secret"), _module_constants(tree))
    assert secret["secret_id"] == "decide-kjbox-secret"
    access = _kwargs(
        _resource_call(tree, "SecretIamMember", "cloud-run-secret-access-decide-kjbox-secret"),
        _module_constants(tree),
    )
    assert access["secret_id"] == "decide-kjbox-secret"
    assert access["role"] == "roles/secretmanager.secretAccessor"


def test_main_service_waits_for_the_kjbox_secret_and_its_grant() -> None:
    """The API mounts DECIDE_KJBOX_SECRET, so Pulumi must create the secret and the accessor
    grant before it updates the service (the version itself is added by hand)."""
    call = _resource_call(_pulumi_tree(), "Service", PULUMI_MAIN_RESOURCE)
    opts = next(kw.value for kw in call.keywords if kw.arg == "opts")
    assert isinstance(opts, ast.Call)
    deps = next(kw.value for kw in opts.keywords if kw.arg == "depends_on")
    assert isinstance(deps, ast.List)
    assert {e.id for e in deps.elts if isinstance(e, ast.Name)} == {
        "decide_kjbox_secret",
        "decide_kjbox_secret_access",
    }


def test_main_cpu_idle_and_boost_match(pulumi_main: dict[str, Any], ci_main_deploy: dict[str, Any]) -> None:
    resources = _container(pulumi_main)["resources"]
    assert resources["cpu_idle"] is True and ci_main_deploy.get("--cpu-throttling") is True
    assert resources["startup_cpu_boost"] is True and ci_main_deploy.get("--cpu-boost") is True


def test_main_startup_probe_matches(pulumi_main: dict[str, Any], ci_main_deploy: dict[str, Any]) -> None:
    probe = _container(pulumi_main)["startup_probe"]
    ci = _kv(str(ci_main_deploy["--startup-probe"]))
    assert probe["http_get"]["path"] == ci["httpGet.path"] == "/api/ready"
    assert str(probe["http_get"]["port"]) == ci["httpGet.port"] == "8000"
    assert str(probe["initial_delay_seconds"]) == ci["initialDelaySeconds"] == "0"
    # 1 s: a ready instance takes traffic within a second (10 s wasted up to 10 s of cold start).
    assert str(probe["period_seconds"]) == ci["periodSeconds"] == "1"
    assert str(probe["timeout_seconds"]) == ci["timeoutSeconds"] == "1"  # Cloud Run: timeout <= period
    assert str(probe["failure_threshold"]) == ci["failureThreshold"] == "240"
    assert probe["failure_threshold"] * probe["period_seconds"] == 240  # the whole 240 s startup window


# --------------------------------------------------------------------------
# SONG_INDEX_RUN belongs to the build job
# --------------------------------------------------------------------------


def test_ci_never_replaces_env_or_touches_song_index_run(ci_deploy: dict[str, Any]) -> None:
    # --set-env-vars / --set-secrets / --clear-env-vars would wipe the job's SONG_INDEX_RUN.
    for flag in ("--set-env-vars", "--env-vars-file", "--clear-env-vars", "--set-secrets", "--remove-env-vars"):
        assert flag not in ci_deploy
    assert JOB_OWNED_ENV not in str(ci_deploy["--update-env-vars"])


def test_pulumi_leaves_song_index_run_to_the_job(pulumi_service: dict[str, Any]) -> None:
    names = [e["name"] for e in _container(pulumi_service)["envs"]]
    assert JOB_OWNED_ENV not in names  # the job appends it; empty → latest.json
    assert "template.containers[0].envs" in pulumi_service["_ignore_changes"]
    assert "template.containers[0].image" in pulumi_service["_ignore_changes"]
    # A rollback pins traffic; a later `pulumi up` must not move it back to LATEST.
    assert "traffics" in pulumi_service["_ignore_changes"]


def test_ci_never_mentions_song_index_run() -> None:
    assert JOB_OWNED_ENV not in CI.read_text()


# --------------------------------------------------------------------------
# The index build job
# --------------------------------------------------------------------------


def test_job_image_is_pinned_by_ci(pulumi_job: dict[str, Any], ci_job_update: dict[str, Any]) -> None:
    assert ci_job_update["--image"].endswith(":EXPR")  # ...:${{ github.sha }}
    assert "template.template.containers[0].image" in pulumi_job["_ignore_changes"]


def test_job_shape(pulumi_job: dict[str, Any]) -> None:
    tmpl = pulumi_job["template"]["template"]
    (c,) = tmpl["containers"]
    assert c["commands"] == ["python", "-m", "karaoke_decide.etl.song_index_build"]
    assert c["args"] == ["run"]
    assert c["resources"]["limits"] == {"cpu": "2", "memory": "8Gi"}
    assert tmpl["timeout"] == "3600s"
    assert tmpl["max_retries"] == 0


# --------------------------------------------------------------------------
# Main service secrets are readable; Phase 3b env; the lb-poll job
# --------------------------------------------------------------------------


def test_main_service_secrets_have_pulumi_access(ci_main_deploy: dict[str, Any]) -> None:
    """Each secret CI mounts on the API has an accessor grant (REQUIRED_SECRETS or a dedicated one)."""
    consts = _module_constants(_pulumi_tree())
    granted = set(consts["REQUIRED_SECRETS"]) | {"decide-kjbox-secret"}  # decide_kjbox_secret_access
    mounted = {ref.split(":", 1)[0] for ref in _kv(str(ci_main_deploy["--set-secrets"])).values()}
    assert mounted <= granted, f"no accessor grant for {sorted(mounted - granted)}"


def test_phase3b_secrets_mounted_unconditionally(ci_main_deploy: dict[str, Any]) -> None:
    secrets = _kv(str(ci_main_deploy["--set-secrets"]))
    assert secrets["GEMINI_API_KEY"] == "gemini-api-key:latest"
    assert secrets["METABRAINZ_CLIENT_ID"] == "metabrainz-client-id:latest"
    assert secrets["METABRAINZ_CLIENT_SECRET"] == "metabrainz-client-secret:latest"
    assert secrets["DECIDE_TOKEN_KEY"] == "decide-token-key:latest"
    assert "DECIDE_3B_SECRETS" not in CI.read_text() and "DECIDE_METABRAINZ" not in CI.read_text()


def test_main_plain_env_vars_match(pulumi_main: dict[str, Any], ci_main_deploy: dict[str, Any]) -> None:
    """CI's --set-env-vars replaces the API's plain env on every deploy: Pulumi must list the same
    names, and the same value wherever Pulumi's is a literal (others come from stack config)."""
    pulumi_envs = {e["name"]: e["value"] for e in _container(pulumi_main)["envs"] if "value" in e}
    ci_envs = _kv(str(ci_main_deploy["--set-env-vars"]))
    assert set(pulumi_envs) == set(ci_envs)
    for name, value in pulumi_envs.items():
        if isinstance(value, str):
            assert ci_envs[name] == value, name
    assert ci_envs["LISTENBRAINZ_OAUTH_ENABLED"] == "true"
    assert ci_envs["PUBLIC_API_BASE_URL"] == "https://decide.nomadkaraoke.com"


@pytest.fixture(scope="module")
def pulumi_lb_poll() -> dict[str, Any]:
    tree = _pulumi_tree()
    call = _resource_call(tree, "Job", "lb-poll-job")
    kw = _kwargs(call, _module_constants(tree))
    kw["_ignore_changes"] = _ignore_changes(call)
    return kw


def test_lb_poll_job_image_step_exists() -> None:
    for step in _ci_steps():
        if "gcloud run jobs update lb-poll" in step.get("run", ""):
            flags = _flags(_command_line(step["run"], "gcloud run jobs update lb-poll"))
            assert str(flags["--image"]).endswith(":EXPR")  # ...:${{ github.sha }}
            return
    raise AssertionError("no `gcloud run jobs update lb-poll` step in deploy-backend")


def test_lb_poll_job_shape(pulumi_lb_poll: dict[str, Any]) -> None:
    tmpl = pulumi_lb_poll["template"]["template"]
    (c,) = tmpl["containers"]
    assert c["commands"] == ["python", "-m", "backend.jobs.lb_poll"]
    assert "args" not in c
    assert tmpl["timeout"] == "1800s"
    assert tmpl["max_retries"] == 0
    assert "template.template.containers[0].image" in pulumi_lb_poll["_ignore_changes"]
    plain = {e["name"]: e["value"] for e in c["envs"] if isinstance(e, dict) and "value" in e}
    assert plain["LISTENBRAINZ_OAUTH_ENABLED"] == "true"
    assert {"ENVIRONMENT", "GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_PROJECT_NUMBER"} <= set(plain)
    consts = _module_constants(_pulumi_tree())
    assert consts["LB_POLL_SECRETS"] == {
        "DECIDE_TOKEN_KEY": "decide-token-key",
        "METABRAINZ_CLIENT_ID": "metabrainz-client-id",
        "METABRAINZ_CLIENT_SECRET": "metabrainz-client-secret",
    }


def test_lb_poll_is_scheduled_daily() -> None:
    tree = _pulumi_tree()
    sched = _kwargs(_resource_call(tree, "Job", "lb-poll-scheduler"), _module_constants(tree))
    assert sched["schedule"] == "30 6 * * *" and sched["time_zone"] == "UTC"
    assert sched["http_target"]["http_method"] == "POST"
    invoker = _kwargs(_resource_call(tree, "JobIamMember", "lb-poll-scheduler-invoker"), _module_constants(tree))
    assert invoker["role"] == "roles/run.invoker"


# The vocal-range-gen job (on demand, no scheduler)
# --------------------------------------------------------------------------


def test_vocal_range_job_shape() -> None:
    tree = _pulumi_tree()
    call = _resource_call(tree, "Job", "vocal-range-gen-job")
    kw = _kwargs(call, _module_constants(tree))
    assert kw["name"] == "vocal-range-gen"
    assert kw["template"]["task_count"] == 10
    tmpl = kw["template"]["template"]
    (c,) = tmpl["containers"]
    assert ast.unparse(c["image"]).rstrip("'").endswith("/karaoke-decide-vocal:latest")
    assert c["commands"] == ["python", "-m", "karaoke_decide.etl.vocal_range_gen"]
    assert c["resources"]["limits"] == {"cpu": "2", "memory": "4Gi"}
    assert tmpl["timeout"] == "21600s"
    assert tmpl["max_retries"] == 0
    assert "template.template.containers[0].image" in _ignore_changes(call)
    # on demand: nothing schedules it
    assert not re.search(r'Job\(\s*"vocal-range-gen-scheduler', PULUMI.read_text())


def test_vocal_range_job_image_is_pinned_by_ci() -> None:
    steps = [s for s in _ci_steps() if "gcloud run jobs update vocal-range-gen" in s.get("run", "")]
    assert len(steps) == 1
    flags = _flags(_command_line(steps[0]["run"], "gcloud run jobs update vocal-range-gen"))
    assert flags["--image"].endswith("/karaoke-decide-vocal:EXPR")
    assert "::warning::" in steps[0]["run"]  # skipped, not failed, until Pulumi creates the job
    build = [s for s in _ci_steps() if "Dockerfile.vocal-analysis" in s.get("run", "")]
    assert len(build) == 1 and "karaoke-decide-vocal" in build[0]["run"]


def test_vocal_range_pulumi_image_tag_is_pushed_by_ci() -> None:
    tree = _pulumi_tree()
    c = _kwargs(_resource_call(tree, "Job", "vocal-range-gen-job"), _module_constants(tree))["template"]["template"][
        "containers"
    ][0]
    tag = ast.unparse(c["image"]).rstrip("'").rsplit(":", 1)[1]
    (build,) = [s for s in _ci_steps() if "Dockerfile.vocal-analysis" in s.get("run", "")]
    assert f"-t $VOCAL_IMAGE:{tag}" in build["run"]
    assert build.get("continue-on-error") is True


def test_vocal_range_bucket_grant_has_list_clause() -> None:
    tree = _pulumi_tree()
    kw = _kwargs(
        _resource_call(tree, "BucketIAMMember", "vocal-range-gen-musicbrainz-data-writer"), _module_constants(tree)
    )
    expr = kw["condition"]["expression"]
    if not isinstance(expr, str):
        expr = ast.literal_eval(expr) if isinstance(expr, ast.Constant) else ast.unparse(expr)
    assert "objects/vocal-range/gen_measured/" in expr
    assert 'resource.type == "storage.googleapis.com/Bucket"' in expr
    # Google: objectListPrefix is only valid in Credential Access Boundaries, not IAM bindings
    assert "getAttribute" not in expr and "objectListPrefix" not in expr
    assert "matches(" not in expr


def test_song_index_build_reads_vocal_range_by_name() -> None:
    """song-index-build reads vocal-range/latest.json + the two TSVs by name (no list), so
    its grant is objectViewer on the musicbrainz bucket, conditioned on the object prefix."""
    tree = _pulumi_tree()
    kw = _kwargs(
        _resource_call(tree, "BucketIAMMember", "song-index-build-vocal-range-reader"), _module_constants(tree)
    )
    assert kw["role"] == "roles/storage.objectViewer"
    bucket = kw["bucket"] if isinstance(kw["bucket"], str) else ast.unparse(kw["bucket"])
    assert "musicbrainz" in bucket.lower()
    expr = kw["condition"]["expression"]
    if not isinstance(expr, str):
        expr = ast.literal_eval(expr) if isinstance(expr, ast.Constant) else ast.unparse(expr)
    # built from the bucket resource's name, not a hardcoded bucket string
    assert "musicbrainz_bucket.name" in expr and "/objects/vocal-range/" in expr
    # narrowed: pointer + latest/ and vocal-range/runs/ only. IAM conditions have no regex.
    assert "matches(" not in expr
    assert '/objects/vocal-range/latest")' in expr
    assert '/objects/vocal-range/runs/")' in expr
    assert expr.count("startsWith(") == 2
    assert "ultrastar" not in expr and "gen_measured" not in expr
    assert "startsWith(\"projects/_/buckets/', musicbrainz_bucket.name, '/objects/vocal-range/\")" not in expr
    assert "nomadkaraoke-musicbrainz-data" not in expr
    assert "objectListPrefix" not in expr  # by-name reads only

    from karaoke_decide.etl import song_index_build as sib

    assert sib.VOCAL_RANGE_BUCKET == "nomadkaraoke-musicbrainz-data"
    for name in (sib.VOCAL_RANGE_POINTER, sib.VOCAL_RANGE_SONG_TSV, sib.VOCAL_RANGE_ARTIST_TSV):
        assert name.startswith("vocal-range/")
    # by name: the job never lists that bucket
    assert "list_names(VOCAL_RANGE_BUCKET" not in Path(sib.__file__).read_text()


# --------------------------------------------------------------------------
# Voice pool (Phase 3d): three jobs, the paused hourly scheduler, IAM, recordings bucket
# --------------------------------------------------------------------------

VOICE_POOL_JOBS = {
    # resource name: (job name, args, cpu, memory, timeout, image repo)
    "voice-pool-select-job": ("voice-pool-select", ["select"], "2", "4Gi", "3600s", "karaoke-decide"),
    "voice-pool-fetch-job": ("voice-pool-fetch", ["fetch"], "1", "1Gi", "3900s", "karaoke-decide"),
    "voice-pool-analyse-job": ("voice-pool-analyse", ["analyse"], "4", "8Gi", "21600s", "karaoke-decide-vocal"),
}


def _expr(kw: dict[str, Any]) -> str:
    expr = kw["condition"]["expression"]
    return expr if isinstance(expr, str) else ast.unparse(expr)


@pytest.mark.parametrize("resource", sorted(VOICE_POOL_JOBS))
def test_voice_pool_job_shape(resource: str) -> None:
    name, args, cpu, memory, timeout, repo = VOICE_POOL_JOBS[resource]
    tree = _pulumi_tree()
    call = _resource_call(tree, "Job", resource)
    kw = _kwargs(call, _module_constants(tree))
    assert kw["name"] == name
    assert kw["location"].id == "region"  # us-central1, from stack config
    tmpl = kw["template"]["template"]
    (c,) = tmpl["containers"]
    assert c["commands"] == ["python", "-m", "karaoke_decide.voice_pool"]
    assert c["args"] == args
    assert c["resources"]["limits"] == {"cpu": cpu, "memory": memory}
    assert ast.unparse(c["image"]).rstrip("'").endswith(f"/karaoke-repo/{repo}:latest")
    assert ast.unparse(tmpl["service_account"]) == "voice_pool_sa.email"
    assert tmpl["timeout"] == timeout
    assert tmpl["max_retries"] == 0
    assert "template.template.containers[0].image" in _ignore_changes(call)


def test_voice_pool_fetch_outlasts_its_deadline_and_reads_flacfetch_secrets() -> None:
    from karaoke_decide.voice_pool.cli import build_parser

    deadline = build_parser().parse_args(["fetch"]).deadline_s
    assert int(VOICE_POOL_JOBS["voice-pool-fetch-job"][4].rstrip("s")) >= deadline + 600
    consts = _module_constants(_pulumi_tree())
    assert consts["VOICE_POOL_SECRETS"] == {
        "FLACFETCH_API_URL": "flacfetch-api-url",
        "FLACFETCH_API_KEY": "flacfetch-api-key",
    }
    assert "flacfetch-api-url" not in str(consts.get("REQUIRED_SECRETS"))  # the API doesn't mount them


def test_voice_pool_analyse_is_one_task() -> None:
    tree = _pulumi_tree()
    kw = _kwargs(_resource_call(tree, "Job", "voice-pool-analyse-job"), _module_constants(tree))
    assert kw["template"]["task_count"] == 1


def test_voice_pool_fetch_scheduler_is_hourly_and_created_paused() -> None:
    tree = _pulumi_tree()
    call = _resource_call(tree, "Job", "voice-pool-fetch-scheduler")
    sched = _kwargs(call, _module_constants(tree))
    assert sched["schedule"] == "7 * * * *" and sched["time_zone"] == "UTC"
    assert sched["paused"] is True
    assert "paused" in _ignore_changes(call)  # a later `pulumi up` never re-pauses a resumed scheduler
    assert sched["http_target"]["http_method"] == "POST"
    invoker = _kwargs(
        _resource_call(tree, "JobIamMember", "voice-pool-fetch-scheduler-invoker"), _module_constants(tree)
    )
    assert invoker["role"] == "roles/run.invoker"
    # on demand: nothing schedules select or analyse
    text = PULUMI.read_text()
    assert not re.search(r'Job\(\s*"voice-pool-(select|analyse)-scheduler', text)


def test_voice_pool_can_start_and_watch_separation_and_analysis() -> None:
    from karaoke_decide.voice_pool import fetch, separate

    tree = _pulumi_tree()
    consts = _module_constants(tree)
    sep = f"projects/nomadkaraoke/locations/{separate.SEPARATE_REGION}/jobs/{separate.SEPARATE_JOB}"
    assert consts["VOICE_POOL_SEPARATE_JOB"] == sep
    for resource, role in [
        ("voice-pool-separate-executor", "roles/run.jobsExecutorWithOverrides"),
        ("voice-pool-separate-viewer", "roles/run.viewer"),
    ]:
        kw = _kwargs(_resource_call(tree, "JobIamMember", resource), consts)
        assert kw["role"] == role and kw["name"] == sep and kw["location"] == separate.SEPARATE_REGION
    assert fetch.ANALYSE_JOB == "voice-pool-analyse" and fetch.ANALYSE_REGION == "us-central1"
    for resource, role in [
        ("voice-pool-analyse-executor", "roles/run.jobsExecutorWithOverrides"),
        ("voice-pool-analyse-viewer", "roles/run.viewer"),
    ]:
        kw = _kwargs(_resource_call(tree, "JobIamMember", resource), consts)
        assert kw["role"] == role and ast.unparse(kw["name"]) == "voice_pool_analyse_job.name"


def test_voice_pool_bucket_grants_are_prefix_scoped() -> None:
    from karaoke_decide.etl.vocal_range_refresh import POOL_MEASURED_PREFIX
    from karaoke_decide.voice_pool.voicedb import VOICE_BUCKET, VOICE_PREFIX

    tree = _pulumi_tree()
    consts = _module_constants(tree)
    gen = _kwargs(_resource_call(tree, "BucketIAMMember", "voice-pool-gen-storage-writer"), consts)
    assert gen["role"] == "roles/storage.objectAdmin" and gen["bucket"] == "karaoke-gen-storage-nomadkaraoke"
    expr = _expr(gen)
    assert "/objects/voice-pool/" in expr and 'resource.type == "storage.googleapis.com/Bucket"' in expr
    data = _kwargs(_resource_call(tree, "BucketIAMMember", "voice-pool-musicbrainz-data-writer"), consts)
    assert data["role"] == "roles/storage.objectAdmin" and data["bucket"] == VOICE_BUCKET
    expr = _expr(data)
    assert f"/objects/{VOICE_PREFIX}/" in expr and f"/objects/{POOL_MEASURED_PREFIX}" in expr
    assert expr.count("startsWith(") == 2
    for e in (_expr(gen), expr):
        assert "matches(" not in e and "objectListPrefix" not in e
    # objectViewer (catalog.db, availability.db, the UltraStar zip; and listing) from a loop.
    text = PULUMI.read_text()
    loop = text[text.index('f"voice-pool-{_name}-reader"') - 200 :]
    assert '("musicbrainz-data", MUSICBRAINZ_DATA_BUCKET), ("kn-data", KN_DATA_BUCKET)' in loop[:200]
    assert 'role="roles/storage.objectViewer"' in loop[:500]


def test_voice_recordings_bucket_is_private_and_kept() -> None:
    tree = _pulumi_tree()
    consts = _module_constants(tree)
    kw = _kwargs(_resource_call(tree, "Bucket", "voice-recordings-bucket"), consts)
    assert kw["name"] == "nomadkaraoke-voice-recordings"
    assert kw["uniform_bucket_level_access"] is True
    assert kw["public_access_prevention"] == "enforced"
    assert kw["versioning"] == {"enabled": False}
    # Privacy: a user's delete must really erase the audio (no 7-day soft-delete copy).
    assert kw["soft_delete_policy"] == {"retention_duration_seconds": 0}
    assert "lifecycle_rules" not in kw
    admin = _kwargs(_resource_call(tree, "BucketIAMMember", "voice-recordings-api-admin"), consts)
    assert admin["role"] == "roles/storage.objectAdmin"


def test_main_api_has_voice_env(pulumi_main: dict[str, Any], ci_main_deploy: dict[str, Any]) -> None:
    envs = _kv(str(ci_main_deploy["--set-env-vars"]))
    assert envs["VOICE_DATA_BUCKET"] == "nomadkaraoke-musicbrainz-data"
    assert envs["VOICE_RECORDINGS_BUCKET"] == "nomadkaraoke-voice-recordings"
    assert _kv(str(ci_main_deploy["--set-secrets"]))["ADMIN_TOKENS"] == "admin-tokens:latest"
    assert _secret_envs(_container(pulumi_main))["ADMIN_TOKENS"] == "admin-tokens:latest"


@pytest.mark.parametrize(
    ("job", "repo"),
    [
        ("voice-pool-select", "karaoke-decide"),
        ("voice-pool-fetch", "karaoke-decide"),
        ("voice-pool-analyse", "karaoke-decide-vocal"),
    ],
)
def test_voice_pool_job_images_are_pinned_by_ci(job: str, repo: str) -> None:
    steps = [s for s in _ci_steps() if f"gcloud run jobs update {job}" in s.get("run", "")]
    assert len(steps) == 1
    flags = _flags(_command_line(steps[0]["run"], f"gcloud run jobs update {job}"))
    image = str(flags["--image"])
    assert image.endswith(":EXPR")  # ...:${{ github.sha }}
    if repo == "karaoke-decide-vocal":
        assert image.endswith("/karaoke-decide-vocal:EXPR")
        assert steps[0].get("continue-on-error") is True
        assert steps[0]["if"] == "steps.vocal_build.outcome == 'success'"
    else:
        assert "karaoke-decide-vocal" not in image
    assert f"::warning::{job} does not exist yet" in steps[0]["run"]  # skipped, not failed, until Pulumi
    # Non-blocking: a describe/update failure can't skip the later search deploy + verify steps.
    assert steps[0].get("continue-on-error") is True
    step_id = steps[0]["id"]
    warns = [s for s in _ci_steps() if f"steps.{step_id}.outcome == 'failure'" in str(s.get("if", ""))]
    assert len(warns) == 1 and "::warning::" in warns[0]["run"]


def test_steps_after_the_voice_pool_updates_still_run() -> None:
    names = [s.get("name", "") for s in _ci_steps()]
    last_voice = max(i for i, n in enumerate(names) if n.startswith("Update voice-pool-"))
    assert names.index("Deploy search service") < last_voice < names.index("Verify deployment")
