"""OpenShift manifests (M5 Task 25): kustomize base, components, dev and prod overlays.

Renders ``deploy/openshift/overlays/{dev,prod}`` and the worked release example with the
``kustomize`` binary and checks the objects: every role, the start order (each DPS Deployment
waits in an init container for the release's schema), pod security for OpenShift's
``restricted-v2`` SCC (arbitrary UID, read-only root), digests, secrets only as mounted files, the
production posture guard per role, network policies, monitoring, and the Wega placeholder pattern
(committed overlays carry placeholders that only *warn*; ``tools/openshift_check.py
--fail-on-warn`` refuses a release overlay that still carries one).
"""

from __future__ import annotations

import copy
import importlib
import importlib.util
import inspect
import ipaddress
import os
import re
from collections.abc import Sequence
from functools import cache
from pathlib import Path
from typing import Any

import pytest
import yaml

from dps.config import Settings
from tools import openshift_check as oc

pytestmark = pytest.mark.deploy

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy" / "openshift"
README = DEPLOY / "README.md"
RELEASE_EXAMPLE = DEPLOY / "release-example"
OVERLAYS = ("dev", "prod")
ROLES = ("dps-app", "dps-worker", "dps-browser-worker")
MAINTAIN = "dps-migrate-maintain"  # the audit-partition CronJob (S5 review r1, B1)
SCHEMA_WAIT = "wait-for-schema"  # init container of each DPS Deployment (S5 review r2, B1)
# Retries the verify until it passes (5 s apart); the trap ends the wait at once on SIGTERM (a shell
# as PID 1 ignores it otherwise and the pod hangs for its grace period when it is deleted).
SCHEMA_WAIT_ARGS = [
    "sh",
    "-c",
    "trap 'exit 143' TERM INT; until python -m dps.migrate --verify; do sleep 5 & wait $!; done",
]
KUSTOMIZE = oc.find_kustomize()


@cache
def _render(path: str) -> tuple[dict[str, Any], ...]:
    return tuple(oc.render(Path(path), KUSTOMIZE))


def render(target: str) -> list[dict[str, Any]]:
    """Rendered objects of an overlay (``dev``/``prod``) or ``release-example`` (deep copies)."""
    if KUSTOMIZE is None:
        pytest.skip("needs the kustomize binary (marker deploy)")
    path = DEPLOY / "overlays" / target if target in OVERLAYS else DEPLOY / target
    return copy.deepcopy(list(_render(str(path))))


def one(docs: list[dict[str, Any]], kind: str, name: str) -> dict[str, Any]:
    found = [d for d in docs if d["kind"] == kind and d["metadata"]["name"] == name]
    assert len(found) == 1, (kind, name, len(found))
    return found[0]


def pod_of(docs: list[dict[str, Any]], name: str) -> dict[str, Any]:
    return next(pod for _, n, pod in oc.workloads(docs) if n == name)


def main_container(pod: dict[str, Any]) -> dict[str, Any]:
    containers: list[dict[str, Any]] = pod["containers"]
    assert len(containers) == 1
    return containers[0]


def schema_wait(pod: dict[str, Any]) -> dict[str, Any]:
    """The pod's only init container, which must be the schema wait."""
    inits: list[dict[str, Any]] = pod.get("initContainers", [])
    assert [c["name"] for c in inits] == [SCHEMA_WAIT]
    return inits[0]


def dps_containers(pod: dict[str, Any]) -> list[dict[str, Any]]:
    """Every container (init containers included) that takes the ``dps`` ConfigMap."""
    return [c for c in oc.containers(pod) if oc.reads_dps_configmap(c)]


def env_of(container: dict[str, Any]) -> dict[str, str]:
    return {e["name"]: e.get("value", "") for e in container.get("env", []) if "value" in e}


def quantity(value: str | int) -> float:
    """A Kubernetes cpu or memory quantity as a number (``500m`` → 0.5, ``2Gi`` → 2 * 1024**3)."""
    text = str(value)
    units = {"m": 1e-3, "Ki": 1024.0, "Mi": 1024.0**2, "Gi": 1024.0**3}
    for suffix, factor in units.items():
        if text.endswith(suffix):
            return float(text.removesuffix(suffix)) * factor
    return float(text)


def secret_volumes(pod: dict[str, Any]) -> set[str]:
    return {v["secret"]["secretName"] for v in pod.get("volumes", []) if "secret" in v}


def image_digest(container: dict[str, Any]) -> str:
    """The ``sha256:…`` part of a container's digest-pinned image."""
    return str(container["image"]).split("@", 1)[1]


def release_overlay(tmp_path: Path, *, derive: bool = True, literals: Sequence[str] = ()) -> Path:
    """The release example with values a bank would set, over ``overlays/prod``, in ``tmp_path``.

    No ``.example`` host, TEST-NET CIDR or example digest is left (app image ``a…a``, browser
    image ``b…b``), so the gate's only findings are the ones a test provokes. ``derive=False``
    drops the example's ``replacements`` (the digest settings then stay what the layers below set);
    ``literals`` are merged into the ``dps`` ConfigMap.
    """
    text = (RELEASE_EXAMPLE / "kustomization.yaml").read_text("utf-8")
    for old, new in (
        ("bank.example", "corp.internal"),
        ("198.51.100.", "10.20.30."),
        ("1" * 64, "a" * 64),
        ("2" * 64, "b" * 64),
    ):
        text = text.replace(old, new)
    spec = yaml.safe_load(text)
    # kustomize refuses an absolute path as a kustomization root: refer to the overlay relatively
    spec["resources"] = [os.path.relpath(DEPLOY / "overlays" / "prod", tmp_path)]
    if not derive:
        del spec["replacements"]
    spec["configMapGenerator"][0]["literals"].extend(literals)
    (tmp_path / "kustomization.yaml").write_text(yaml.safe_dump(spec, sort_keys=False), "utf-8")
    return tmp_path


def stamped_digests(docs: list[dict[str, Any]]) -> dict[str, str]:
    """``DPS_IMAGE_DIGEST`` each DPS container starts with (ConfigMap, then its own env), keyed
    ``<workload>/<container>``; init containers included."""
    out: dict[str, str] = {}
    for _, name, pod in oc.workloads(docs):
        for container in dps_containers(pod):
            digest = oc.pod_environment(docs, container).get("DPS_IMAGE_DIGEST", "")
            out[f"{name}/{container['name']}"] = digest
    return out


# --- roles -------------------------------------------------------------------------------------


