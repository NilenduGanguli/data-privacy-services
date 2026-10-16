"""Check rendered OpenShift manifests: hardening rules (deny) and deploy parameters (warn).

The committed overlays (``deploy/openshift/overlays/{dev,prod}``) carry **placeholders** for what
this repository cannot know — all-zero image digests, ``.invalid`` hosts, TEST-NET-1
(``192.0.2.0/24``) NetworkPolicy peers and the migrate Job's committed name ``dps-migrate-0`` — and
the release overlay the deploy agent writes over ``overlays/prod`` sets each of them
(``deploy/openshift/README.md`` lists every one). A placeholder is a *warning*, so the committed
overlays render and pass; ``--fail-on-warn`` makes the same check the release gate, refusing a
release overlay that still carries one (the Wega pattern). The worked example
(``deploy/openshift/release-example``) uses documentation values (``.example`` hosts,
TEST-NET-2/3 CIDRs, the ``1…1`` and ``2…2`` digests) that warn as well, so a verbatim copy fails
the gate too.

Hardening violations are *denials* (non-zero exit always): pod security for the ``restricted-v2``
SCC (non-root arbitrary UID, ``RuntimeDefault`` seccomp, read-only root, no privilege escalation,
all capabilities dropped, no host namespaces or ``hostPath``), resources, probes, digest-pinned
images, secrets only as mounted files, the owner (DDL) login only in the migrate Job and the
audit-maintenance CronJob, a ``default-deny`` NetworkPolicy, and no mocks or source systems in
staging/prod. Two consistency rules deny as well: each DPS container's ``DPS_IMAGE_DIGEST`` (stamped
into every evidence pack) is the digest of the image that container runs, and ``DPS_PG_HOST`` is the
``dps-db`` Service's externalName. So does the start order: every Deployment that runs DPS waits in
an init container until ``python -m dps.migrate --verify`` passes, in the image it runs
(``schema-wait``; Kubernetes applies the migrate Job and the Deployments together). With the ``dps``
package importable, each DPS container's settings (init containers included) are built from the
ConfigMap and its env (every ``*_FILE`` pointed at a dummy file) in a clean subprocess and, for the
Deployments' DPS containers in staging/prod, run through the production posture guard
(``dps.posture.posture_problems``).

Usage::

    python tools/openshift_check.py deploy/openshift/overlays/prod          # committed: warnings ok
    python tools/openshift_check.py <release-overlay-dir> --fail-on-warn    # the release gate
    kustomize build <dir> | python tools/openshift_check.py - --fail-on-warn

Exit codes: 0 pass · 1 a denial (or a warning with ``--fail-on-warn``) · 2 the render failed.
Messages name resources, fields and settings — never a value of a secret (manifests hold none).
"""

from __future__ import annotations

import argparse
import base64
import ipaddress
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

