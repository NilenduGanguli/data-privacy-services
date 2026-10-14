"""OpenShift manifests (M5 Task 25): kustomize base, components, dev and prod overlays.

Renders ``deploy/openshift/overlays/{dev,prod}`` and the worked release example with the
``kustomize`` binary and checks the objects: every role, pod security for OpenShift's
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
SECRET_NAME = re.compile(r"(KEY|SECRET|PASSWORD|TOKEN|KEK)$")
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


def env_of(container: dict[str, Any]) -> dict[str, str]:
    return {e["name"]: e.get("value", "") for e in container.get("env", []) if "value" in e}


def secret_volumes(pod: dict[str, Any]) -> set[str]:
    return {v["secret"]["secretName"] for v in pod.get("volumes", []) if "secret" in v}


# --- roles -------------------------------------------------------------------------------------


@pytest.mark.parametrize("overlay", OVERLAYS)
def test_overlays_render_every_role(overlay: str) -> None:
    docs = render(overlay)
    names = {(d["kind"], d["metadata"]["name"]) for d in docs}
    assert {("Deployment", n) for n in ROLES} <= names
    assert ("Job", "dps-migrate-0") in names
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
        name: main_container(pod_of(docs, name))["args"] for name in (*ROLES, "dps-migrate-0")
    }
    assert commands["dps-app"][:3] == ["uvicorn", "dps.app:create_app", "--factory"]
    # uvicorn's access log is stdlib logging and bypasses the structlog allow-list (Dockerfile CMD).
    assert "--no-access-log" in commands["dps-app"]
    assert commands["dps-worker"] == ["python", "-m", "dps.worker"]
    assert commands["dps-browser-worker"] == ["python", "-m", "dps.browser_worker"]
    assert commands["dps-migrate-0"] == ["python", "-m", "dps.migrate"]
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
            if kind == "Deployment":
                assert c.get("livenessProbe") and c.get("readinessProbe"), (name, c["name"])
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
        assert not SECRET_NAME.search(key), key
    for _, name, pod in oc.workloads(docs):
        volumes = {v["name"]: v for v in pod.get("volumes", [])}
        for c in oc.containers(pod):
            assert not any("secretRef" in src for src in c.get("envFrom", [])), (name, c["name"])
            mounts = [(m["mountPath"], volumes[m["name"]]) for m in c.get("volumeMounts", [])]
            for entry in c.get("env", []):
                env_name = entry["name"]
                if "value" in entry:
                    assert not SECRET_NAME.search(env_name), (name, env_name)
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
    names = set(oc.configmap_data(docs, "dps"))
    for _, _, pod in oc.workloads(docs):
        for c in oc.containers(pod):
            names |= {e["name"] for e in c.get("env", []) if e["name"].startswith("DPS_")}
    for env_name in names:
        field = env_name.removeprefix("DPS_").lower()
        field = field.removesuffix("_file") if field.removesuffix("_file") in fields else field
        assert field in fields, env_name


def test_only_the_migrate_job_mounts_the_owner_secret() -> None:
    for overlay in OVERLAYS:
        docs = render(overlay)
        owners = [n for _, n, pod in oc.workloads(docs) if "dps-pg-owner" in secret_volumes(pod)]
        assert owners == ["dps-migrate-0"], overlay
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


# --- configuration: Settings and the production posture guard ------------------------------------


@pytest.mark.parametrize("role", ROLES)
def test_prod_configuration_passes_the_posture_guard(role: str) -> None:
    """The ConfigMap plus the role's env, with each ``*_FILE`` pointing at a dummy file."""
    problems = oc.settings_problems(render("prod"), role, posture=True)
    assert problems == []


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
    for _, name, pod in oc.workloads(docs):
        if oc.dps_container(pod) is None:
            continue  # the dev mocks are not DPS processes
        assert oc.settings_problems(docs, name, posture=False) == [], name


@pytest.mark.parametrize("target", [*OVERLAYS, "release-example"])
def test_the_app_dials_the_dps_db_host_by_its_certificate_name(target: str) -> None:
    """verify-full TLS checks the server certificate against the name dialled (dps/db.py)."""
    docs = render(target)
    cm = oc.configmap_data(docs, "dps")
    assert cm["DPS_PG_HOST"] == one(docs, "Service", "dps-db")["spec"]["externalName"]
    assert cm["DPS_PG_SSL_ROOT_CERT"]


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
    }
    # the raw-PII alert carries the tenant and reads both counters (Task 16B, HANDOFF §5)
    raw = rules["DpsRawPiiSentToLlm"]["expr"]
    assert "sum by (tenant_id)" in raw and "dps_llm_raw_pii_sent_by_tenant_total" in raw
    assert "dps_llm_raw_pii_sent_total" in raw
    # only the app loads DB keys: the gauge reads 0 in every worker
    assert 'app_kubernetes_io_component="app"' in rules["DpsNoDbKeysLoaded"]["expr"]
    assert 'app_kubernetes_io_component="worker"' in rules["DpsWorkersAbsent"]["expr"]
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