@pytest.mark.parametrize("overlay", OVERLAYS)
def test_overlays_render_every_role(overlay: str) -> None:
    docs = render(overlay)
    names = {(d["kind"], d["metadata"]["name"]) for d in docs}
    assert {("Deployment", n) for n in ROLES} <= names
    assert ("Job", "dps-migrate-0") in names
    assert ("CronJob", MAINTAIN) in names
    assert one(docs, "Service", "dps-app")["spec"]["type"] == "ClusterIP"
    assert one(docs, "Service", "dps-db")["spec"]["type"] == "ExternalName"
    route = one(docs, "Route", "dps-app")
    assert route["spec"]["tls"] == {
        "termination": "edge",
        "insecureEdgeTerminationPolicy": "Redirect",
    }
    assert route["spec"]["to"]["name"] == "dps-app"
    accounts = {d["metadata"]["name"] for d in docs if d["kind"] == "ServiceAccount"}
    assert accounts == {"dps-app", "dps-worker", "dps-browser-worker", "dps-migrate"}
    assert ("ConfigMap", "dps") in names
    assert any(d["kind"] == "PodMonitor" for d in docs)
    assert any(d["kind"] == "PrometheusRule" for d in docs)
    assert ("NetworkPolicy", "default-deny") in names
    assert {d["metadata"].get("namespace") for d in docs} == {f"dps-{overlay}"}

    # One image, four roles (spec §4); the browser worker has its own image (contract §11.1).
    commands = {
        name: main_container(pod_of(docs, name))["args"]
        for name in (*ROLES, "dps-migrate-0", MAINTAIN)
    }
    assert commands["dps-app"][:3] == ["uvicorn", "dps.app:create_app", "--factory"]
    # uvicorn's access log is stdlib logging and bypasses the structlog allow-list (Dockerfile CMD).
    assert "--no-access-log" in commands["dps-app"]
    assert commands["dps-worker"] == ["python", "-m", "dps.worker"]
    assert commands["dps-browser-worker"] == ["python", "-m", "dps.browser_worker"]
    assert commands["dps-migrate-0"] == ["python", "-m", "dps.migrate"]
    assert commands[MAINTAIN] == ["python", "-m", "dps.migrate", "--maintain"]
    for name in ROLES:
        image = main_container(pod_of(docs, name))["image"]
        repo = image.split("@")[0].rsplit("/", 1)[-1]
        expected = (
            "data-privacy-services-browser"
            if name == "dps-browser-worker"
            else "data-privacy-services"
        )
        assert repo == expected, (name, image)
        assert pod_of(docs, name)["serviceAccountName"] == name
    assert pod_of(docs, "dps-migrate-0")["serviceAccountName"] == "dps-migrate"
    assert pod_of(docs, MAINTAIN)["serviceAccountName"] == "dps-migrate"

    replicas = {n: one(docs, "Deployment", n)["spec"]["replicas"] for n in ROLES}
    assert replicas == (
        {"dps-app": 1, "dps-worker": 1, "dps-browser-worker": 1}
        if overlay == "dev"
        else {"dps-app": 3, "dps-worker": 2, "dps-browser-worker": 1}
    )


def test_every_role_runs_a_module_the_package_has() -> None:
    for module in ("dps.app", "dps.worker", "dps.migrate"):
        assert importlib.util.find_spec(module) is not None, module
    if importlib.util.find_spec("dps.browser_worker") is None:
        pytest.skip("dps.browser_worker arrives with the M4 merge (contract §3.5)")


# --- pod security (restricted-v2: arbitrary UID, read-only root) --------------------------------


@pytest.mark.parametrize("overlay", OVERLAYS)
def test_pod_security_everywhere(overlay: str) -> None:
    docs = render(overlay)
    workloads = oc.workloads(docs)
    assert len(workloads) >= 4
    for kind, name, pod in workloads:
        sc = pod.get("securityContext", {})
        assert sc.get("runAsNonRoot") is True, name
        assert sc.get("seccompProfile", {}).get("type") == "RuntimeDefault", name
        assert "runAsUser" not in sc, name  # OpenShift assigns the namespace's arbitrary UID
        assert pod.get("automountServiceAccountToken") is False, name
        # Services are named dps-*: service links would inject DPS_* variables Settings reads
        assert pod.get("enableServiceLinks") is False, name
        assert not pod.get("hostNetwork") and not pod.get("hostPID") and not pod.get("hostIPC")
        assert not any("hostPath" in v for v in pod.get("volumes", [])), name
        for c in oc.containers(pod):
            csc = c.get("securityContext", {})
            assert csc.get("allowPrivilegeEscalation") is False, (name, c["name"])
            assert csc.get("readOnlyRootFilesystem") is True, (name, c["name"])
            assert csc.get("capabilities", {}).get("drop") == ["ALL"], (name, c["name"])
            assert not csc.get("capabilities", {}).get("add"), (name, c["name"])
            assert "runAsUser" not in csc and not csc.get("privileged"), (name, c["name"])
            res = c.get("resources", {})
            for part in ("requests", "limits"):
                assert {"cpu", "memory"} <= set(res.get(part, {})), (name, c["name"], part)
            # A read-only root needs a writable /tmp; HOME points there (no passwd entry for the UID).
            mounts = {m["mountPath"] for m in c.get("volumeMounts", [])}
            assert "/tmp" in mounts, (name, c["name"])
            assert env_of(c).get("HOME") == "/tmp", (name, c["name"])
            if kind == "Deployment" and c in pod["containers"]:
                assert c.get("livenessProbe") and c.get("readinessProbe"), (name, c["name"])
            if c in pod.get("initContainers", []):
                # the API server refuses probes on an init container (it is not a sidecar)
                assert not {"livenessProbe", "readinessProbe", "startupProbe"} & set(c), name
                assert "restartPolicy" not in c, name
        for volume in pod.get("volumes", []):
            if "secret" in volume:
                assert volume["secret"].get("defaultMode") == 0o440, (name, volume["name"])
            if "emptyDir" in volume:
                assert volume["emptyDir"].get("sizeLimit"), (name, volume["name"])
    for account in (d for d in docs if d["kind"] == "ServiceAccount"):
        assert account.get("automountServiceAccountToken") is False, account["metadata"]["name"]


@pytest.mark.parametrize("overlay", OVERLAYS)
def test_images_are_pinned_by_digest(overlay: str) -> None:
    for _, name, pod in oc.workloads(render(overlay)):
        for c in oc.containers(pod):
            assert "@sha256:" in c["image"], (name, c["image"])


# --- secrets ------------------------------------------------------------------------------------


@pytest.mark.parametrize("overlay", OVERLAYS)
def test_no_literal_secrets_and_every_secret_is_a_mounted_file(overlay: str) -> None:
    docs = render(overlay)
    for key in oc.configmap_data(docs, "dps"):
        assert not oc.is_secret_setting(key), key
    for _, name, pod in oc.workloads(docs):
        volumes = {v["name"]: v for v in pod.get("volumes", [])}
        for c in oc.containers(pod):
            assert not any("secretRef" in src for src in c.get("envFrom", [])), (name, c["name"])
            mounts = [(m["mountPath"], volumes[m["name"]]) for m in c.get("volumeMounts", [])]
            for entry in c.get("env", []):
                env_name = entry["name"]
                if "value" in entry:
                    assert not oc.is_secret_setting(env_name), (name, env_name)
                if env_name.startswith("DPS_"):
                    # posture (M5 Task 16): a secret is a file, never an environment variable
                    assert "secretKeyRef" not in entry.get("valueFrom", {}), (name, env_name)
                if env_name.endswith("_FILE"):
                    path = entry["value"]
                    owners = [v for mp, v in mounts if path.startswith(mp.rstrip("/") + "/")]
                    assert owners, (name, env_name, path)
                    assert all("secret" in v or "configMap" in v for v in owners), (name, env_name)


