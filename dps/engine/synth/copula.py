"""Gaussian-copula tabular synthesizer (spec §5.6, contract §6.6).

**Fit.** Per column: empirical quantiles on a 201-point grid for numbers, dates (ordinal days) and
timestamps (epoch seconds), with the two extreme grid points replaced by the 0.1 % / 99.9 %
quantiles so no single outlier's exact value is stored or emitted; frequency tables for
categories and booleans, where a category is kept only if it has at least
``max(min_category_count, ⌈rare_floor · n⌉)`` rows and the folded mass is redistributed over the
kept ones (rare values are never emitted); null rates. The copula is the Pearson correlation of
normal scores — ranks for numbers, a jittered point inside the category's probability interval
for categories — over pairwise-complete rows, repaired to the nearest positive-definite
correlation matrix.

**Sample.** For a row anchor, 2·d uniforms come from a keyed BLAKE2b stream (same seed + anchor →
same row on every machine and numpy version); the first d become correlated normals through the
Cholesky factor and are mapped back through each marginal's inverse CDF; the second d decide
nulls. ``attempt`` > 0 salts the stream so a rejected row can be redrawn deterministically.

**Reject set.** Keyed 64-bit digests of real rows (fit sample + holdout); a synthetic row equal
to any of them on every reject column (the non-key output columns) is rejected by the caller
(``is_rejected``).

The model embeds quantiles and category labels of real data. It is sealed with the job KEK,
never logged, never stored unsealed and purged with the job key. Serialisation is a compressed
``npz`` read back with ``allow_pickle=False``; the seed is never part of it. Errors name columns,
kinds and counts — never a value.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import zipfile
from collections import Counter
from collections.abc import Mapping, Sequence, Set
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, ClassVar

import numpy as np
from scipy import special, stats

from dps.engine.metrics import canonical, row_digest, to_float

FORMAT = "dps.copula.v1"
QUANTILE_POINTS = 201
TAIL = 0.001
MIN_ROWS = 50
MIN_CATEGORY_COUNT = 5
MAX_CATEGORIES = 500
MIN_PAIR_ROWS = 10
NUMERIC_KINDS = frozenset({"int", "decimal", "float", "date", "timestamp"})
KINDS = NUMERIC_KINDS | {"bool", "categorical"}
_TWO_53 = 9007199254740992.0
_P_EDGE = 1e-12
_MAX_ORDINAL = date.max.toordinal()


class CopulaError(ValueError):
    """The sample (or a model blob) cannot be used. Messages name columns and counts, never values."""


def _subkey(seed: bytes, label: str) -> bytes:
    """A 32-byte key for one purpose (``fit``, ``sample``, ``reject``) derived from the seed."""
    return hashlib.blake2b(
        label.encode("utf-8"), key=seed[:64], digest_size=32, person=b"dps.copula.key"
    ).digest()


def uniforms(key: bytes, anchor: str, count: int) -> np.ndarray:
    """``count`` uniforms in (0, 1) from a keyed BLAKE2b stream over ``anchor``.

    Each 64-byte block gives eight 53-bit uniforms, so a shorter request is a prefix of a longer
    one for the same key and anchor.
    """
    blocks = -(-count // 8)
    raw = b"".join(
        hashlib.blake2b(
            f"{anchor}\x1f{i}".encode(), key=key[:64], digest_size=64, person=b"dps.copula.v1"
        ).digest()
        for i in range(blocks)
    )
    ints = np.frombuffer(raw, dtype=">u8")[:count].astype(np.uint64)
    return ((ints >> np.uint64(11)).astype(np.float64) + 0.5) / _TWO_53


def nearest_correlation(matrix: np.ndarray, floor: float = 1e-6) -> np.ndarray:
    """Clip eigenvalues to ``floor`` and rescale to a unit diagonal (Higham's one-step repair)."""
    sym = (matrix + matrix.T) / 2.0
    values, vectors = np.linalg.eigh(sym)
    fixed = (vectors * np.clip(values, floor, None)) @ vectors.T
    scale = np.sqrt(np.clip(np.diag(fixed), floor, None))
    fixed = fixed / np.outer(scale, scale)
    np.fill_diagonal(fixed, 1.0)
    return fixed


def _cholesky(matrix: np.ndarray) -> np.ndarray:
    try:
        return np.linalg.cholesky(matrix)
    except np.linalg.LinAlgError:
        eye = np.eye(len(matrix))
        return np.linalg.cholesky(nearest_correlation(matrix + 1e-6 * eye, floor=1e-4))


def _label(value: Any, kind: str) -> str:
    if kind == "bool":
        return "true" if bool(value) else "false"
    return value if isinstance(value, str) else str(canonical(value))


@dataclass
class Marginal:
    """One column's marginal distribution (quantile grid or category table, and a null rate)."""

    path: str
    kind: str
    null_rate: float
    quantiles: np.ndarray | None = None
    categories: list[str] | None = None
    cum: np.ndarray | None = None
    scale: int = 0
    tz_aware: bool = False

    @property
    def name(self) -> str:
        """The column name (the path without ``$.``)."""
        return self.path[2:]

    def inverse(self, p: np.ndarray) -> list[Any]:
        """Map probabilities in (0, 1) to typed values of this column."""
        if self.kind in NUMERIC_KINDS:
            if self.quantiles is None:
                raise CopulaError(f"model column {self.path} has no quantiles")
            grid = np.linspace(0.0, 1.0, len(self.quantiles))
            return [self._typed(float(x)) for x in np.interp(p, grid, self.quantiles)]
        if self.categories is None or self.cum is None:
            raise CopulaError(f"model column {self.path} has no categories")
        last = len(self.categories) - 1
        idx = np.minimum(np.searchsorted(self.cum, p, side="right"), last)
        labels = [self.categories[int(i)] for i in idx]
        return [lab == "true" for lab in labels] if self.kind == "bool" else labels

    def _typed(self, x: float) -> Any:
        if self.kind == "int":
            return int(round(x))
        if self.kind == "decimal":
            return Decimal(f"{x:.{max(0, self.scale)}f}")
        if self.kind == "date":
            return date.fromordinal(min(_MAX_ORDINAL, max(1, int(round(x)))))
        if self.kind == "timestamp":
            moment = datetime.fromtimestamp(round(x, 6), tz=UTC)
            return moment if self.tz_aware else moment.replace(tzinfo=None)
        return x


def _fit_numeric(
    path: str, kind: str, values: Sequence[Any], scale: int
) -> tuple[Marginal, np.ndarray] | str:
    raw = [to_float(v) for v in values]
    mask = np.array([x is not None for x in raw], dtype=bool)
    x = np.array([v for v in raw if v is not None], dtype=float)
    if x.size == 0:
        return "all_null"
    q = np.quantile(x, np.linspace(0.0, 1.0, QUANTILE_POINTS))
    q[0], q[-1] = np.quantile(x, [TAIL, 1.0 - TAIL])
    q = np.maximum.accumulate(q)
    z = np.full(len(values), np.nan)
    z[mask] = special.ndtri(stats.rankdata(x, method="average") / (x.size + 1))
    tz_aware = kind == "timestamp" and any(
        isinstance(v, datetime) and v.tzinfo is not None for v in values
    )
    marginal = Marginal(
        path, kind, float(1.0 - mask.mean()), quantiles=q, scale=scale, tz_aware=tz_aware
    )
    return marginal, z


def _fit_categorical(
    path: str,
    kind: str,
    values: Sequence[Any],
    *,
    rare_floor: float,
    min_count: int,
    jitter: np.ndarray,
) -> tuple[Marginal, np.ndarray] | str:
    labels = [None if v is None else _label(v, kind) for v in values]
    counts = Counter(lab for lab in labels if lab is not None)
    nonnull = sum(counts.values())
    if nonnull == 0:
        return "all_null"
    threshold = max(min_count, math.ceil(rare_floor * nonnull))
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:MAX_CATEGORIES]
    kept = [(c, k) for c, k in ranked if k >= threshold]
    if not kept:
        return "no_frequent_category"
    total = float(sum(k for _, k in kept))
    cum = np.cumsum([k / total for _, k in kept])
    cum[-1] = 1.0
    lo = np.concatenate([[0.0], cum[:-1]])
    pos = {c: i for i, (c, _) in enumerate(kept)}
    z = np.full(len(values), np.nan)
    for r, lab in enumerate(labels):
        if lab is not None and lab in pos:
            i = pos[lab]
            p = lo[i] + (cum[i] - lo[i]) * jitter[r]
            z[r] = special.ndtri(min(max(p, _P_EDGE), 1.0 - _P_EDGE))
    null_rate = float(sum(lab is None for lab in labels) / len(labels))
    marginal = Marginal(path, kind, null_rate, categories=[c for c, _ in kept], cum=cum)
    return marginal, z


