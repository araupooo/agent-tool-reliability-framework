"""Live probe: confirm the modelled ThreadPoolExecutor semantics against
a real executor.

Uses a real ``concurrent.futures.ThreadPoolExecutor`` with real (short)
sleeps. Asserts ONLY boolean outcomes and completion order — no
wall-clock thresholds — so the probe is stable: task durations (0.5 s)
are much longer than the 0.1 s settle delay before the cancel attempts.

Observed (this run, see the log):
- ``Future.cancel()`` on a queued future returns True.
- ``Future.cancel()`` on a running future returns False.
- ``wait(timeout=...)`` returns without cancelling anything: the
  running future is still not done after the wait, and it completes
  normally afterwards.
- ``shutdown(cancel_futures=True)`` cancels queued futures only.

Run from the pack root: python3 components/threaded_dispatcher/probe_live.py
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, wait


def work(name: str, seconds: float) -> str:
    time.sleep(seconds)
    return f"{name}: done"


def main() -> int:
    print("== probe: live ThreadPoolExecutor semantics ==")
    with ThreadPoolExecutor(max_workers=1) as executor:
        f1 = executor.submit(work, "task-1", 0.5)
        f2 = executor.submit(work, "task-2", 0.5)
        f3 = executor.submit(work, "task-3", 0.5)
        time.sleep(0.1)  # let task-1 start; task-2 and task-3 are queued
        print(f"task-1 running: {f1.running()}")
        print(f"task-2 running: {f2.running()}")
        cancel_queued = f2.cancel()
        print(f"cancel queued task-2: {cancel_queued}")
        cancel_running = f1.cancel()
        print(f"cancel running task-1: {cancel_running}")
        done, not_done = wait([f1], timeout=0.05)
        print(f"wait(timeout=0.05) done_count: {len(done)} not_done_count: {len(not_done)}")
        print(f"wait(timeout=0.05) task-1 done: {f1.done()}")
        print(f"task-2 cancelled: {f2.cancelled()}")
        print(f"task-1 result after wait: {f1.result()}")
        print(f"task-3 result (ran after task-1): {f3.result()}")

    # Second executor: shutdown(cancel_futures=True) cancels queued only.
    with ThreadPoolExecutor(max_workers=1) as executor:
        g1 = executor.submit(work, "task-a", 0.5)
        g2 = executor.submit(work, "task-b", 0.5)
        time.sleep(0.1)
        executor.shutdown(wait=True, cancel_futures=True)
        print(f"shutdown(cancel_futures=True): running task-a result: {g1.result()}")
        print(f"shutdown(cancel_futures=True): queued task-b cancelled: {g2.cancelled()}")

    ok = True

    def check(condition: bool, label: str) -> None:
        nonlocal ok
        print(f"{'PASS' if condition else 'FAIL'}: {label}")
        if not condition:
            ok = False

    check(cancel_queued is True, "cancel() on a queued future returns True")
    check(cancel_running is False, "cancel() on a running future returns False")
    check(f1.done(), "wait(timeout) did not cancel the running task; it completed")
    check(f2.cancelled(), "queued task stayed cancelled")
    check(g2.cancelled(), "shutdown(cancel_futures=True) cancels a queued future")
    check(g1.result() == "task-a: done", "shutdown(cancel_futures=True) does not stop a running future")
    print("probe live ThreadPoolExecutor: ALL PASS" if ok else "probe: FAILURES PRESENT")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
