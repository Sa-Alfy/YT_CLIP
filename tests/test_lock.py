#!/usr/bin/env python3
"""Lock test: a second run must refuse while one holds run.lock.

Run from the pipeline root: ./venv/bin/python tests/test_lock.py
Holds the lock in a background process, then runs run_pipeline.py
--dry-run (which must exit 2 fast, before any discovery).
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = os.path.join(ROOT, "venv", "bin", "python")

holder = subprocess.Popen(
    [PY, "-c",
     "import sys; sys.path.insert(0, '.'); import run_pipeline, time; "
     # The lock lives in the CONFIG dir (tests/), same dir the dry-run
     # below will use — that is the lock that must be held.
     "fh = run_pipeline.acquire_lock('tests', 'locktest'); "
     "assert fh is not None; time.sleep(20)"],
    cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(2)  # let the holder take the lock
try:
    r = subprocess.run(
        [PY, "run_pipeline.py", "--config", "tests/config.test.yaml",
         "--clips", "1", "--dry-run"],
        cwd=ROOT, capture_output=True, text=True, timeout=60)
    ok = r.returncode == 2 and "already in progress" in (r.stdout + r.stderr)
    print(f"{'PASS' if ok else 'FAIL'}  second run refused while lock held "
          f"(rc={r.returncode})")
    if not ok:
        print("stdout:", r.stdout[-500:])
        print("stderr:", r.stderr[-500:])
        sys.exit(1)
finally:
    holder.terminate()
    holder.wait(timeout=10)

# After the holder dies the lock must be acquirable again (flock is
# released automatically — a stale lock cannot exist).
r = subprocess.run(
    [PY, "-c",
     "import sys; sys.path.insert(0, '.'); import run_pipeline; "
     "fh = run_pipeline.acquire_lock('tests', 'locktest2'); "
     "print('LOCK_FREE' if fh else 'LOCK_STUCK'); fh.close()"],
    cwd=ROOT, capture_output=True, text=True, timeout=30)
ok = "LOCK_FREE" in r.stdout
print(f"{'PASS' if ok else 'FAIL'}  lock released after holder exits")
if not ok:
    print(r.stdout, r.stderr)
    sys.exit(1)
print("lock tests passed")
