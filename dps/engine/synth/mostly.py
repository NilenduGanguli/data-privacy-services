"""Optional MOSTLY AI engine (spec §5.6, D7; ``synth-ml`` extra, Apache-2.0 SDK, local mode).

The SDK trains a generator locally (TabularARGN) and is not deterministic per row anchor, so the
adapter trains once per job on the fit sample, generates a **pool** of ``mostly_pool_rows``
synthetic rows, and serves rows from the pool by a keyed hash of the row anchor — deterministic
and resumable like the copula (contract §6.6 ``TabularSampler``). With more source rows than pool
rows, synthetic rows repeat (the evidence records ``tabular.pool_rows``); fidelity and privacy are
measured on the preview exactly as for the copula. The pool is synthetic but derived from real
data, so it is sealed with the job KEK exactly like the copula model and purged with the key.

**Differential privacy** (Task 7B): with a :class:`DpConfig` the trainer adds the SDK's
differential-privacy block to the table's model configuration (:func:`_dp_configuration` is the
only place that names its keys); the sampler records ``dp`` for the evidence (``tabular.dp``).

The SDK is never imported at module import time (:func:`mostly_available` checks without importing
torch); :func:`_sdk_trainer` (and :func:`dps.engine.synth.linked._sdk_linked_trainer`) are the only
functions that touch it. Errors never quote a value.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import tempfile
import zlib
from collections.abc import Callable, Mapping, Sequence, Set
from datetime import date, datetime
from decimal import Decimal
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from dps.engine.metrics import row_digest

FORMAT = "dps.mostly.v1"


class DpConfig(BaseModel):
    """The SDK's differential-privacy budget for one training run (spec §5.6)."""

    model_config = ConfigDict(frozen=True, hide_input_in_errors=True)
    max_epsilon: float = Field(gt=0)
    delta: float = Field(gt=0, lt=1)


#: ``(columns by name, rows to generate, training minutes, DP budget or None) → generated rows``.
Trainer = Callable[[dict[str, list[Any]], int, int, DpConfig | None], list[dict[str, Any]]]
_find_spec = importlib.util.find_spec  # module attribute so tests can simulate a missing extra
_NULL_TEXT = frozenset({"NaT", "nan", "NaN", "<NA>", "None"})


class TabularUnavailable(RuntimeError):
    """The ``synth-ml`` extra is missing, or the generator or a pool blob is unusable."""


def mostly_available() -> bool:
    """True when the ``synth-ml`` extra is importable (checked without importing torch)."""
    try:
        return _find_spec("mostlyai") is not None and _find_spec("pandas") is not None
    except (ImportError, ValueError):
        return False


def _dp_configuration(dp: DpConfig) -> dict[str, Any]:
    """The SDK's ``DifferentialPrivacyConfig`` block for a table's model configuration — the only
    place that names its keys (verify them with ``help()`` in the ``ml`` run)."""
    return {"differential_privacy": {"max_epsilon": dp.max_epsilon, "delta": dp.delta}}


def model_configuration(minutes: int, dp: DpConfig | None) -> dict[str, Any]:
    """A table's ``tabular_model_configuration``: the training budget and, with DP, its block."""
    config: dict[str, Any] = {"max_training_time": minutes}
    if dp is not None:
        config |= _dp_configuration(dp)
    return config


def dp_report(dp: DpConfig | None, achieved_epsilon: float | None = None) -> dict[str, Any]:
    """The evidence's ``tabular.dp`` block (value-free)."""
    if dp is None:
        return {"enabled": False}
    return {
        "enabled": True,
        "max_epsilon": dp.max_epsilon,
        "delta": dp.delta,
        "achieved_epsilon": achieved_epsilon,
    }


