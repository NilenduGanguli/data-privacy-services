"""Worker roles (contract §3.5): the handler context and handler signatures.

Task 23 adds the ``Consumer`` (claim / heartbeat / reaper / LISTEN loops), ``build_context``,
``serve_health`` and ``main`` (``python -m dps.worker``). Handlers live next to their layer:
``dps.layers.connectors.discovery.discover_job``, ``dps.layers.connectors.jobs_run``
(``prepare``, ``run_partition``, ``finalize``) and ``dps.retention.retention_job``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from dps.audit import AuditSink
from dps.config import Settings
from dps.engine.service import PrivacyEngine
from dps.jobs import ClaimedJob, ClaimedPartition, JobInternal
from dps.storage import ArtefactService


@dataclass
class HandlerContext:
    """What every handler gets: settings, the engine, this worker's id, the job KEK, artefacts
    and the audit sink. ``kek`` is key material: it is never logged or put in an error."""

    settings: Settings
    engine: PrivacyEngine
    worker_id: str
    kek: bytes
    artefacts: ArtefactService
    audit: AuditSink

    def __repr__(self) -> str:  # never show the KEK
        return f"HandlerContext(worker_id={self.worker_id!r})"


#: A job-row handler (discover, run prepare/finalize, retention, scan); the job is leased.
Handler = Callable[[HandlerContext, ClaimedJob], Awaitable[None]]
#: A partition handler; the partition is leased, its job row is not.
PartitionHandler = Callable[[HandlerContext, JobInternal, ClaimedPartition], Awaitable[None]]