ZERO_DIGEST = "sha256:" + "0" * 64
EXAMPLE_DIGESTS = ("sha256:" + "1" * 64, "sha256:" + "2" * 64)  # app, browser (release example)
TEST_NET_1 = ipaddress.ip_network("192.0.2.0/24")  # RFC 5737: committed placeholders
EXAMPLE_NETS = (ipaddress.ip_network("198.51.100.0/24"), ipaddress.ip_network("203.0.113.0/24"))
# A host ends at any character a host name cannot hold (a comma list, a port, a path, the end).
INVALID_HOST = re.compile(r"(?:[A-Za-z0-9-]+\.)+invalid(?![A-Za-z0-9.-])")
EXAMPLE_HOST = re.compile(r"(?:[A-Za-z0-9-]+\.)+example(?![A-Za-z0-9.-])")
SECRET_NAME = re.compile(r"(KEY|SECRET|PASSWORD|TOKEN|KEK)$")
#: The secrets the production posture guard requires as files (``dps.posture.SECRET_ENV``; a test
#: keeps this a superset). Kept here so the check runs without ``dps`` importable.
SECRET_SETTINGS = frozenset(
    {
        "DPS_JOB_KEK",
        "DPS_JOB_KEK_PREVIOUS",
        "DPS_API_KEYS",
        "DPS_COIN_CLIENT_ID",
        "DPS_COIN_CLIENT_SECRET",
        "DPS_PG_PASSWORD",
        "DPS_PG_WORKER_PASSWORD",
        "DPS_PG_MIGRATION_PASSWORD",
        "DPS_TOKENIZER_TOKEN",
        "DPS_AZURE_VISION_KEY",
        "DPS_S3_ACCESS_KEY_ID",
        "DPS_S3_SECRET_ACCESS_KEY",
    }
)
#: Credentials the libraries in the images read from the environment (botocore, libpq): never a
#: literal either, in a ConfigMap or an env entry (``AWS_ACCESS_KEY_ID`` ends in neither suffix).
LIBRARY_SECRETS = frozenset(
    {"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "PGPASSWORD"}
)
PROD_FORBIDDEN = re.compile(r"mock|oracle|mongo|minio|source-pg")
WORKLOAD_KINDS = ("Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob")
RUN_TO_COMPLETION = ("Job", "CronJob")  # no liveness/readiness probes
OWNER_SECRET = "dps-pg-owner"
MIGRATE_PREFIX = "dps-migrate-"
MAINTAIN_CRONJOB = "dps-migrate-maintain"  # python -m dps.migrate --maintain, daily
DPS_CONFIGMAP = "dps"
DIGEST_SETTING = "DPS_IMAGE_DIGEST"
DB_SERVICE = "dps-db"
SCHEMA_VERIFY = "python -m dps.migrate --verify"  # what a DPS Deployment's init container waits on


class RenderError(RuntimeError):
    """``kustomize build`` failed or produced no objects."""


@dataclass(frozen=True)
class Finding:
    """One result: ``deny`` fails the check; ``warn`` fails it only with ``--fail-on-warn``."""

    level: Literal["warn", "deny"]
    rule: str
    resource: str
    message: str

    def __str__(self) -> str:
        return f"{self.level.upper():4} [{self.rule}] {self.resource}: {self.message}"


# --- rendering -----------------------------------------------------------------------------


def find_kustomize() -> list[str] | None:
    """The command that renders a kustomization: ``$KUSTOMIZE``, ``kustomize``, ``oc``/``kubectl``."""
    if os.environ.get("KUSTOMIZE"):
        return os.environ["KUSTOMIZE"].split()
    if shutil.which("kustomize"):
        return ["kustomize", "build"]
    for cli in ("oc", "kubectl"):
        if shutil.which(cli):
            return [cli, "kustomize"]
    return None


def load_documents(text: str) -> list[dict[str, Any]]:
    """Every non-empty YAML document, as a dict."""
    return [d for d in yaml.safe_load_all(text) if isinstance(d, dict) and d]


def render(path: Path, kustomize: Sequence[str] | None = None) -> list[dict[str, Any]]:
    """Render a kustomization directory, or load an already-rendered YAML file."""
    if path.is_file():
        return load_documents(path.read_text("utf-8"))
    command = list(kustomize or find_kustomize() or [])
    if not command:
        raise RenderError("no kustomize binary (install kustomize, or use oc/kubectl kustomize)")
    if command[-1] not in ("build", "kustomize"):
        command.append("build")
    proc = subprocess.run([*command, str(path)], capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise RenderError(f"{' '.join(command)} {path} failed: {proc.stderr.strip()[:2000]}")
    docs = load_documents(proc.stdout)
    if not docs:
        raise RenderError(f"{path} rendered no objects")
    return docs


# --- object helpers ------------------------------------------------------------------------


def ref(doc: dict[str, Any]) -> str:
    """``Kind/name`` of an object."""
    return f"{doc.get('kind')}/{doc.get('metadata', {}).get('name')}"


def objects(docs: Sequence[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    """Every object of one kind."""
    return [d for d in docs if d.get("kind") == kind]


def is_secret_setting(name: str) -> bool:
    """A variable that must never carry a literal: the guard's secrets, a library's credential, or
    a secret-like name."""
    return name in SECRET_SETTINGS or name in LIBRARY_SECRETS or bool(SECRET_NAME.search(name))


def is_migrate_workload(kind: str, name: str) -> bool:
    """The per-release migrate Job (``dps-migrate-<release>``) or the audit-maintenance CronJob."""
    return (kind == "Job" and name.startswith(MIGRATE_PREFIX)) or (
        kind == "CronJob" and name == MAINTAIN_CRONJOB
    )


def reads_dps_configmap(container: dict[str, Any]) -> bool:
    """Whether a container takes the ``dps`` ConfigMap as its environment (a DPS process)."""
    return any(
        source.get("configMapRef", {}).get("name") == DPS_CONFIGMAP
        for source in container.get("envFrom", []) or []
    )


def pod_spec(doc: dict[str, Any]) -> dict[str, Any] | None:
    """The pod spec of a workload object (``None`` for anything else)."""
    spec = doc.get("spec", {})
    if doc.get("kind") == "CronJob":
        spec = spec.get("jobTemplate", {}).get("spec", {})
    if doc.get("kind") not in WORKLOAD_KINDS:
        return None
    pod: dict[str, Any] = spec.get("template", {}).get("spec", {})
    return pod


def workloads(docs: Sequence[dict[str, Any]]) -> list[tuple[str, str, dict[str, Any]]]:
    """``(kind, name, pod_spec)`` of every workload."""
    out = []
    for doc in docs:
        pod = pod_spec(doc)
        if pod is not None:
            out.append((str(doc["kind"]), str(doc["metadata"]["name"]), pod))
    return out


def containers(pod: dict[str, Any]) -> list[dict[str, Any]]:
    """Init containers and containers of a pod spec."""
    return [*pod.get("initContainers", []), *pod.get("containers", [])]


def configmap_data(docs: Sequence[dict[str, Any]], name: str = DPS_CONFIGMAP) -> dict[str, str]:
    """``data`` of the ConfigMap ``name`` (``{}`` when it is not rendered)."""
    for cm in objects(docs, "ConfigMap"):
        if cm["metadata"]["name"] == name:
            return {str(k): str(v) for k, v in (cm.get("data") or {}).items()}
    return {}


def dps_container(pod: dict[str, Any]) -> dict[str, Any] | None:
    """The container that reads the ``dps`` ConfigMap (a DPS process), if any."""
    for c in pod.get("containers", []):
        if reads_dps_configmap(c):
            return c
    return None


def pod_environment(docs: Sequence[dict[str, Any]], container: dict[str, Any]) -> dict[str, str]:
    """The literal environment a container starts with: its ConfigMaps, then its ``env`` values."""
    env: dict[str, str] = {}
    for source in container.get("envFrom", []):
        name = source.get("configMapRef", {}).get("name")
        if name:
            prefix = source.get("prefix", "")
            env.update({prefix + k: v for k, v in configmap_data(docs, name).items()})
    for entry in container.get("env", []):
        if "value" in entry:
            env[entry["name"]] = str(entry["value"])
    return env


def _strings(node: Any, path: str = "") -> Iterator[tuple[str, str]]:
    """``(json-path, value)`` of every single-line string scalar under ``node``."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _strings(value, f"{path}.{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _strings(value, f"{path}[{i}]")
    elif isinstance(node, str) and "\n" not in node:
        yield path, node


def _cidrs(doc: dict[str, Any]) -> Iterator[tuple[str, str]]:
    """``(json-path, cidr)`` of every NetworkPolicy ipBlock (``cidr`` and ``except``)."""
    if doc.get("kind") != "NetworkPolicy":
        return
    for direction, side in (("ingress", "from"), ("egress", "to")):
        for i, rule in enumerate(doc.get("spec", {}).get(direction, []) or []):
            for j, peer in enumerate(rule.get(side, []) or []):
                block = peer.get("ipBlock")
                if not block:
                    continue
                where = f".spec.{direction}[{i}].{side}[{j}].ipBlock"
                yield f"{where}.cidr", str(block.get("cidr", ""))
                for k, cidr in enumerate(block.get("except", []) or []):
                    yield f"{where}.except[{k}]", str(cidr)


def _within(cidr: str, nets: Sequence[ipaddress.IPv4Network | ipaddress.IPv6Network]) -> bool:
    try:
        network = ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        return False
    return any(
        network.version == net.version and network.subnet_of(net)  # type: ignore[arg-type]
        for net in nets
    )


def placeholder_values(docs: Sequence[dict[str, Any]]) -> dict[str, set[str]]:
    """The placeholders a render still carries: ``.invalid`` hosts, zero-digest images, CIDRs."""
    found: dict[str, set[str]] = {"hosts": set(), "images": set(), "cidrs": set()}
    for doc in docs:
        for _, value in _strings(doc):
            found["hosts"] |= set(INVALID_HOST.findall(value))
            if value.endswith("@" + ZERO_DIGEST):
                found["images"].add(value.split("@")[0])
        for _, cidr in _cidrs(doc):
            if _within(cidr, [TEST_NET_1]):
                found["cidrs"].add(cidr)
    return found


# --- warnings: deploy parameters -------------------------------------------------------------


def placeholder_findings(docs: Sequence[dict[str, Any]]) -> list[Finding]:
    """A ``warn`` per placeholder (and per example value) left in the render."""
    out: list[Finding] = []
    for doc in docs:
        where = ref(doc)
        for path, value in _strings(doc):
            if ZERO_DIGEST in value:
                out.append(
                    Finding("warn", "placeholder-digest", where, f"{path} has the all-zero digest")
                )
            for host in INVALID_HOST.findall(value):
                out.append(
                    Finding(
                        "warn", "placeholder-host", where, f"{path} names {host}; set the real one"
                    )
                )
            if any(d in value for d in EXAMPLE_DIGESTS) or EXAMPLE_HOST.search(value):
                out.append(
                    Finding("warn", "example-value", where, f"{path} still has the example's value")
                )
        for path, cidr in _cidrs(doc):
            if _within(cidr, [TEST_NET_1]):
                out.append(
                    Finding(
                        "warn", "placeholder-cidr", where, f"{path} is the TEST-NET peer {cidr}"
                    )
                )
            elif _within(cidr, EXAMPLE_NETS):
                out.append(
                    Finding("warn", "example-value", where, f"{path} is the example's peer {cidr}")
                )
        if doc.get("kind") == "Job" and doc["metadata"]["name"] == f"{MIGRATE_PREFIX}0":
            out.append(
                Finding(
                    "warn",
                    "placeholder-job",
                    where,
                    "the committed name; name it dps-migrate-<release> (a Job is immutable)",
                )
            )
    return out


# --- denials: hardening --------------------------------------------------------------------


def _pod_findings(kind: str, name: str, pod: dict[str, Any]) -> Iterator[Finding]:
    where = f"{kind}/{name}"
    sc = pod.get("securityContext", {}) or {}
    if sc.get("runAsNonRoot") is not True:
        yield Finding("deny", "pod-security", where, "securityContext.runAsNonRoot must be true")
    if (sc.get("seccompProfile") or {}).get("type") != "RuntimeDefault":
        yield Finding("deny", "pod-security", where, "seccompProfile.type must be RuntimeDefault")
    if "runAsUser" in sc:
        yield Finding("deny", "pod-security", where, "runAsUser is set (OpenShift assigns the UID)")
    if pod.get("automountServiceAccountToken") is not False:
        yield Finding("deny", "pod-security", where, "automountServiceAccountToken must be false")
    if pod.get("enableServiceLinks") is not False:
        # Services named dps-* would inject DPS_*_PORT/... variables that Settings reads.
        yield Finding("deny", "pod-security", where, "enableServiceLinks must be false")
    for flag in ("hostNetwork", "hostPID", "hostIPC"):
        if pod.get(flag):
            yield Finding("deny", "pod-security", where, f"{flag} is set")
    volumes = {v["name"]: v for v in pod.get("volumes", []) or []}
    for vol in volumes.values():
        if "hostPath" in vol:
            yield Finding("deny", "pod-security", where, f"volume {vol['name']} is a hostPath")
    for c in containers(pod):
        cname = f"{where} container {c.get('name')}"
        csc = c.get("securityContext", {}) or {}
        caps = csc.get("capabilities", {}) or {}
        if csc.get("allowPrivilegeEscalation") is not False:
            yield Finding("deny", "container-security", cname, "allowPrivilegeEscalation: false")
        if csc.get("readOnlyRootFilesystem") is not True:
            yield Finding("deny", "container-security", cname, "readOnlyRootFilesystem: true")
        if caps.get("drop") != ["ALL"] or caps.get("add"):
            yield Finding("deny", "container-security", cname, "capabilities: drop [ALL], add none")
        if csc.get("privileged") or "runAsUser" in csc:
            yield Finding("deny", "container-security", cname, "privileged or runAsUser is set")
        res = c.get("resources", {}) or {}
        for part in ("requests", "limits"):
            if not {"cpu", "memory"} <= set(res.get(part, {}) or {}):
                yield Finding("deny", "resources", cname, f"cpu and memory {part} are required")
        if kind not in RUN_TO_COMPLETION and c in pod.get("containers", []):
            if not (c.get("livenessProbe") and c.get("readinessProbe")):
                yield Finding("deny", "probes", cname, "liveness and readiness probes are required")
        if "@sha256:" not in str(c.get("image", "")):
            yield Finding("deny", "image-digest", cname, "the image must be pinned by digest")
        for source in c.get("envFrom", []) or []:
            if "secretRef" in source:
                yield Finding(
                    "deny", "literal-secret", cname, "envFrom secretRef: mount it instead"
                )
        mounts = [(m["mountPath"], volumes.get(m["name"], {})) for m in c.get("volumeMounts", [])]
        for entry in c.get("env", []) or []:
            env_name = str(entry.get("name"))
            if "value" in entry and is_secret_setting(env_name):
                yield Finding("deny", "literal-secret", cname, f"{env_name} has a literal value")
            if env_name.startswith("DPS_") and "secretKeyRef" in (entry.get("valueFrom") or {}):
                yield Finding(
                    "deny", "literal-secret", cname, f"{env_name} from a secretKeyRef: use _FILE"
                )
            if env_name.endswith("_FILE") and "value" in entry:
                path = str(entry["value"])
                owners = [v for mp, v in mounts if path.startswith(mp.rstrip("/") + "/")]
                if not owners or not all("secret" in v or "configMap" in v for v in owners):
                    yield Finding(
                        "deny", "secret-file", cname, f"{env_name} is not under a mounted secret"
                    )


def hardening_findings(docs: Sequence[dict[str, Any]]) -> list[Finding]:
    """A ``deny`` per hardening violation."""
    out: list[Finding] = []
    for kind, name, pod in workloads(docs):
        out.extend(_pod_findings(kind, name, pod))
        secrets = {
            v["secret"].get("secretName") for v in pod.get("volumes", []) or [] if "secret" in v
        }
        if OWNER_SECRET in secrets and not is_migrate_workload(kind, name):
            out.append(
                Finding(
                    "deny",
                    "owner-secret",
                    f"{kind}/{name}",
                    f"only the migrate Job and CronJob/{MAINTAIN_CRONJOB} mount {OWNER_SECRET}",
                )
            )
    for account in objects(docs, "ServiceAccount"):
        if account.get("automountServiceAccountToken") is not False:
            out.append(
                Finding(
                    "deny", "service-account", ref(account), "automountServiceAccountToken: false"
                )
            )
    for cm in objects(docs, "ConfigMap"):
        # every key, not only DPS_*: a ConfigMap taken as envFrom feeds the libraries too
        for key in cm.get("data") or {}:
            if is_secret_setting(str(key)):
                out.append(Finding("deny", "literal-secret", ref(cm), f"{key} is a secret setting"))
    policies = objects(docs, "NetworkPolicy")
    if not any(
        p["metadata"]["name"] == "default-deny"
        and p["spec"].get("podSelector") == {}
        and set(p["spec"].get("policyTypes", [])) == {"Ingress", "Egress"}
        for p in policies
    ):
        out.append(Finding("deny", "default-deny", "NetworkPolicy/default-deny", "missing"))
    env = configmap_data(docs).get("DPS_ENV", "")
    if env in ("staging", "prod"):
        for doc in docs:
            if PROD_FORBIDDEN.search(str(doc.get("metadata", {}).get("name", ""))):
                out.append(
                    Finding("deny", "prod-mocks", ref(doc), f"no mock or source system in {env}")
                )
    return out


# --- denials: consistency (provenance, the database host) -----------------------------------


def _short(digest: str) -> str:
    return digest[:19] + "…" if len(digest) > 19 else digest


def image_digest_findings(docs: Sequence[dict[str, Any]]) -> list[Finding]:
    """A ``deny`` per DPS container whose ``DPS_IMAGE_DIGEST`` is not its own image's digest.

    Evidence packs carry the setting (job packs from the worker, scan packs from the browser
    worker), so each container must name the image it runs: the browser worker overrides the
    ConfigMap's app digest in its env. The zero placeholder is left to ``placeholder-digest``
    (a warning) and an image without a digest to ``image-digest``.
    """
    out: list[Finding] = []
    for kind, name, pod in workloads(docs):
        for c in containers(pod):
            entries = [e for e in c.get("env", []) or [] if e.get("name") == DIGEST_SETTING]
            if not (reads_dps_configmap(c) or entries):
                continue  # not a DPS process
            where = f"{kind}/{name} container {c.get('name')}"
            if any("value" not in e for e in entries):
                out.append(
                    Finding(
                        "deny",
                        "image-digest-setting",
                        where,
                        f"{DIGEST_SETTING} must be a literal value (the check compares it with "
                        "the image)",
                    )
                )
                continue
            image = str(c.get("image", ""))
            if "@sha256:" not in image:
                continue
            digest = image.rsplit("@", 1)[1]
            value = pod_environment(docs, c).get(DIGEST_SETTING, "")
            if not value:
                out.append(
                    Finding(
                        "deny",
                        "image-digest-setting",
                        where,
                        f"{DIGEST_SETTING} is not set: evidence would name no image (set this "
                        "container's image digest)",
                    )
                )
            elif value != ZERO_DIGEST and value != digest:
                out.append(
                    Finding(
                        "deny",
                        "image-digest-setting",
                        where,
                        f"{DIGEST_SETTING} {_short(value)} is not this container's image digest "
                        f"{_short(digest)}: evidence would name another build",
                    )
                )
    return out


def _command_line(container: dict[str, Any]) -> str:
    return " ".join(
        str(p) for p in [*(container.get("command") or []), *(container.get("args") or [])]
    )


def schema_wait_findings(docs: Sequence[dict[str, Any]]) -> list[Finding]:
    """A ``deny`` per long-running DPS workload that can start before its release's schema.

    Compose starts the app and the workers after its ``migrate`` one-shot completed; Kubernetes
    applies the migrate Job and the Deployments together. The app verifies the schema once at boot
    (one that starts too early keeps its job platform down and stays Ready) and the workers exit on
    drift, so each such pod waits in an init container that runs ``python -m dps.migrate --verify``
    until it passes, with the image of its DPS container and the ``dps`` ConfigMap: the migrations
    that image ships, checked against the database it uses.
    """
    out: list[Finding] = []
    for kind, name, pod in workloads(docs):
        main = dps_container(pod)
        if kind in RUN_TO_COMPLETION or main is None:
            continue  # the migrate Job and CronJob apply the schema; mocks are not DPS
        waits = [
            c for c in pod.get("initContainers", []) or [] if SCHEMA_VERIFY in _command_line(c)
        ]
        if not waits:
            message = (
                f"no init container runs `{SCHEMA_VERIFY}` until it passes: a pod that starts "
                "before the release's migrate Job has committed keeps its job platform down (app) "
                "or crash-loops (workers)"
            )
        elif not any(c.get("image") == main.get("image") and reads_dps_configmap(c) for c in waits):
            message = (
                f"the schema wait must run {main.get('name')}'s image with the {DPS_CONFIGMAP} "
                "ConfigMap (the migrations that image ships, against the database it uses)"
            )
        else:
            continue
        out.append(Finding("deny", "schema-wait", f"{kind}/{name}", message))
    return out


def database_host_findings(docs: Sequence[dict[str, Any]]) -> list[Finding]:
    """A ``deny`` when ``DPS_PG_HOST`` is not the ``dps-db`` Service's externalName.

    DPS dials ``DPS_PG_HOST`` (verify-full TLS checks the certificate against the name dialled);
    ``dps-db`` is the in-namespace alias of the same server, and the README keeps them equal.
    """
    host = configmap_data(docs).get("DPS_PG_HOST")
    out: list[Finding] = []
    for service in objects(docs, "Service"):
        spec = service.get("spec", {}) or {}
        if service["metadata"]["name"] != DB_SERVICE or spec.get("type") != "ExternalName":
            continue
        if host is not None and host != spec.get("externalName"):
            out.append(
                Finding(
                    "deny",
                    "db-host",
                    f"ConfigMap/{DPS_CONFIGMAP}",
                    f"DPS_PG_HOST is not Service/{DB_SERVICE}'s externalName: set both to the "
                    "platform PostgreSQL host",
                )
            )
    return out


# --- configuration: Settings and the production posture guard --------------------------------

_PROBE = """
import json, sys
try:
    from dps.config import Settings
    from dps.posture import posture_problems
except ModuleNotFoundError:
    print(json.dumps({"skipped": "the dps package is not importable"}))
    raise SystemExit(0)
try:
    settings = Settings(_env_file=None)
except Exception as exc:  # a validation error: field names only, never an input value
    errors = getattr(exc, "errors", None)
    fields = sorted({".".join(str(p) for p in e.get("loc", ())) for e in errors()}) if callable(errors) else []
    print(json.dumps({"problems": ["Settings refused the configuration: " + type(exc).__name__ + " " + ", ".join(fields)]}))
    raise SystemExit(0)
print(json.dumps({"problems": posture_problems(settings) if sys.argv[1] == "posture" else []}))
"""


def _dummy_secret(env_name: str) -> str:
    """A well-formed stand-in for a mounted secret file (never a real value)."""
    if env_name.startswith("DPS_JOB_KEK"):
        return base64.b64encode(bytes(32)).decode("ascii")
    if env_name == "DPS_API_KEYS_FILE":
        entry = {"name": "ops", "key_sha256": "0" * 64, "tenant_ids": ["*"], "scopes": ["admin"]}
        return json.dumps([entry])
    return "x"


def settings_problems(
    docs: Sequence[dict[str, Any]],
    workload: str,
    *,
    posture: bool,
    container: str | None = None,
) -> list[str] | None:
    """Build a DPS container's Settings in a clean subprocess; with ``posture``, also the guard.

    ``container`` names one of the pod's containers or init containers (default: its DPS
    container). The environment is the ConfigMap plus that container's literal env (nothing from
    the caller's environment); every ``*_FILE`` points at a dummy file. ``None`` when ``dps`` is not
    importable.
    """
    pod = next((p for _, n, p in workloads(docs) if n == workload), None)
    if pod is None:
        raise KeyError(workload)
    if container is None:
        target = dps_container(pod)
        if target is None:
            return []
    else:
        found = [c for c in containers(pod) if c.get("name") == container]
        if not found:
            raise KeyError(f"{workload}/{container}")
        target = found[0]
    env = pod_environment(docs, target)
    with tempfile.TemporaryDirectory(prefix="dps-openshift-check-") as tmp:
        for key in [k for k in env if k.endswith("_FILE")]:
            dummy = Path(tmp) / key.lower()
            dummy.write_text(_dummy_secret(key), "utf-8")
            env[key] = str(dummy)
        proc = subprocess.run(
            [sys.executable, "-I", "-c", _PROBE, "posture" if posture else "settings"],
            env={"PATH": os.environ.get("PATH", ""), **env, "HOME": tmp},
            cwd=tmp,
            capture_output=True,
            text=True,
            timeout=180,
        )
    lines = proc.stdout.strip().splitlines()
    if proc.returncode != 0 or not lines:
        # the exception class only: a traceback line could quote a source line or a value
        last = (proc.stderr.strip().splitlines() or ["no output"])[-1]
        return [f"settings probe failed: {last.split(':', 1)[0]}"]
    result = json.loads(lines[-1])
    if "skipped" in result:
        return None
    problems: list[str] = result["problems"]
    return problems


def configuration_findings(docs: Sequence[dict[str, Any]]) -> list[Finding]:
    """Settings for every DPS container, init containers included (the schema wait runs
    ``dps.migrate``, which builds them from its own env); the posture guard for the DPS container
    of each Deployment in staging/prod (the process that serves or works)."""
    out: list[Finding] = []
    production = configmap_data(docs).get("DPS_ENV", "") in ("staging", "prod")
    for kind, name, pod in workloads(docs):
        main = dps_container(pod)
        for c in containers(pod):
            if not reads_dps_configmap(c):
                continue
            guarded = production and kind == "Deployment" and c is main
            problems = settings_problems(docs, name, posture=guarded, container=str(c.get("name")))
            if problems is None:
                return [
                    Finding(
                        "warn",
                        "settings-skipped",
                        "-",
                        "dps is not importable here: run from the repository's venv to check "
                        "Settings and the posture guard",
                    )
                ]
            where = f"{kind}/{name}" if c is main else f"{kind}/{name} container {c.get('name')}"
            out.extend(Finding("deny", "posture", where, p) for p in problems)
    return out


def check(docs: Sequence[dict[str, Any]], *, settings: bool = True) -> list[Finding]:
    """Every finding for a render: hardening and configuration denials, placeholder warnings."""
    findings = hardening_findings(docs) + image_digest_findings(docs) + database_host_findings(docs)
    findings += schema_wait_findings(docs)
    findings += placeholder_findings(docs)
    if settings:
        findings += configuration_findings(docs)
    return findings


# --- JSON6902 patches (the "test before replace" rule) -----------------------------------------


def json6902_patches(kustomization: Path) -> list[list[dict[str, Any]]]:
    """The JSON6902 operation lists a kustomization applies (from files or inline)."""
    spec = yaml.safe_load(kustomization.read_text("utf-8")) or {}
    out: list[list[dict[str, Any]]] = []
    for patch in spec.get("patches", []) or []:
        if "path" in patch:
            body = yaml.safe_load((kustomization.parent / patch["path"]).read_text("utf-8"))
        else:
            body = yaml.safe_load(patch.get("patch", "") or "null")
        if isinstance(body, list) and all(isinstance(op, dict) and "op" in op for op in body):
            out.append(body)
    return out


# --- CLI -----------------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """``python tools/openshift_check.py <dir|rendered.yaml|-> [--fail-on-warn]``."""
    parser = argparse.ArgumentParser(prog="python tools/openshift_check.py", description=__doc__)
    parser.add_argument("target", help="a kustomization directory, a rendered YAML file, or -")
    parser.add_argument("--fail-on-warn", action="store_true", help="the release gate")
    parser.add_argument(
        "--settings",
        choices=("on", "off"),
        default="on",
        help="build each workload's Settings and run the posture guard (needs dps importable)",
    )
    args = parser.parse_args(argv)
    try:
        if args.target == "-":
            docs = load_documents(sys.stdin.read())
        else:
            docs = render(Path(args.target))
    except (RenderError, OSError, yaml.YAMLError) as exc:
        print(f"render failed: {exc}", file=sys.stderr)
        return 2
    findings = check(docs, settings=args.settings == "on")
    for finding in sorted(findings, key=lambda f: (f.level != "deny", f.rule, f.resource)):
        print(finding)
    denies = sum(f.level == "deny" for f in findings)
    warns = sum(f.level == "warn" for f in findings)
    print(f"{len(docs)} objects: {denies} denied, {warns} warnings")
    return 1 if denies or (warns and args.fail_on_warn) else 0


if __name__ == "__main__":
    raise SystemExit(main())