def _sdk_trainer(
    data: dict[str, list[Any]], size: int, minutes: int, dp: DpConfig | None = None
) -> list[dict[str, Any]]:
    """Train a local generator and draw ``size`` rows.

    The only function that touches the SDK for one table — if an SDK release renames a
    parameter, this is the one place to change (``help(MostlyAI)``).

    Raises:
        TabularUnavailable: the extra (``mostlyai``, ``pandas``) is not installed.
    """
    try:
        import pandas as pd
        from mostlyai.sdk import MostlyAI
    except ImportError as exc:
        raise TabularUnavailable("the synth-ml extra (mostlyai, pandas) is not installed") from exc
    with tempfile.TemporaryDirectory(prefix="dps-mostly-") as workdir:
        mostly = MostlyAI(local=True, local_dir=workdir)
        generator = mostly.train(
            config={
                "name": "dps-tabular",
                "tables": [
                    {
                        "name": "rows",
                        "data": pd.DataFrame(data),
                        "tabular_model_configuration": model_configuration(minutes, dp),
                    }
                ],
            },
            start=True,
            wait=True,
        )
        generated = mostly.generate(generator, size=size, start=True, wait=True).data()
        # The SDK types data() as one frame or a frame per table; this generator has one table.
        frame = generated.get("rows") if isinstance(generated, dict) else generated
        if not isinstance(frame, pd.DataFrame):
            raise TabularUnavailable("the generator did not return the generated table")
        return [dict(r) for r in frame.to_dict(orient="records")]


