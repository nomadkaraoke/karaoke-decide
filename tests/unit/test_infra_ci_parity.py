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
    assert "decide-kjbox-secret" in _module_constants(tree)["REQUIRED_SECRETS"]


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