@pytest.mark.parametrize("overlay", OVERLAYS)
def test_every_dps_variable_is_a_setting(overlay: str) -> None:
    """A typo in a variable name is silently ignored by Settings (``extra="ignore"``)."""
    fields = set(Settings.model_fields)
    docs = render(overlay)
    cm = oc.configmap_data(docs, "dps")
    names = {k for k in cm if k.startswith("DPS_")}
    # the one library variable: botocore's CA bundle (S3), which DPS_SSL_CERT_FILE does not reach
    assert set(cm) - names == {"AWS_CA_BUNDLE"}
    for _, _, pod in oc.workloads(docs):
        for c in oc.containers(pod):
            names |= {e["name"] for e in c.get("env", []) if e["name"].startswith("DPS_")}
    for env_name in names:
        field = env_name.removeprefix("DPS_").lower()
        field = field.removesuffix("_file") if field.removesuffix("_file") in fields else field
        assert field in fields, env_name


def test_secret_settings_cover_the_posture_guard() -> None:
    """The checker's secret names are the guard's (S5 review r1, N4): not only a suffix regex."""
    from dps.posture import SECRET_ENV

    assert set(SECRET_ENV) <= oc.SECRET_SETTINGS
    docs = render("prod")
    one(docs, "ConfigMap", "dps")["data"]["DPS_COIN_CLIENT_ID"] = "x"
    main_container(pod_of(docs, "dps-worker"))["env"] += [
        {"name": "DPS_S3_ACCESS_KEY_ID", "value": "x"},
        {"name": "DPS_JOB_KEK_PREVIOUS", "value": "x"},
    ]
    denied = [f for f in oc.check(docs, settings=False) if f.rule == "literal-secret"]
    assert all(f.level == "deny" for f in denied)
    for name in ("DPS_COIN_CLIENT_ID", "DPS_S3_ACCESS_KEY_ID", "DPS_JOB_KEK_PREVIOUS"):
        assert any(f.message.startswith(name + " ") for f in denied), name


def test_the_release_check_denies_library_secrets_in_the_configmap_and_the_env() -> None:
    """S5 review r2, N2: botocore and libpq read these names from the environment, and the
    ``dps`` ConfigMap already carries a key that is not ``DPS_*`` (``AWS_CA_BUNDLE``)."""
    docs = render("prod")
    assert not [f for f in oc.check(docs, settings=False) if f.rule == "literal-secret"]
    data = one(docs, "ConfigMap", "dps")["data"]
    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "PGPASSWORD"):
        data[name] = "x"
    main_container(pod_of(docs, "dps-app"))["env"].append(
        {"name": "AWS_ACCESS_KEY_ID", "value": "x"}
    )
    denied = [f for f in oc.check(docs, settings=False) if f.rule == "literal-secret"]
    assert all(f.level == "deny" for f in denied)
    assert sorted((f.resource, f.message.split()[0]) for f in denied) == [
        ("ConfigMap/dps", "AWS_ACCESS_KEY_ID"),
        ("ConfigMap/dps", "AWS_SECRET_ACCESS_KEY"),
        ("ConfigMap/dps", "PGPASSWORD"),
        ("Deployment/dps-app container app", "AWS_ACCESS_KEY_ID"),
    ]
    assert not oc.is_secret_setting("AWS_CA_BUNDLE")  # a CA bundle path, not a secret


@pytest.mark.parametrize("overlay", OVERLAYS)
def test_the_s3_client_trusts_the_org_ca_like_every_other_egress(overlay: str) -> None:
    """botocore reads ``AWS_CA_BUNDLE``, not ``SSL_CERT_FILE`` nor ``DPS_SSL_CERT_FILE`` (N1)."""
    cm = oc.configmap_data(render(overlay), "dps")
    assert cm["AWS_CA_BUNDLE"] == cm["DPS_SSL_CERT_FILE"] == "/etc/dps/ca/org-ca.pem"


def test_only_the_migrate_workloads_mount_the_owner_secret() -> None:
    for overlay in OVERLAYS:
        docs = render(overlay)
        owners = {n for _, n, pod in oc.workloads(docs) if "dps-pg-owner" in secret_volumes(pod)}
        assert owners == {"dps-migrate-0", MAINTAIN}, overlay
        migrate = pod_of(docs, "dps-migrate-0")
        container = main_container(migrate)
        assert container["args"] == ["python", "-m", "dps.migrate"]
        assert env_of(container)["DPS_MIGRATIONS_MODE"] == "auto"
        assert migrate["restartPolicy"] == "Never"
        # least privilege: the DDL login and the CA bundle, nothing else (no KEK, no API keys)
        assert secret_volumes(migrate) == {"dps-pg-owner"}
        assert one(docs, "Job", "dps-migrate-0")["spec"]["backoffLimit"] == 6
        # the app and the workers only verify the schema
        assert oc.configmap_data(docs, "dps")["DPS_MIGRATIONS_MODE"] == "verify"


@pytest.mark.parametrize("target", [*OVERLAYS, "release-example"])
def test_a_daily_cronjob_keeps_the_audit_log_writable(target: str) -> None:
    """S5 review r1, B1: ``dps_audit_log`` has one partition per UTC month and no DEFAULT one.

    Only the owner login creates partitions — ``dps.migrate`` this month and the next three,
    ``--maintain`` the same and it also drops the months past ``DPS_AUDIT_RETENTION_MONTHS`` — and
    the migrate Job runs once per release, so without a schedule every audit write fails about four
    months after the last release.
    """
    docs = render(target)
    cron = one(docs, "CronJob", MAINTAIN)["spec"]
    # every day, so a failed run still has three months of partitions in hand; UTC like the months
    minute, hour, *days = cron["schedule"].split()
    assert days == ["*", "*", "*"] and minute.isdigit() and hour.isdigit(), cron["schedule"]
    assert cron["timeZone"] == "Etc/UTC"
    assert cron.get("suspend") is not True
    # one run at a time; each run bounded (with Forbid a hung run would block every later one); a
    # missed schedule still starts late; a short history of finished Jobs
    assert cron["concurrencyPolicy"] == "Forbid"
    job = cron["jobTemplate"]["spec"]
    assert 0 < job["activeDeadlineSeconds"] <= 3600
    assert 0 < job["backoffLimit"] <= 6
    assert cron["startingDeadlineSeconds"] > 0
    assert 1 <= cron["successfulJobsHistoryLimit"] <= 5
    assert 1 <= cron["failedJobsHistoryLimit"] <= 5
    # the migrate Job's pod with one more argument: the owner login and the CA bundle only, the
    # same account, security context, resources and labels (component=migrate: egress-dps-db)
    release_job = next(
        d for d in docs if d["kind"] == "Job" and d["metadata"]["name"].startswith("dps-migrate-")
    )
    expected = copy.deepcopy(release_job["spec"]["template"])
    expected["spec"]["containers"][0]["args"] = ["python", "-m", "dps.migrate", "--maintain"]
    assert job["template"] == expected
    assert secret_volumes(job["template"]["spec"]) == {"dps-pg-owner"}
    labels = job["template"]["metadata"]["labels"]
    assert labels["app.kubernetes.io/component"] == "migrate"
    db = one(docs, "NetworkPolicy", "egress-dps-db")
    assert _selected(db, "migrate")
    # a CronJob's template is mutable: its name is not a per-release placeholder like the Job's
    renamed = [f.resource for f in oc.placeholder_findings(docs) if f.rule == "placeholder-job"]
    assert f"CronJob/{MAINTAIN}" not in renamed


