"""The matcher processes that run long policy-pattern runs (S3 review round 2, R2-B1).

:class:`dps.engine.detect.regex_guard.PatternBudget` runs a pattern in the calling thread for a
short slice first; a run that needs longer moves here, to a small pool of child processes
(:mod:`dps.engine.detect.pattern_child`), each running one pattern at a time on one thread. There
the ``regex`` module's timeout — a **process** CPU clock — is exactly the run's own CPU time, so a
run is stopped on its own work and never on what other requests of the server are doing, and the
server's GIL and event loop stay free while it runs.

The pool holds at most :func:`configure_pattern_processes` children (``DPS_PATTERN_PROCESSES``,
default 2), started on first use; a caller waits for a free one. So long pattern runs occupy at
most that many CPUs of a server process, however many requests carry them. A child that dies, hangs
or answers out of protocol is killed and replaced, and the run is reported as failed — the caller
refuses it (fail closed), never skips it. Children get an empty environment (no ``DPS_*`` setting
or secret reaches them), run in isolated mode (``python -I``), print nothing and exit when the
server process does (their stdin closes).
"""

from __future__ import annotations

import os
import select
import subprocess
import sys
import threading
import time
from array import array
from pathlib import Path

from dps.engine.detect.pattern_child import ANSWER, OK, REQUEST, TIMED_OUT
from dps.observability import get_logger

logger = get_logger(__name__)

_CHILD = str(Path(__file__).with_name("pattern_child.py"))
#: default number of matcher processes per server process (``DPS_PATTERN_PROCESSES``)
DEFAULT_PROCESSES = 2
#: a child that has not answered after ``WALL_GRACE_SECONDS + WALL_FACTOR x`` its CPU limit is
#: stuck and is killed (a busy host stretches wall time, never the CPU a run may use)
WALL_GRACE_SECONDS = 60.0
WALL_FACTOR = 20.0


class MatcherFailed(Exception):
    """No child process could run the pattern (start failure, crash, hang); value-free."""


class RunResult:
    """A child's answer: ``spans`` (``None`` when the limit ran out) and the CPU the run took."""

    __slots__ = ("cpu", "spans")

    def __init__(self, spans: list[tuple[int, int]] | None, cpu: float) -> None:
        self.spans = spans
        self.cpu = cpu


def _encode(value: str) -> bytes:
    return value.encode("utf-8", "surrogatepass")


class _Matcher:
    """One child process; one thread at a time uses it (the pool hands it out)."""

    def __init__(self) -> None:
        self.proc = subprocess.Popen(  # noqa: S603 - this interpreter and our own file, no shell
            [sys.executable, "-I", _CHILD],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env={},
            close_fds=True,
        )
        assert self.proc.stdin is not None and self.proc.stdout is not None
        self._in = self.proc.stdin
        self._out = self.proc.stdout.fileno()
        # poll, not select: a busy server's descriptors pass FD_SETSIZE (1024)
        self._poll = select.poll()
        self._poll.register(self._out, select.POLLIN | select.POLLHUP | select.POLLERR)

    def run(self, pattern: str, flags: int, text: str, limit: float) -> RunResult:
        raw_pattern, raw_text = _encode(pattern), _encode(text)
        deadline = time.monotonic() + WALL_GRACE_SECONDS + WALL_FACTOR * limit
        try:
            self._in.write(REQUEST.pack(len(raw_pattern), len(raw_text), flags, limit))
            self._in.write(raw_pattern)
            self._in.write(raw_text)
            self._in.flush()
        except (OSError, ValueError):
            raise MatcherFailed from None
        status, cpu, count = ANSWER.unpack(self._read(ANSWER.size, deadline))
        raw_spans = self._read(16 * count, deadline) if count else b""
        if status == TIMED_OUT:
            return RunResult(None, cpu)
        if status != OK:
            raise MatcherFailed
        flat = array("q")
        flat.frombytes(raw_spans)
        return RunResult(list(zip(flat[0::2], flat[1::2], strict=True)), cpu)

    def _read(self, size: int, deadline: float) -> bytes:
        chunks: list[bytes] = []
        while size:
            wait = deadline - time.monotonic()
            if wait <= 0 or not self._poll.poll(max(1, int(wait * 1000))):
                raise MatcherFailed
            chunk = os.read(self._out, min(size, 1 << 20))
            if not chunk:
                raise MatcherFailed  # the child is gone
            chunks.append(chunk)
            size -= len(chunk)
        return b"".join(chunks)

    def close(self) -> None:
        """Kill the child (it may be mid-run) and reap it."""
        try:
            self.proc.kill()
        except OSError:
            pass
        for stream in (self.proc.stdin, self.proc.stdout):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass
        try:
            self.proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass


class MatcherPool:
    """At most ``size`` matcher processes, started on first use, shared by every thread."""

    def __init__(self, size: int = DEFAULT_PROCESSES) -> None:
        self._size = max(1, size)
        self._idle: list[_Matcher] = []
        self._count = 0
        self._cond = threading.Condition()

    @property
    def size(self) -> int:
        """The most children this pool runs at once."""
        return self._size

    def resize(self, size: int) -> None:
        """Change the most children run at once; idle ones above the new size are stopped."""
        surplus: list[_Matcher] = []
        with self._cond:
            self._size = max(1, size)
            while self._idle and self._count > self._size:
                surplus.append(self._idle.pop())
                self._count -= 1
            self._cond.notify_all()
        for matcher in surplus:
            matcher.close()

    def run(self, pattern: str, flags: int, text: str, limit: float) -> RunResult:
        """Run ``pattern`` over ``text`` in a child, with a CPU limit of ``limit`` seconds.

        Raises:
            MatcherFailed: no child could run it (start failure, crash, hang).
        """
        matcher = self._acquire()
        try:
            result = matcher.run(pattern, flags, text, limit)
        except BaseException:
            self._discard(matcher)
            raise
        self._release(matcher)
        return result

    def _acquire(self) -> _Matcher:
        with self._cond:
            while not self._idle and self._count >= self._size:
                self._cond.wait()
            if self._idle:
                return self._idle.pop()
            self._count += 1
        try:
            if not sys.executable:
                raise OSError("no interpreter to start")
            return _Matcher()
        except (OSError, ValueError):
            with self._cond:
                self._count -= 1
                self._cond.notify()
            logger.error("pattern_matcher_start_failed")
            raise MatcherFailed from None

    def _release(self, matcher: _Matcher) -> None:
        with self._cond:
            surplus = self._count > self._size
            if surplus:
                self._count -= 1
            else:
                self._idle.append(matcher)
            self._cond.notify()
        if surplus:
            matcher.close()

    def _discard(self, matcher: _Matcher) -> None:
        logger.warning("pattern_matcher_replaced")
        matcher.close()
        with self._cond:
            self._count -= 1
            self._cond.notify()

    def close(self) -> None:
        """Stop the idle children (tests; at exit their stdin closes and they end anyway)."""
        with self._cond:
            idle, self._idle = self._idle, []
            self._count -= len(idle)
        for matcher in idle:
            matcher.close()

    def _after_fork(self) -> None:
        """In a forked process the parent's children are not ours (and a lock may be held)."""
        self._cond = threading.Condition()
        self._idle, self._count = [], 0


POOL = MatcherPool()
os.register_at_fork(after_in_child=POOL._after_fork)


def configure_pattern_processes(size: int) -> None:
    """Set how many matcher processes this server process may run (``DPS_PATTERN_PROCESSES``)."""
    POOL.resize(size)
