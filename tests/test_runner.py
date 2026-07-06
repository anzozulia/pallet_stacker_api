"""Hard-timeout runner tests (D6) — the subprocess kill path. Needs the core."""
from __future__ import annotations

import pytest

pytest.importorskip("pallet_packer")

from pallet_api.solver.runner import run_with_hard_timeout  # noqa: E402


def test_hard_timeout_kills_and_reports_timeout(fast_cfg, pack_payload):
    # A real solve takes seconds (spawn + import + BRKGA); a 0.1s hard cap can
    # never be met, so it must be killed and reported as timeout.
    out = run_with_hard_timeout(pack_payload(5, budget=5), fast_cfg,
                                hard_timeout_s=0.1)
    assert out["status"] == "timeout"
    assert out["error"]["code"] == "solve_timeout"


def test_done_returns_result(fast_cfg, pack_payload):
    out = run_with_hard_timeout(pack_payload(4, budget=5), fast_cfg,
                                hard_timeout_s=60)
    assert out["status"] == "done"
    assert "input_summary" in out["result"]


def test_gate_violation_maps_to_invalid_input(fast_cfg, pack_payload):
    out = run_with_hard_timeout(pack_payload(2, bad=True), fast_cfg,
                                hard_timeout_s=60)
    assert out["status"] == "failed"
    assert out["error"]["code"] == "invalid_input"
    assert out["error"]["problems"]


def test_non_gate_exception_maps_to_solver_error(fast_cfg):
    # Missing 'pallet' -> KeyError inside solve (not a PackingInputError).
    out = run_with_hard_timeout({"boxes": [{"id": "B1", "length": 10,
                                            "width": 10, "height": 10}]},
                                fast_cfg, hard_timeout_s=60)
    assert out["status"] == "failed"
    assert out["error"]["code"] == "solver_error"


# ------------------------------------------------- round 6 (F32): the
# deadline-boundary drain. A result enqueued in the sub-ms window AT the
# hard deadline used to be dropped (`timeout`) — or, if the child had
# put-then-exited, a SUCCESSFUL solve was mislabeled `solver_crashed`.
# The race window is sub-millisecond, so these use the `_ctx` seam with a
# scripted Queue/Process instead of racing a real subprocess.
import queue as _q  # noqa: E402


class _StubQueue:
    """get() always times out (the loop never sees the result); the
    post-deadline get_nowait() finds it — the exact F32 window."""
    def __init__(self, item):
        self._item = item

    def get(self, timeout=None):
        raise _q.Empty

    def get_nowait(self):
        if self._item is None:
            raise _q.Empty
        item, self._item = self._item, None
        return item


class _StubProcess:
    def __init__(self, alive_during_loop=True):
        self._alive = alive_during_loop
        self.killed = False

    def start(self):
        pass

    def is_alive(self):
        return self._alive

    def terminate(self):
        self._alive = False

    def kill(self):
        self.killed = True
        self._alive = False

    def join(self, timeout=None):
        self._alive = False


class _StubCtx:
    def __init__(self, item, alive):
        self._item = item
        self._alive = alive

    def Queue(self):
        return _StubQueue(self._item)

    def Process(self, target=None, args=(), daemon=None):
        return _StubProcess(self._alive)


def test_result_at_deadline_is_drained_not_timeout():
    # Child alive at the deadline with its result already queued: was
    # `timeout` (result discarded), must be `done`.
    ctx = _StubCtx(item=("done", {"ok": 1}), alive=True)
    out = run_with_hard_timeout({}, {}, hard_timeout_s=0.3, _ctx=ctx)
    assert out == {"status": "done", "result": {"ok": 1}}


def test_put_then_exit_at_deadline_is_done_not_crashed():
    # Child enqueued its result and exited inside the final slice: was
    # mislabeled `solver_crashed`, must be `done`.
    ctx = _StubCtx(item=("done", {"ok": 2}), alive=False)
    out = run_with_hard_timeout({}, {}, hard_timeout_s=0.3, _ctx=ctx)
    assert out == {"status": "done", "result": {"ok": 2}}


def test_empty_queue_envelopes_unchanged():
    # Genuinely empty queue: the timeout / crashed envelopes must be
    # exactly as before the drain was added.
    out = run_with_hard_timeout({}, {}, hard_timeout_s=0.3,
                                _ctx=_StubCtx(item=None, alive=True))
    assert out["status"] == "timeout"
    assert out["error"]["code"] == "solve_timeout"
    out = run_with_hard_timeout({}, {}, hard_timeout_s=0.3,
                                _ctx=_StubCtx(item=None, alive=False))
    assert out["status"] == "failed"
    assert out["error"]["code"] == "solver_crashed"