def test_the_release_check_keeps_the_owner_login_in_the_migrate_workloads() -> None:
    docs = render("prod")
    # the migrate Job and the maintenance CronJob pass (a run-to-completion pod has no probes)
    assert not [f for f in oc.check(docs, settings=False) if f.rule in ("owner-secret", "probes")]
    one(docs, "CronJob", MAINTAIN)["metadata"]["name"] = "dps-reports"
    pod_of(docs, "dps-worker")["volumes"].append(
        {"name": "pg-owner", "secret": {"secretName": "dps-pg-owner", "defaultMode": 288}}
    )
    denied = [f for f in oc.check(docs, settings=False) if f.rule == "owner-secret"]
    assert {f.resource for f in denied} == {"CronJob/dps-reports", "Deployment/dps-worker"}
    assert all(f.level == "deny" for f in denied)


def test_each_role_mounts_only_the_secrets_it_uses() -> None:
    docs = render("prod")
    common = {"dps-job-kek", "dps-api-keys", "dps-s3", "dps-connector-refs", "dps-pg-app"}
    engine = {"dps-coin", "dps-tokenizer", "dps-ocr"}
    assert secret_volumes(pod_of(docs, "dps-app")) == common | engine
    assert secret_volumes(pod_of(docs, "dps-worker")) == common | engine | {"dps-pg-worker"}
    # the scanner never calls the LLM, OCR or the tokenizer: those are switched off, not mounted
    browser = pod_of(docs, "dps-browser-worker")
    assert secret_volumes(browser) == common | {"dps-pg-worker"}
    env = env_of(main_container(browser))
    assert env["DPS_LLM_PROVIDER"] == "" and env["DPS_TOKENIZER_PROVIDER"] == ""
    assert env["DPS_OCR_PROVIDER"] == "off"
    # Chromium needs a real /dev/shm
    dshm = next(v for v in browser["volumes"] if v["name"] == "dshm")
    assert dshm["emptyDir"]["medium"] == "Memory"
    # pvc file targets: the worker writes them, the app's connection test puts a probe object there
    claims = {
        n: {
            v["persistentVolumeClaim"]["claimName"]
            for v in pod["volumes"]
            if "persistentVolumeClaim" in v
        }
        for _, n, pod in oc.workloads(docs)
    }
    assert {n for n, c in claims.items() if c} == {"dps-app", "dps-worker"}
    assert claims["dps-app"] == claims["dps-worker"] == {"dps-exports"}
    for name in ("dps-app", "dps-worker"):
        mounts = {
            m["name"]: m["mountPath"] for m in main_container(pod_of(docs, name))["volumeMounts"]
        }
        assert mounts["exports"] == oc.configmap_data(docs, "dps")["DPS_EXPORT_ROOT"]


def _opens(function: object) -> set[str]:
    """The metadata-DB logins a process start-up opens: ``app`` (db.init_pool), ``worker``."""
    source = inspect.getsource(function)  # type: ignore[arg-type]
    opened = {"app"} if re.search(r"\binit_pool\(", source) else set()
    return opened | ({"worker"} if "init_worker_pool(" in source else set())


def test_every_role_has_the_logins_its_process_opens() -> None:
    """Both workers open the API pool too (``db.init_pool``, found by the runtime proof of S5)."""
    from dps import worker

    docs = render("prod")
    starts: dict[str, object] = {"dps-worker": worker._amain}
    if importlib.util.find_spec("dps.browser_worker") is not None:
        browser_worker = importlib.import_module("dps.browser_worker")
        starts["dps-browser-worker"] = browser_worker.amain
    files = {"app": "DPS_PG_PASSWORD_FILE", "worker": "DPS_PG_WORKER_PASSWORD_FILE"}
    for role, start in starts.items():
        opened = _opens(start)
        assert opened, role
        env = env_of(main_container(pod_of(docs, role)))
        for login in opened:
            assert files[login] in env, (role, login)
    assert "DPS_PG_PASSWORD_FILE" in env_of(main_container(pod_of(docs, "dps-app")))
    # each login is its own file: the app never holds the worker's, nobody but migrate the owner's
    assert "DPS_PG_WORKER_PASSWORD_FILE" not in env_of(main_container(pod_of(docs, "dps-app")))


# --- start order: the release's schema before the processes (S5 review r2, B1) ---------------------


@pytest.mark.parametrize("target", [*OVERLAYS, "release-example"])
def test_every_dps_deployment_waits_for_the_release_schema(target: str) -> None:
    """S5 review r2, B1: compose starts app and workers only after its ``migrate`` one-shot has
    completed (``service_completed_successfully``); Kubernetes has no such order, and a release
    applies the migrate Job and the Deployments together. An app pod that started before the Job
    committed verified the schema once, kept its job platform down (every ``require_db`` route 503)
    and stayed Ready; the workers exited on ``MigrationDrift`` and crash-looped. So each Deployment
    that runs DPS first waits in an init container until ``python -m dps.migrate --verify`` passes,
    with the image it runs (the migrations that image ships) and the login that check opens.
    """
    docs = render(target)
    deployments = {n: pod for k, n, pod in oc.workloads(docs) if k == "Deployment"}
    runs_dps = {n for n, pod in deployments.items() if dps_containers(pod)}
    assert runs_dps == set(ROLES)
    # the dev mocks are not DPS processes; the migrate Job and CronJob apply what the wait awaits
    assert {n for n, pod in deployments.items() if pod.get("initContainers")} == runs_dps
    others = [
        n for k, n, pod in oc.workloads(docs) if k != "Deployment" and "initContainers" in pod
    ]
    assert others == []
    for name in sorted(runs_dps):
        pod = deployments[name]
        main, wait = main_container(pod), schema_wait(pod)
        # args, not command: the app image's entrypoint stays (in verify mode it only execs them)
        assert "command" not in wait and wait["args"] == SCHEMA_WAIT_ARGS, name
        assert wait["image"] == main["image"], name
        assert wait["envFrom"] == main["envFrom"] == [{"configMapRef": {"name": "dps"}}], name
        expected = {"HOME": "/tmp", "DPS_PG_PASSWORD_FILE": env_of(main)["DPS_PG_PASSWORD_FILE"]}
        if "DPS_IMAGE_DIGEST" in env_of(main):  # the browser worker's own image digest
            expected["DPS_IMAGE_DIGEST"] = env_of(main)["DPS_IMAGE_DIGEST"]
        assert env_of(wait) == expected and len(wait["env"]) == len(expected), name
        mounts = {m["name"]: m for m in main["volumeMounts"]}
        assert wait["volumeMounts"] == [mounts["tmp"], mounts["ca"], mounts["pg-app"]], name
        assert wait["securityContext"] == main["securityContext"], name
        # small, and never above the main container's: the pod is scheduled on the larger of the two
        for part in ("requests", "limits"):
            for resource in ("cpu", "memory"):
                small = quantity(wait["resources"][part][resource])
                assert 0 < small <= quantity(main["resources"][part][resource]), (name, part)
    # app and worker run one image, so they wait in exactly the same way
    assert schema_wait(deployments["dps-app"]) == schema_wait(deployments["dps-worker"])
    assert not [f for f in oc.check(docs, settings=False) if f.rule == "schema-wait"]