class GaussianCopula:
    """See the module docstring. Construct with :meth:`fit` or :meth:`from_bytes`."""

    engine: ClassVar[str] = "copula"

    def __init__(
        self,
        marginals: list[Marginal],
        chol: np.ndarray,
        reject: set[int],
        *,
        seed: bytes,
        excluded: dict[str, str],
        fit_rows: int,
        reject_columns: Sequence[str],
    ) -> None:
        self._marginals = marginals
        self._chol = chol
        self._reject = reject
        self._sample_key = _subkey(seed, "sample")
        self._reject_key = _subkey(seed, "reject")
        self._null_rates = np.array([m.null_rate for m in marginals], dtype=float)
        self.excluded = dict(excluded)
        self.fit_rows = fit_rows
        self.reject_columns = list(reject_columns)

    # -- fit -------------------------------------------------------------------------------
    @classmethod
    def fit(
        cls,
        columns: dict[str, list[Any]],
        kinds: dict[str, str],
        *,
        rare_floor: float,
        seed: bytes,
        scales: Mapping[str, int] | None = None,
        reject_rows: Sequence[Mapping[str, Any]] = (),
        reject_columns: Sequence[str] = (),
        min_rows: int = MIN_ROWS,
        min_category_count: int = MIN_CATEGORY_COUNT,
    ) -> GaussianCopula:
        """Fit marginals and the copula on ``columns`` (path ``$.NAME`` → values, equal lengths).

        ``kinds`` maps each path to ``int | decimal | float | date | timestamp | bool |
        categorical``; ``scales`` gives decimal columns their output scale. ``reject_rows``
        (column name → value) become the keyed reject set over ``reject_columns``.

        Raises:
            CopulaError: an unknown kind, a path without a kind, unequal column lengths, fewer
                than ``min_rows`` rows, or no column that can be modelled.
        """
        missing = sorted(p for p in columns if p not in kinds)
        if missing:
            raise CopulaError(f"no kind given for {', '.join(missing)}")
        unknown = sorted(p for p in columns if kinds[p] not in KINDS)
        if unknown:
            raise CopulaError(f"unsupported column kinds for {', '.join(unknown)}")
        lengths = {len(v) for v in columns.values()}
        if len(lengths) > 1:
            raise CopulaError("every column must have the same number of rows")
        n = lengths.pop() if lengths else 0
        if n < min_rows:
            raise CopulaError(f"tabular synthesis needs at least {min_rows} rows, got {n}")
        rng = np.random.default_rng(int.from_bytes(_subkey(seed, "fit")[:8], "big"))
        marginals: list[Marginal] = []
        scores: list[np.ndarray] = []
        excluded: dict[str, str] = {}
        for path in sorted(columns):
            kind = kinds[path]
            fitted: tuple[Marginal, np.ndarray] | str
            if kind in NUMERIC_KINDS:
                fitted = _fit_numeric(path, kind, columns[path], (scales or {}).get(path, 0))
            else:
                fitted = _fit_categorical(
                    path,
                    kind,
                    columns[path],
                    rare_floor=rare_floor,
                    min_count=min_category_count,
                    jitter=rng.uniform(0.05, 0.95, n),
                )
            if isinstance(fitted, str):
                excluded[path] = fitted
                continue
            marginals.append(fitted[0])
            scores.append(fitted[1])
        if not marginals:
            raise CopulaError("no column can be modelled (all null or no frequent category)")
        z = np.column_stack(scores)
        finite = np.isfinite(z)
        corr = np.eye(len(marginals))
        for i in range(len(marginals)):
            for j in range(i + 1, len(marginals)):
                both = finite[:, i] & finite[:, j]
                a, b = z[both, i], z[both, j]
                if both.sum() >= MIN_PAIR_ROWS and a.std() > 0 and b.std() > 0:
                    corr[i, j] = corr[j, i] = float(np.corrcoef(a, b)[0, 1])
        model = cls(
            marginals,
            _cholesky(nearest_correlation(corr)),
            set(),
            seed=seed,
            excluded=excluded,
            fit_rows=n,
            reject_columns=reject_columns,
        )
        if model.reject_columns:
            model._reject = {model.row_digest(r) for r in reject_rows}
        return model

    # -- sample ----------------------------------------------------------------------------
    @property
    def columns(self) -> list[str]:
        """The column names the sampler writes."""
        return [m.name for m in self._marginals]

    @property
    def correlation(self) -> np.ndarray:
        """The (repaired) copula correlation matrix, in :attr:`columns` order."""
        return self._chol @ self._chol.T

    @property
    def reject_size(self) -> int:
        """How many real-row digests the reject set holds."""
        return len(self._reject)

    def sample_rows(self, anchors: Sequence[str], *, attempt: int = 0) -> list[dict[str, Any]]:
        """One synthetic row per anchor (column name → typed value); deterministic."""
        if not anchors:
            return []
        d = len(self._marginals)
        salt = "" if attempt == 0 else f"\x1eretry{attempt}"
        u = np.vstack([uniforms(self._sample_key, a + salt, 2 * d) for a in anchors])
        p = np.clip(special.ndtr(special.ndtri(u[:, :d]) @ self._chol.T), _P_EDGE, 1.0 - _P_EDGE)
        nulls = u[:, d:] < self._null_rates[None, :]
        rows: list[dict[str, Any]] = [{} for _ in anchors]
        for j, marginal in enumerate(self._marginals):
            for r, value in enumerate(marginal.inverse(p[:, j])):
                rows[r][marginal.name] = None if nulls[r, j] else value
        return rows

    def sample_row(self, anchor: str) -> dict[str, Any]:
        """Contract §6.6 single-row form of :meth:`sample_rows`."""
        return self.sample_rows([anchor])[0]

    def row_digest(self, row: Mapping[str, Any]) -> int:
        """Keyed digest of ``row`` over the reject columns (non-key output columns)."""
        return row_digest(row, self.reject_columns, self._reject_key)

    def is_rejected(self, row: Mapping[str, Any], *, extra: Set[int] = frozenset()) -> bool:
        """True when ``row`` equals a real row of the fit sample/holdout (or one in ``extra``)."""
        if not self.reject_columns:
            return False
        digest = self.row_digest(row)
        return digest in self._reject or digest in extra

    # -- (de)serialise -----------------------------------------------------------------------
    def to_bytes(self) -> bytes:
        """Compressed npz; the seed is NOT included (it lives in the JobSecret)."""
        meta = {
            "format": FORMAT,
            "fit_rows": self.fit_rows,
            "excluded": self.excluded,
            "reject_columns": self.reject_columns,
            "marginals": [
                {
                    "path": m.path,
                    "kind": m.kind,
                    "null_rate": m.null_rate,
                    "categories": m.categories,
                    "scale": m.scale,
                    "tz_aware": m.tz_aware,
                }
                for m in self._marginals
            ],
        }
        arrays: dict[str, np.ndarray] = {
            "meta": np.frombuffer(json.dumps(meta, sort_keys=True).encode("utf-8"), dtype=np.uint8),
            "chol": self._chol,
            "reject": np.array(sorted(self._reject), dtype=np.uint64),
        }
        for i, m in enumerate(self._marginals):
            if m.quantiles is not None:
                arrays[f"q{i}"] = m.quantiles
            if m.cum is not None:
                arrays[f"c{i}"] = m.cum
        buffer = io.BytesIO()
        np.savez_compressed(buffer, **arrays)  # type: ignore[arg-type]  # numeric arrays only
        return buffer.getvalue()

    @classmethod
    def from_bytes(cls, data: bytes, seed: bytes) -> GaussianCopula:
        """Rebuild a model; ``seed`` is the job's synthesis seed bytes.

        Raises:
            CopulaError: the bytes are not a model of this format (no detail about the content).
        """
        try:
            loaded = np.load(io.BytesIO(data), allow_pickle=False)
            if not isinstance(loaded, np.lib.npyio.NpzFile):
                raise CopulaError("model bytes could not be read")
            with loaded as archive:
                meta = json.loads(archive["meta"].tobytes().decode("utf-8"))
                if meta.get("format") != FORMAT:
                    raise CopulaError("unsupported model format")
                marginals: list[Marginal] = []
                for i, m in enumerate(meta["marginals"]):
                    if m["kind"] not in KINDS:
                        raise CopulaError("unsupported model format")
                    marginals.append(
                        Marginal(
                            path=str(m["path"]),
                            kind=str(m["kind"]),
                            null_rate=float(m["null_rate"]),
                            quantiles=archive[f"q{i}"] if f"q{i}" in archive.files else None,
                            categories=m["categories"],
                            cum=archive[f"c{i}"] if f"c{i}" in archive.files else None,
                            scale=int(m["scale"]),
                            tz_aware=bool(m["tz_aware"]),
                        )
                    )
                chol = np.asarray(archive["chol"], dtype=float)
                if chol.shape != (len(marginals), len(marginals)) or not marginals:
                    raise CopulaError("unsupported model format")
                return cls(
                    marginals,
                    chol,
                    {int(x) for x in archive["reject"]},
                    seed=seed,
                    excluded=dict(meta["excluded"]),
                    fit_rows=int(meta["fit_rows"]),
                    reject_columns=[str(c) for c in meta["reject_columns"]],
                )
        except CopulaError:
            raise
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            EOFError,
            zipfile.BadZipFile,
        ) as exc:
            raise CopulaError("model bytes could not be read") from exc