def _is_null(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    return isinstance(value, str) and value in _NULL_TEXT


def coerce(value: Any, kind: str, scale: int) -> Any:
    """A generated value (what pandas hands back) as its column kind's Python type.

    Raises:
        ValueError, TypeError, OverflowError: the value cannot be read as ``kind`` (the caller
            replaces the message: it may quote the value).
    """
    if _is_null(value):
        return None
    if kind == "int":
        return int(round(float(value)))
    if kind == "decimal":
        return Decimal(f"{float(value):.{max(0, scale)}f}")
    if kind == "float":
        out = float(value)
        return None if math.isnan(out) else out
    if kind == "bool":
        return value if isinstance(value, bool) else str(value).strip().lower() in ("true", "1")
    if kind in ("date", "timestamp"):
        text = value.isoformat() if hasattr(value, "isoformat") else str(value)
        moment = datetime.fromisoformat(text)
        return moment.date() if kind == "date" else moment
    return str(value)


def _encode(value: Any) -> Any:
    if isinstance(value, Decimal):
        return {"d": str(value)}
    if isinstance(value, datetime):
        return {"t": value.isoformat()}
    if isinstance(value, date):
        return {"D": value.isoformat()}
    return value


def _decode(value: Any) -> Any:
    if isinstance(value, dict):
        if "d" in value:
            return Decimal(value["d"])
        if "t" in value:
            return datetime.fromisoformat(value["t"])
        if "D" in value:
            return date.fromisoformat(value["D"])
    return value


def coerce_rows(
    raw: Sequence[Mapping[str, Any]],
    names: Sequence[str],
    kinds: Mapping[str, str],
    scales: Mapping[str, int],
) -> list[list[Any]]:
    """Generated rows → typed rows in ``names`` order (``kinds``/``scales`` keyed ``$.NAME``).

    Raises:
        TabularUnavailable: a generated value its column cannot hold (value-free message).
    """
    try:
        return [
            [coerce(r.get(n), kinds[f"$.{n}"], scales.get(f"$.{n}", 0)) for n in names] for r in raw
        ]
    except (ValueError, TypeError, OverflowError, ArithmeticError):
        raise TabularUnavailable(
            "the MOSTLY AI generator returned a value its column cannot hold"
        ) from None


class MostlySampler:
    """A pool of generated rows served by a keyed hash of the row anchor (``TabularSampler``)."""

    engine: ClassVar[str] = "mostly"

    def __init__(
        self,
        names: list[str],
        pool: list[list[Any]],
        *,
        seed: bytes,
        reject: set[int],
        reject_columns: Sequence[str],
        dp: dict[str, Any] | None = None,
    ) -> None:
        self._names, self._pool = list(names), pool
        self.dp: dict[str, Any] = dict(dp or {"enabled": False})
        self._pick_key = hashlib.blake2b(seed, digest_size=32, person=b"dps.mostly.pick").digest()
        self._reject_key = hashlib.blake2b(seed, digest_size=32, person=b"dps.mostly.rej").digest()
        self._reject = reject
        self.reject_columns = list(reject_columns)

    @classmethod
    def fit(
        cls,
        columns: dict[str, list[Any]],
        kinds: dict[str, str],
        *,
        seed: bytes,
        pool_rows: int,
        max_training_minutes: int,
        scales: Mapping[str, int] | None = None,
        reject_rows: Sequence[Mapping[str, Any]] = (),
        reject_columns: Sequence[str] = (),
        trainer: Trainer | None = None,
        dp: DpConfig | None = None,
    ) -> MostlySampler:
        """Train on ``columns`` (path ``$.NAME`` → values) and generate the pool (with ``dp``, under
        the SDK's differential privacy).

        Raises:
            TabularUnavailable: the extra is missing, the generator returned no row, or a value
                its column cannot hold.
        """
        names = sorted(p[2:] for p in columns)
        data = {p[2:]: list(v) for p, v in columns.items()}
        raw = (trainer or _sdk_trainer)(data, pool_rows, max_training_minutes, dp)
        if not raw:
            raise TabularUnavailable("the MOSTLY AI generator returned no rows")
        pool = coerce_rows(raw, names, kinds, scales or {})
        sampler = cls(
            names, pool, seed=seed, reject=set(), reject_columns=reject_columns, dp=dp_report(dp)
        )
        if sampler.reject_columns:
            sampler._reject = {sampler.row_digest(r) for r in reject_rows}
        return sampler

    @property
    def columns(self) -> list[str]:
        """The column names the sampler writes."""
        return list(self._names)

    @property
    def pool_rows(self) -> int:
        """How many generated rows the pool holds."""
        return len(self._pool)

    def sample_rows(self, anchors: Sequence[str], *, attempt: int = 0) -> list[dict[str, Any]]:
        """One pool row per anchor (keyed BLAKE2b pick; ``attempt`` salts a redraw)."""
        out: list[dict[str, Any]] = []
        for anchor in anchors:
            digest = hashlib.blake2b(
                f"{anchor}\x1f{attempt}".encode(), key=self._pick_key, digest_size=8
            ).digest()
            row = self._pool[int.from_bytes(digest, "big") % len(self._pool)]
            out.append(dict(zip(self._names, row, strict=True)))
        return out

    def sample_row(self, anchor: str) -> dict[str, Any]:
        """Contract §6.6 single-row form of :meth:`sample_rows`."""
        return self.sample_rows([anchor])[0]

    def row_digest(self, row: Mapping[str, Any]) -> int:
        """Keyed digest of ``row`` over the reject columns."""
        return row_digest(row, self.reject_columns, self._reject_key)

    def is_rejected(self, row: Mapping[str, Any], *, extra: Set[int] = frozenset()) -> bool:
        """True when ``row`` equals a real row of the fit sample/holdout (or one in ``extra``)."""
        if not self.reject_columns:
            return False
        digest = self.row_digest(row)
        return digest in self._reject or digest in extra

    def to_bytes(self) -> bytes:
        """zlib-compressed JSON; the seed is NOT included (it lives in the JobSecret)."""
        body = {
            "format": FORMAT,
            "names": self._names,
            "pool": [[_encode(v) for v in r] for r in self._pool],
            "reject": sorted(self._reject),
            "reject_columns": self.reject_columns,
            "dp": self.dp,
        }
        return zlib.compress(json.dumps(body, separators=(",", ":")).encode("utf-8"), 6)

    @classmethod
    def from_bytes(cls, data: bytes, seed: bytes) -> MostlySampler:
        """Rebuild a pool sampler; ``seed`` is the job's synthesis seed bytes.

        Raises:
            TabularUnavailable: the bytes are not a pool of this format (no detail).
        """
        try:
            body = json.loads(zlib.decompress(data).decode("utf-8"))
            if not isinstance(body, dict) or body.get("format") != FORMAT:
                raise TabularUnavailable("unsupported pool format")
            names = [str(n) for n in body["names"]]
            pool = [[_decode(v) for v in r] for r in body["pool"]]
            if not pool or any(len(r) != len(names) for r in pool):
                raise TabularUnavailable("unsupported pool format")
            return cls(
                names,
                pool,
                seed=seed,
                reject={int(x) for x in body["reject"]},
                reject_columns=[str(c) for c in body["reject_columns"]],
                dp=body.get("dp") if isinstance(body.get("dp"), dict) else None,
            )
        except TabularUnavailable:
            raise
        except (zlib.error, ValueError, KeyError, TypeError, ArithmeticError):
            raise TabularUnavailable("unsupported pool format") from None