def test_the_schema_wait_holds_only_the_login_its_check_opens() -> None:
    """``python -m dps.migrate --verify`` reads the ledger through ``db.verify_migrations``, which
    opens the API pool (the app login): the wait mounts that Secret and no other — no KEK, no API
    keys, never the owner login (the Task 25 runtime proof: ``--verify`` cannot run in the Job's pod).
    """
    from dps import db, migrate

    assert "db.verify_migrations(" in inspect.getsource(migrate._run)
    assert _opens(db.verify_migrations) == {"app"}
    docs = render("prod")
    for role in ROLES:
        pod = pod_of(docs, role)
        volumes = {v["name"]: v for v in pod["volumes"]}
        wait = schema_wait(pod)
        secrets = {
            volumes[m["name"]]["secret"]["secretName"]
            for m in wait["volumeMounts"]
            if "secret" in volumes[m["name"]]
        }
        assert secrets == {"dps-pg-app"}, role
        assert {k for k in env_of(wait) if k.endswith("_FILE")} == {"DPS_PG_PASSWORD_FILE"}, role
    readme = README.read_text("utf-8")
    assert SCHEMA_WAIT in readme and "python -m dps.migrate --verify" in readme


def test_the_release_check_denies_a_dps_deployment_that_does_not_wait_for_its_schema() -> None:
    docs = render("prod")
    assert not [f for f in oc.check(docs, settings=False) if f.rule == "schema-wait"]
    del pod_of(docs, "dps-worker")["initContainers"]  # starts at once
    schema_wait(pod_of(docs, "dps-app"))["args"] = ["sh", "-c", "sleep 30"]  # waits for nothing
    # verifies the migrations another image ships
    app_image = main_container(pod_of(docs, "dps-app"))["image"]
    schema_wait(pod_of(docs, "dps-browser-worker"))["image"] = app_image
    denied = [f for f in oc.check(docs, settings=False) if f.rule == "schema-wait"]
    assert all(f.level == "deny" for f in denied)
    assert sorted(f.resource for f in denied) == [
        "Deployment/dps-app",
        "Deployment/dps-browser-worker",
        "Deployment/dps-worker",
    ]


# --- configuration: Settings and the production posture guard ------------------------------------


@pytest.mark.parametrize("role", ROLES)
def test_prod_configuration_passes_the_posture_guard(role: str) -> None:
    """The ConfigMap plus the role's env, with each ``*_FILE`` pointing at a dummy file."""
    problems = oc.settings_problems(render("prod"), role, posture=True)
    assert problems == []


def test_tokenization_is_refused_until_the_release_lists_each_tenants_profiles() -> None:
    """S3 review F-11 met the manifests at the release merge: staging/prod need
    ``DPS_TOKENIZER_PROFILES`` with the tokenizer on. The committed ConfigMap fails closed (``{}``:
    no tenant may tokenize anything) and passes the posture guard; the release example lists
    each tenant's entity types and data elements; dev (the mock, no posture) leaves it open."""
    from dps.config import Settings

    prod = oc.configmap_data(render("prod"), "dps")
    assert prod["DPS_TOKENIZER_PROFILES"] == "{}"
    committed = Settings(_env_file=None, tokenizer_profiles=prod["DPS_TOKENIZER_PROFILES"])  # type: ignore[call-arg]
    assert committed.tokenizer_profiles_restricted
    assert not committed.tokenizer_profile_allowed("default", "SSN", None)
    assert not committed.tokenizer_profile_allowed("default", "SSN", "ssn-fpe")
    example = oc.configmap_data(render("release-example"), "dps")["DPS_TOKENIZER_PROFILES"]
    released = Settings(_env_file=None, tokenizer_profiles=example)  # type: ignore[call-arg]
    assert released.tokenizer_profile_allowed("default", "SSN", "ssn-fpe")
    assert not released.tokenizer_profile_allowed("default", "SSN", None)
    dev = oc.configmap_data(render("dev"), "dps")
    assert dev["DPS_TOKENIZER_PROFILES"] == ""
    readme = README.read_text("utf-8")
    assert "DPS_TOKENIZER_PROFILES" in readme


def test_the_posture_probe_sees_a_weakened_configuration() -> None:
    docs = render("prod")
    cm = one(docs, "ConfigMap", "dps")
    cm["data"]["DPS_VERIFY_WARN_ALLOWED"] = "true"
    cm["data"]["DPS_CORS_ORIGINS"] = "*"
    problems = oc.settings_problems(docs, "dps-app", posture=True)
    assert problems is not None
    assert any("DPS_VERIFY_WARN_ALLOWED" in p for p in problems)
    assert any("DPS_CORS_ORIGINS" in p for p in problems)


@pytest.mark.parametrize("overlay", OVERLAYS)
def test_every_workload_builds_its_settings(overlay: str) -> None:
    docs = render(overlay)
    checked = 0
    for _, name, pod in oc.workloads(docs):
        for container in dps_containers(pod):  # the dev mocks are not DPS processes
            cname = container["name"]
            assert oc.settings_problems(docs, name, posture=False, container=cname) == [], cname
            checked += 1
    assert checked == len(ROLES) * 2 + 2  # each role and its schema wait; the Job; the CronJob


def test_the_release_check_builds_the_settings_of_the_schema_wait_too() -> None:
    """The wait runs DPS code (``dps.migrate`` builds ``Settings``) with its own, shorter env."""
    docs = render("prod")
    assert [f for f in oc.configuration_findings(docs) if f.level == "deny"] == []
    wait = schema_wait(pod_of(docs, "dps-worker"))
    wait["env"].append({"name": "DPS_MIGRATIONS_MODE", "value": "sometimes"})
    denied = [f for f in oc.configuration_findings(docs) if f.level == "deny"]
    assert [(f.rule, f.resource) for f in denied] == [
        ("posture", f"Deployment/dps-worker container {SCHEMA_WAIT}")
    ]
    assert "migrations_mode" in denied[0].message


@pytest.mark.parametrize("target", [*OVERLAYS, "release-example"])
def test_the_app_dials_the_dps_db_host_by_its_certificate_name(target: str) -> None:
    """verify-full TLS checks the server certificate against the name dialled (dps/db.py)."""
    docs = render(target)
    cm = oc.configmap_data(docs, "dps")
    assert cm["DPS_PG_HOST"] == one(docs, "Service", "dps-db")["spec"]["externalName"]
    assert cm["DPS_PG_SSL_ROOT_CERT"]


def test_the_release_check_denies_a_db_host_that_is_not_the_alias() -> None:
    """S5 review r1, N5: the release gate itself keeps ``DPS_PG_HOST`` and ``dps-db`` the same."""
    docs = render("release-example")
    assert not [f for f in oc.check(docs, settings=False) if f.rule == "db-host"]
    one(docs, "ConfigMap", "dps")["data"]["DPS_PG_HOST"] = "pgsql-other.bank.example"
    denied = [f for f in oc.check(docs, settings=False) if f.rule == "db-host"]
    assert [(f.level, f.resource) for f in denied] == [("deny", "ConfigMap/dps")]


