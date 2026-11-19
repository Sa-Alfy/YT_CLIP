#!/usr/bin/env python3
"""Shared subprocess helper for the viral clip pipeline.

Every subprocess call in the pipeline goes through run(): it enforces
a timeout and, on timeout, kills the WHOLE process group. yt-dlp and
ffmpeg spawn children of their own — killing only the direct child
would leave them running and hang the run (in v1 a stalled segment
download hung for ~8 min with 0 bytes written).
"""
import os
import signal
import subprocess


class CommandError(Exception):
    """A command finished with a non-zero exit code."""

    def __init__(self, what, returncode, stdout, stderr):
        super().__init__(f"{what} failed (exit {returncode})")
        self.what = what
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class CommandTimeout(Exception):
    """A command did not finish within its timeout (process group killed)."""

    def __init__(self, what, timeout, stdout, stderr):
        super().__init__(
            f"{what} timed out after {timeout}s (process group killed)")
        self.what = what
        self.timeout = timeout
        self.stdout = stdout
        self.stderr = stderr


def run(cmd, what, timeout=None):
    """Run cmd, capture output, enforce timeout.

    The child is started in its own process group (start_new_session).
    On timeout the whole group is SIGKILLed, so no orphaned yt-dlp /
    ffmpeg children survive. Returns subprocess.CompletedProcess.
    Raises CommandError on non-zero exit, CommandTimeout on timeout.
    """
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, start_new_session=True)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        out, err = proc.communicate()
        raise CommandTimeout(what, timeout, out, err)
    if proc.returncode != 0:
        raise CommandError(what, proc.returncode, out, err)
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)
