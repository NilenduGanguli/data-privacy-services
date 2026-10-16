"""``tools/bench_connector.py`` defaults to the documented size per engine (M3 G6 review N2).

``--engine oracle|mongo`` used to default to 200 000 rows in 50 000-row partitions, the PostgreSQL
figure: a bare Oracle run reseeded the shared demo customer space (the smoke test's data) to
200 000 rows on Oracle Free. The M3 benchmark is 20 000 rows in 5 000-row partitions; that is now
the default for those engines, and an explicit value still wins.
"""

from __future__ import annotations

from tools import bench_connector


def test_engine_defaults_follow_the_documented_benchmarks() -> None:
    pg = bench_connector.parse_args([])
    assert (pg.rows, pg.partition_rows) == (200_000, 50_000)
    for engine in ("oracle", "mongo"):
        args = bench_connector.parse_args(["--engine", engine])
        assert (args.rows, args.partition_rows) == (20_000, 5_000), engine


def test_explicit_sizes_win() -> None:
    args = bench_connector.parse_args(
        ["--engine", "oracle", "--rows", "1000", "--partition-rows", "500"]
    )
    assert (args.rows, args.partition_rows) == (1000, 500)
    pg = bench_connector.parse_args(["--rows", "30000"])
    assert (pg.rows, pg.partition_rows) == (30_000, 50_000)