# --- provenance: the image digest each container stamps into evidence ------------------------------


@pytest.mark.parametrize("target", [*OVERLAYS, "release-example"])
def test_each_container_stamps_its_own_image_digest(target: str) -> None:
    """S5 review r1, B2: evidence packs carry ``DPS_IMAGE_DIGEST`` — the worker's job packs and,
    since M4, the browser worker's scan packs — so each DPS container's value is the digest of the
    image it runs. The browser worker runs its own image: its env entry overrides the ConfigMap's.
    """
    docs = render(target)
    stamped = stamped_digests(docs)
    jobs = {n for k, n, _ in oc.workloads(docs) if k == "Job"}
    assert set(stamped) == {
        "dps-app/app",
        "dps-worker/worker",
        "dps-browser-worker/browser-worker",
        *(f"{role}/{SCHEMA_WAIT}" for role in ROLES),  # S5 review r2: the schema waits read it too
        f"{MAINTAIN}/migrate",
        *(f"{job}/migrate" for job in jobs),
    }
    for _, name, pod in oc.workloads(docs):
        for container in dps_containers(pod):
            key = f"{name}/{container['name']}"
            assert stamped[key] == image_digest(container), key
    browser = pod_of(docs, "dps-browser-worker")
    assert "DPS_IMAGE_DIGEST" in env_of(main_container(browser))
    assert "DPS_IMAGE_DIGEST" in env_of(schema_wait(browser))  # it runs the browser image too
    if target == "release-example":
        assert stamped["dps-browser-worker/browser-worker"] != stamped["dps-app/app"]
    assert not [f for f in oc.check(docs, settings=False) if f.rule == "image-digest-setting"]


def test_a_release_that_sets_every_parameter_passes_the_gate(tmp_path: Path) -> None:
    """Every parameter set with a bank's values: 0 denied, 0 warnings — the digest settings follow
    ``images:`` through the example's ``replacements`` (each digest is set once, in ``images:``).
    """
    if KUSTOMIZE is None:
        pytest.skip("needs the kustomize binary (marker deploy)")
    release = release_overlay(tmp_path)
    docs = oc.render(release, KUSTOMIZE)
    assert oc.check(docs) == []  # with the Settings and posture probe of every role
    assert oc.main([str(release), "--settings", "off", "--fail-on-warn"]) == 0
    stamped = stamped_digests(docs)
    browser = {k: stamped.pop(k) for k in sorted(stamped) if k.startswith("dps-browser-worker/")}
    assert browser == {
        "dps-browser-worker/browser-worker": "sha256:" + "b" * 64,
        f"dps-browser-worker/{SCHEMA_WAIT}": "sha256:" + "b" * 64,
    }
    assert set(stamped.values()) == {"sha256:" + "a" * 64}


def test_the_release_check_denies_a_stale_digest_setting(tmp_path: Path) -> None:
    """S5 review r1, B2: the images are bumped (``a…a``, ``b…b``) but ``DPS_IMAGE_DIGEST`` still
    names another build (``c…c``), so evidence would name an image that did not produce it. This
    passed ``--fail-on-warn`` with 0 findings before; now every container that reads it is denied.
    """
    if KUSTOMIZE is None:
        pytest.skip("needs the kustomize binary (marker deploy)")
    stale = "DPS_IMAGE_DIGEST=sha256:" + "c" * 64
    release = release_overlay(tmp_path, derive=False, literals=[stale])
    findings = oc.check(oc.render(release, KUSTOMIZE), settings=False)
    denied = [f for f in findings if f.rule == "image-digest-setting"]
    assert all(f.level == "deny" for f in denied)
    assert sorted(f.resource for f in denied) == [
        f"CronJob/{MAINTAIN} container migrate",
        "Deployment/dps-app container app",
        f"Deployment/dps-app container {SCHEMA_WAIT}",
        f"Deployment/dps-worker container {SCHEMA_WAIT}",
        "Deployment/dps-worker container worker",
        "Job/dps-migrate-1-0-0 container migrate",
    ]
    # the browser worker's own entries (its two containers) are still the zero placeholder: a
    # warning each (the gate refuses them with --fail-on-warn), not this denial
    zero = [f.resource for f in findings if f.rule == "placeholder-digest"]
    assert zero == ["Deployment/dps-browser-worker"] * 2
    assert oc.main([str(release), "--settings", "off"]) == 1


@pytest.mark.parametrize("container", ["browser-worker", SCHEMA_WAIT])
def test_the_release_check_denies_the_app_digest_in_the_browser_worker(
    tmp_path: Path, container: str
) -> None:
    """Without its own entry a browser-worker container inherits the ConfigMap's value: the app
    image's. Its schema wait runs the browser image as well."""
    if KUSTOMIZE is None:
        pytest.skip("needs the kustomize binary (marker deploy)")
    docs = oc.render(release_overlay(tmp_path), KUSTOMIZE)
    pod = pod_of(docs, "dps-browser-worker")
    target = next(c for c in oc.containers(pod) if c["name"] == container)
    target["env"] = [e for e in target["env"] if e["name"] != "DPS_IMAGE_DIGEST"]
    denied = [f for f in oc.check(docs, settings=False) if f.rule == "image-digest-setting"]
    assert [(f.level, f.resource) for f in denied] == [
        ("deny", f"Deployment/dps-browser-worker container {container}")
    ]


def test_the_digest_setting_must_be_a_literal_the_check_can_compare() -> None:
    docs = render("prod")  # zero digests everywhere: placeholders that warn, no denial
    assert not [f for f in oc.check(docs, settings=False) if f.rule == "image-digest-setting"]
    del one(docs, "ConfigMap", "dps")["data"]["DPS_IMAGE_DIGEST"]
    main_container(pod_of(docs, "dps-browser-worker"))["env"] = [
        e
        for e in main_container(pod_of(docs, "dps-browser-worker"))["env"]
        if e["name"] != "DPS_IMAGE_DIGEST"
    ] + [{"name": "DPS_IMAGE_DIGEST", "valueFrom": {"fieldRef": {"fieldPath": "metadata.name"}}}]
    findings = [f for f in oc.check(docs, settings=False) if f.rule == "image-digest-setting"]
    assert len(findings) == 7 and all(f.level == "deny" for f in findings)
    denied = {f.resource: f.message for f in findings}
    unset = [r for r, m in denied.items() if "DPS_IMAGE_DIGEST is not set" in m]
    assert sorted(unset) == [
        f"CronJob/{MAINTAIN} container migrate",
        "Deployment/dps-app container app",
        f"Deployment/dps-app container {SCHEMA_WAIT}",
        f"Deployment/dps-worker container {SCHEMA_WAIT}",
        "Deployment/dps-worker container worker",
        "Job/dps-migrate-0 container migrate",
    ]
    assert "literal" in denied["Deployment/dps-browser-worker container browser-worker"]


# --- what runs where ----------------------------------------------------------------------------


def test_prod_has_no_mocks_and_no_source_databases() -> None:
    forbidden = re.compile(r"mock|oracle|mongo|minio|source-pg")
    prod = {d["metadata"]["name"] for d in render("prod")}
    assert not {n for n in prod if forbidden.search(n)}
    dev = {d["metadata"]["name"] for d in render("dev")}
    assert {"dps-tokenizer-mock", "dps-azure-ocr-mock"} <= dev
    assert not {n for n in dev if re.search(r"oracle|mongo|minio|source-pg", n)}


