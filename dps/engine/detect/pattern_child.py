"""A matcher process for policy-supplied patterns (S3 review round 2, R2-B1).

:mod:`dps.engine.detect.regex_guard` starts it as ``python -I <this file>`` and sends it one
pattern run at a time: a pattern, its flags, a text and a CPU limit. It answers with every match
span (``finditer`` order), or that the limit ran out, and the CPU time the run took.

The ``regex`` module's ``timeout`` counts the CPU of the whole **process**. Inside a request
thread of the server that clock also runs for every other thread, so a legitimate run was cut short
whenever other requests were busy. This process runs nothing else — one run at a time, one thread —
so here the limit is exactly the run's own CPU time, whatever the server is doing.

It imports only the standard library and ``regex`` (never ``dps``) and writes nothing but its
binary answers to stdout; it exits when stdin closes (the server is gone). Texts travel as UTF-8
with ``surrogatepass``, so any Python ``str`` survives and the spans index the same string. A
``RLIMIT_CPU`` set before each compile and each run is a backstop should the matcher ever miss its
own limit: the kernel ends the process and the server refuses the run (fail closed); the process
never writes a core (``RLIMIT_CORE`` 0: a core would hold request texts — review round 3, N-r3-3).

Compiling a pattern it has not seen (a deny list's trie is the largest) is part of the run's
charge (review round 3, R3-B2): the answer reports its CPU (``compile_cpu``) beside the run's, and
the server charges it to the call like a deny-list build.
"""

from __future__ import annotations

import struct
import sys
import time
from array import array
from collections import OrderedDict
from typing import BinaryIO

import regex

#: request: pattern bytes, text bytes, ``regex`` flags, CPU limit in seconds
REQUEST = struct.Struct("<IIId")
#: answer: status, CPU seconds the run took, CPU seconds compiling the pattern took (0 when it was
#: cached), number of spans (then 2 x int64 per span)
ANSWER = struct.Struct("<BddQ")
OK, TIMED_OUT, FAILED = 0, 1, 2
#: compiled patterns kept (a policy's patterns come back for every text it scans)
CACHE_SIZE = 64
#: seconds past a run's limit before ``RLIMIT_CPU`` ends the process (the backstop)
RLIMIT_SLACK = 2


def _read(stream: BinaryIO, size: int) -> bytes | None:
    """Exactly ``size`` bytes, or ``None`` at the end of the stream."""
    chunks: list[bytes] = []
    while size:
        chunk = stream.read(size)
        if not chunk:
            return None
        chunks.append(chunk)
        size -= len(chunk)
    return b"".join(chunks)


def _cpu_backstop(limit: float) -> None:
    """End the process (SIGXCPU) if this run outlives its limit by :data:`RLIMIT_SLACK`."""
    try:
        import resource  # noqa: PLC0415 - POSIX only

        _soft, hard = resource.getrlimit(resource.RLIMIT_CPU)
        soft = int(time.process_time() + limit) + RLIMIT_SLACK
        if hard != resource.RLIM_INFINITY:
            soft = min(soft, hard)
        resource.setrlimit(resource.RLIMIT_CPU, (soft, hard))
    except (ImportError, OSError, ValueError):  # no backstop: the matcher's own limit holds
        pass


def _no_core_dumps() -> None:
    """Never write a core: SIGXCPU (the backstop) dumps one by default, and it would hold the
    request texts this process was matching."""
    try:
        import resource  # noqa: PLC0415 - POSIX only

        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except (ImportError, OSError, ValueError):  # no such limit here: nothing to dump into either
        pass


def main() -> None:
    """Serve runs until stdin closes."""
    _no_core_dumps()
    reader, writer = sys.stdin.buffer, sys.stdout.buffer
    cache: OrderedDict[tuple[str, int], regex.Pattern[str]] = OrderedDict()
    while True:
        head = _read(reader, REQUEST.size)
        if head is None:
            return
        pattern_len, text_len, flags, limit = REQUEST.unpack(head)
        raw_pattern = _read(reader, pattern_len)
        raw_text = _read(reader, text_len)
        if raw_pattern is None or raw_text is None:
            return
        pattern = raw_pattern.decode("utf-8", "surrogatepass")
        text = raw_text.decode("utf-8", "surrogatepass")
        spans = array("q")
        status, used, compiling = OK, 0.0, 0.0
        try:
            compiled = cache.get((pattern, flags))
            if compiled is None:
                _cpu_backstop(limit)
                started = time.process_time()
                compiled = regex.compile(pattern, flags)
                compiling = time.process_time() - started
                cache[(pattern, flags)] = compiled
                if len(cache) > CACHE_SIZE:
                    cache.popitem(last=False)
            else:
                cache.move_to_end((pattern, flags))
            _cpu_backstop(limit)
            started = time.process_time()
            try:
                for match in compiled.finditer(text, timeout=limit):
                    spans.extend(match.span())
            except TimeoutError:
                status, spans = TIMED_OUT, array("q")
            used = time.process_time() - started
        except Exception:  # noqa: BLE001 - value-free: the server refuses the run (fail closed)
            status, spans = FAILED, array("q")
        writer.write(ANSWER.pack(status, used, compiling, len(spans) // 2))
        writer.write(spans.tobytes())
        writer.flush()


if __name__ == "__main__":
    main()