def test_dev_mock_server_matches_the_compose_mock() -> None:
    copy_ = DEPLOY / "components" / "dev-mocks" / "server.py"
    assert copy_.read_bytes() == (ROOT / "docker" / "tokenizer-mock" / "server.py").read_bytes()


def test_dev_points_the_engine_at_the_in_namespace_mocks() -> None:
    docs = render("dev")
    cm = oc.configmap_data(docs, "dps")
    assert cm["DPS_ENV"] == "dev" and cm["DPS_LLM_PROVIDER"] == "stub"
    assert cm["DPS_TOKENIZER_URL"] == "http://dps-tokenizer-mock:8420"
    assert cm["DPS_AZURE_VISION_ENDPOINT"] == "http://dps-azure-ocr-mock:5000"
    assert cm["DPS_EGRESS_TLS_REQUIRED"] == "false"
    mock = pod_of(docs, "dps-tokenizer-mock")
    container = main_container(mock)
    assert container["args"][:2] == ["python", "/mock/server.py"]
    server = next(v for v in mock["volumes"] if v.get("configMap"))
    assert "server.py" in one(docs, "ConfigMap", server["configMap"]["name"])["data"]
    assert one(docs, "Service", "dps-tokenizer-mock")["spec"]["ports"][0]["port"] == 8420
    assert one(docs, "Service", "dps-azure-ocr-mock")["spec"]["ports"][0]["port"] == 5000
    prod = oc.configmap_data(render("prod"), "dps")
    assert prod["DPS_ENV"] == "prod" and prod["DPS_LLM_PROVIDER"] == "stellar"
    assert prod["DPS_EGRESS_TLS_REQUIRED"] == "true"


# --- network policies ---------------------------------------------------------------------------


def _selected(policy: dict[str, Any], component: str) -> bool:
    selector = policy["spec"]["podSelector"]
    if not selector:
        return True
    labels = {
        "app.kubernetes.io/name": "data-privacy-services",
        "app.kubernetes.io/component": component,
    }
    if any(labels.get(k) != v for k, v in selector.get("matchLabels", {}).items()):
        return False
    for expr in selector.get("matchExpressions", []):
        assert expr["operator"] == "In"
        if labels.get(expr["key"]) not in expr["values"]:
            return False
    return True


def _egress_ports(docs: list[dict[str, Any]], component: str) -> set[tuple[str, int]]:
    """(peer, port) pairs a component may reach; peer = cidr or ``ns:<name>`` or ``pods``."""
    out: set[tuple[str, int]] = set()
    for policy in (d for d in docs if d["kind"] == "NetworkPolicy"):
        if "Egress" not in policy["spec"].get("policyTypes", []) or not _selected(
            policy, component
        ):
            continue
        for rule in policy["spec"].get("egress", []):
            for peer in rule.get("to", []):
                if "ipBlock" in peer:
                    key = peer["ipBlock"]["cidr"]
                elif "namespaceSelector" in peer:
                    key = (
                        "ns:"
                        + peer["namespaceSelector"]["matchLabels"]["kubernetes.io/metadata.name"]
                    )
                else:
                    key = "pods"
                out |= {(key, p["port"]) for p in rule.get("ports", [])}
    return out


@pytest.mark.parametrize("overlay", OVERLAYS)
def test_network_policies_deny_by_default_and_open_only_what_each_role_uses(overlay: str) -> None:
    docs = render(overlay)
    deny = one(docs, "NetworkPolicy", "default-deny")["spec"]
    assert deny["podSelector"] == {} and set(deny["policyTypes"]) == {"Ingress", "Egress"}
    assert "ingress" not in deny and "egress" not in deny
    for component in ("app", "worker", "browser-worker", "migrate"):
        reach = _egress_ports(docs, component)
        assert {("ns:openshift-dns", 53), ("ns:openshift-dns", 5353)} <= reach, component
        assert any(
            port == 5432 and ipaddress.ip_network(peer).prefixlen == 32
            for peer, port in reach
            if "/" in peer
        ), component
    app, worker = _egress_ports(docs, "app"), _egress_ports(docs, "worker")
    browser, migrate = _egress_ports(docs, "browser-worker"), _egress_ports(docs, "migrate")
    # app and worker: the gateways (LLM, COIN, OCR, tokenizer) and the object store on 443, and
    # the source databases (the app tests and browses connections; the worker reads and writes)
    for reach in (app, worker):
        assert len({peer for peer, port in reach if port == 443}) >= 5
        assert {1521, 2484, 5432, 27017} <= {port for _, port in reach}
    # the browser worker: the metadata DB, the object store and the scan targets only
    assert {port for _, port in browser} == {53, 5353, 5432, 443}
    assert not {port for _, port in browser} & {1521, 2484, 27017}
    # migrate: DNS and the metadata DB only
    assert {port for _, port in migrate} == {53, 5353, 5432}
    # ingress: the router to the app's http port; the monitoring stack to every metrics port
    ingress = [
        d for d in docs if d["kind"] == "NetworkPolicy" and "Ingress" in d["spec"]["policyTypes"]
    ]
    router = one(docs, "NetworkPolicy", "app-ingress-from-router")["spec"]
    assert router["podSelector"]["matchLabels"]["app.kubernetes.io/component"] == "app"
    assert [p["port"] for p in router["ingress"][0]["ports"]] == [8400]
    metrics = one(docs, "NetworkPolicy", "metrics-from-monitoring")["spec"]
    assert {p["port"] for p in metrics["ingress"][0]["ports"]} == {8400, 9400, 9401}
    if overlay == "prod":
        assert {d["metadata"]["name"] for d in ingress} == {
            "default-deny",
            "app-ingress-from-router",
            "metrics-from-monitoring",
        }
    else:  # the in-namespace mocks answer the app and the worker only
        mocks = one(docs, "NetworkPolicy", "dev-mocks-ingress")["spec"]
        assert mocks["ingress"][0]["from"][0]["podSelector"]["matchExpressions"][0]["values"] == [
            "app",
            "worker",
        ]


# --- monitoring ---------------------------------------------------------------------------------


def test_metrics_are_scraped_and_the_alerts_exist() -> None:
    docs = render("prod")
    monitor = next(d for d in docs if d["kind"] == "PodMonitor")["spec"]
    assert {e["port"] for e in monitor["podMetricsEndpoints"]} == {"http", "ops"}
    assert all(e["path"] == "/metrics" for e in monitor["podMetricsEndpoints"])
    assert monitor["podTargetLabels"] == ["app.kubernetes.io/component"]
    expr = monitor["selector"]["matchExpressions"][0]
    assert expr["key"] == "app.kubernetes.io/component" and set(expr["values"]) == {
        "app",
        "worker",
        "browser-worker",
    }
    rules = {
        r["alert"]: r
        for d in docs
        if d["kind"] == "PrometheusRule"
        for g in d["spec"]["groups"]
        for r in g["rules"]
    }
    severities = {name: r["labels"]["severity"] for name, r in rules.items()}
    assert severities == {
        "DpsRawPiiSentToLlm": "critical",
        "DpsLlmExposureBlocked": "warning",
        "DpsVerificationFailures": "warning",
        "DpsExactRowResamplesHigh": "info",
        "DpsTokenizerFailing": "warning",
        "DpsNoDbKeysLoaded": "warning",
        "DpsWorkersAbsent": "critical",
        "DpsAuditMaintenanceStale": "warning",
    }
    # the raw-PII alert carries the tenant and reads both counters (Task 16B, HANDOFF §5)
    raw = rules["DpsRawPiiSentToLlm"]["expr"]
    assert "sum by (tenant_id)" in raw and "dps_llm_raw_pii_sent_by_tenant_total" in raw
    assert "dps_llm_raw_pii_sent_total" in raw
    # only the app loads DB keys: the gauge reads 0 in every worker
    assert 'app_kubernetes_io_component="app"' in rules["DpsNoDbKeysLoaded"]["expr"]
    assert 'app_kubernetes_io_component="worker"' in rules["DpsWorkersAbsent"]["expr"]
    # the audit-partition CronJob (r1 B1) has not succeeded for days, or its runs fail and it has
    # never succeeded: kube-state-metrics series, which rules in a user namespace may read for that
    # namespace. Not kube_cronjob_created: OpenShift's kube-state-metrics drops every *_created
    # series, so a "never since creation" branch could not fire there (S5 review r2, N1).
    stale = rules["DpsAuditMaintenanceStale"]["expr"]
    assert f'kube_cronjob_status_last_successful_time{{cronjob="{MAINTAIN}"}}' in stale
    assert f'kube_job_status_failed{{job_name=~"{MAINTAIN}-.*"}}' in stale
    assert not re.search(r"kube_\w+_created\b", stale)
    for name, r in rules.items():
        assert r["annotations"]["summary"], name


# --- placeholders and the release check (the Wega pattern) ---------------------------------------


@pytest.mark.parametrize("overlay", OVERLAYS)
def test_committed_overlays_carry_placeholders_that_only_warn(overlay: str) -> None:
    findings = oc.check(render(overlay))
    assert [f for f in findings if f.level == "deny"] == []
    rules = {f.rule for f in findings if f.level == "warn"}
    assert {
        "placeholder-digest",
        "placeholder-host",
        "placeholder-cidr",
        "placeholder-job",
    } <= rules


def test_placeholder_hosts_are_found_inside_lists() -> None:
    """S5 review r1, N6: a host is bounded by any character a host name cannot hold."""
    listed = "a.example.invalid,b.example.invalid https://c.example.invalid:8443/x"
    assert oc.INVALID_HOST.findall(listed) == [
        "a.example.invalid",
        "b.example.invalid",
        "c.example.invalid",
    ]
    assert oc.EXAMPLE_HOST.findall("dps.bank.example,x.bank.example") == [
        "dps.bank.example",
        "x.bank.example",
    ]
    assert not oc.INVALID_HOST.findall("host.invalid.corp.internal")  # not the .invalid TLD
    assert not oc.EXAMPLE_HOST.findall("postgres.example.invalid")  # a placeholder, not an example


def test_placeholders_are_documented() -> None:
    readme = README.read_text("utf-8")
    docs = render("prod")
    found = oc.placeholder_values(docs)
    assert found["hosts"] and found["images"] and found["cidrs"]
    for value in sorted(set().union(*found.values())):
        assert value in readme, value
    assert "dps-migrate-0" in readme and "DPS_IMAGE_DIGEST" in readme


def test_the_release_example_sets_every_parameter() -> None:
    """A release overlay that sets every documented parameter clears every placeholder.

    What remains are the example's own documentation values (``.example`` hosts, TEST-NET-2
    CIDRs, a ``1…1`` digest): they warn too, so a verbatim copy of the example fails the gate.
    """
    docs = render("release-example")
    findings = oc.check(docs)
    assert [f for f in findings if f.level == "deny"] == []
    assert {f.rule for f in findings} == {"example-value"}
    assert one(docs, "Job", "dps-migrate-1-0-0")
    assert not oc.placeholder_values(docs)["hosts"]


def test_the_release_check_refuses_a_leftover_placeholder(tmp_path: Path) -> None:
    if KUSTOMIZE is None:
        pytest.skip("needs the kustomize binary (marker deploy)")
    route_host = [
        {"op": "test", "path": "/spec/host", "value": "dps.apps.bank.example"},
        {"op": "replace", "path": "/spec/host", "value": "dps.apps.example.invalid"},
    ]
    # kustomize refuses an absolute path as a kustomization root: refer to the example relatively
    kustomization = {
        "apiVersion": "kustomize.config.k8s.io/v1beta1",
        "kind": "Kustomization",
        "resources": [os.path.relpath(RELEASE_EXAMPLE, tmp_path)],
        "patches": [
            {"target": {"kind": "Route", "name": "dps-app"}, "patch": yaml.safe_dump(route_host)}
        ],
    }
    (tmp_path / "kustomization.yaml").write_text(yaml.safe_dump(kustomization), "utf-8")
    findings = oc.check(oc.render(tmp_path, KUSTOMIZE), settings=False)
    leftover = [f for f in findings if f.rule == "placeholder-host"]
    assert [f.resource for f in leftover] == ["Route/dps-app"]
    assert all(f.level == "warn" for f in leftover)


def test_the_release_check_exit_codes(tmp_path: Path) -> None:
    """Warnings pass a committed render and fail ``--fail-on-warn``; a deny always fails."""
    if KUSTOMIZE is None:
        pytest.skip("needs the kustomize binary (marker deploy)")
    prod = str(DEPLOY / "overlays" / "prod")
    assert oc.main([prod, "--settings", "off"]) == 0
    assert oc.main([prod, "--settings", "off", "--fail-on-warn"]) == 1
    docs = render("prod")
    main_container(pod_of(docs, "dps-app"))["securityContext"]["readOnlyRootFilesystem"] = False
    weakened = tmp_path / "rendered.yaml"
    weakened.write_text(yaml.safe_dump_all(docs), "utf-8")
    assert oc.main([str(weakened), "--settings", "off"]) == 1


def test_the_release_check_denies_a_weakened_pod() -> None:
    docs = render("prod")
    container = main_container(pod_of(docs, "dps-worker"))
    container["securityContext"]["readOnlyRootFilesystem"] = False
    container["env"].append({"name": "DPS_PG_PASSWORD", "value": "x"})
    container["image"] = container["image"].split("@")[0] + ":latest"
    denies = {f.rule for f in oc.check(docs, settings=False) if f.level == "deny"}
    assert {"container-security", "literal-secret", "image-digest"} <= denies


def test_every_json6902_replace_is_guarded_by_a_test() -> None:
    """Each replace/remove follows a ``test`` of the same path: a reordered base fails the render."""
    checked = 0
    for path in sorted(DEPLOY.rglob("kustomization.yaml")):
        for ops in oc.json6902_patches(path):
            tested: set[str] = set()
            for op in ops:
                if op["op"] == "test":
                    tested.add(op["path"])
                elif op["op"] in ("replace", "remove"):
                    assert op["path"] in tested, (path.relative_to(ROOT), op)
                    checked += 1
    assert checked >= 10
