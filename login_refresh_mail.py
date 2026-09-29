"""Local Apple Mail forwarding prerequisite; no mail content or credentials read.

The host owns DCF background launch and inbox connectivity validation. This guard
never runs open/osascript, never activates or quits Mail, and never claims that a
running process proves the source account is online. Run prepare on a worker thread.
"""
from __future__ import annotations

import math
import os
import subprocess
import sys
import time
from collections.abc import Callable

from login_refresh_core import RefreshError

MAIL_EXECUTABLE = '/System/Applications/Mail.app/Contents/MacOS/Mail'


def mac_mail_instance() -> str | None:
    """Read-only probe of the exact Apple Mail executable and its process start time."""
    if sys.platform != 'darwin':
        raise RefreshError('mail_probe_unavailable')
    try:
        result = subprocess.run(
            ['/bin/ps', '-axo', 'pid=,lstart=,comm='],
            capture_output=True, text=True, timeout=5, check=True,
            env={**os.environ, 'LC_ALL': 'C'},
        )
    except (OSError, subprocess.SubprocessError, UnicodeError):
        raise RefreshError('mail_probe_unavailable') from None
    matches = []
    for line in result.stdout.splitlines():
        fields = line.strip().split(None, 6)
        if len(fields) == 7 and fields[6] == MAIL_EXECUTABLE and fields[0].isdigit():
            matches.append(':'.join(fields[:6]))
    if len(matches) > 1:
        raise RefreshError('mail_instance_ambiguous')
    return matches[0] if matches else None


class MailForwardingGate:
    """Prepare once, recheck before every code request and each mail-poll iteration.

    open_background is an optional host-supplied DCF action. It must report exactly
    True only on a policy-approved, non-activating background start. No such action
    is wired by this module. Without it a closed Mail pauses BEFORE any code request.
    The action is attempted at most once per gate. A gate that has observed Mail
    running never relaunches it after closure; the user may have intentionally quit.
    """
    def __init__(self, probe: Callable[[], str | None] = mac_mail_instance, *,
                 open_background: Callable[[], bool] | None = None,
                 warmup_seconds: float = 15, timeout_seconds: float = 90,
                 poll_seconds: float = 1, max_gap_seconds: float = 60,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        values = (warmup_seconds, timeout_seconds, poll_seconds, max_gap_seconds)
        if (any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in values)
                or not 0 <= warmup_seconds < timeout_seconds <= 180
                or not 0 < poll_seconds <= 5
                or not poll_seconds < max_gap_seconds <= 300):
            raise RefreshError('invalid_mail_preflight_config')
        self.probe = probe
        self.open_background = open_background
        self.warmup = warmup_seconds
        self.timeout = timeout_seconds
        self.poll = poll_seconds
        self.max_gap = max_gap_seconds
        self.clock, self.sleep = clock, sleep
        self._ready_instance: str | None = None
        self._last_check: float | None = None
        self._launch_attempted = False
        self._observed_running = False

    def _probe(self) -> str | None:
        try:
            value = self.probe()
        except Exception:
            raise RefreshError('mail_probe_unavailable') from None
        if value is not None and (not isinstance(value, str) or not value):
            raise RefreshError('mail_probe_unavailable')
        return value

    def invalidate(self):
        self._ready_instance = None
        self._last_check = None

    def prepare(self):
        """Settle a stable process before collecting the Gmail baseline/request time.

        This is process readiness only, NOT verification of mailbox online state,
        sync completion or forwarding delivery. Those remain separate acceptance gates.
        """
        start = self.clock()
        if not math.isfinite(start):
            raise RefreshError('mail_clock_invalid')
        try:
            initial = self._probe()
        except RefreshError:
            self.invalidate()
            raise
        if initial is not None:
            self._observed_running = True
        if self._ready_instance == initial and initial is not None and self._last_check is not None:
            elapsed = start - self._last_check
            if 0 <= elapsed <= self.max_gap:
                self._last_check = start
                return
        self.invalidate()
        if initial is None:
            if self._observed_running:
                raise RefreshError('mail_stopped_resume_required')
            if self.open_background is None:
                raise RefreshError('mail_background_start_unavailable')
            if self._launch_attempted:
                raise RefreshError('mail_start_already_attempted')
            self._launch_attempted = True
            try:
                accepted = self.open_background()
            except Exception:
                raise RefreshError('mail_background_start_failed') from None
            if accepted is not True:
                raise RefreshError('mail_background_start_denied')
        instance, stable_since = initial, start if initial else None
        last_poll = start
        while True:
            now = self.clock()
            if not math.isfinite(now) or now < last_poll or now - last_poll > self.max_gap:
                raise RefreshError('mail_preflight_interrupted')
            if now - start >= self.timeout:
                raise RefreshError('mail_start_timeout')
            current = self._probe()
            if current is None and instance is not None:
                raise RefreshError('mail_stopped_resume_required')
            if current is not None:
                self._observed_running = True
                if current != instance:
                    instance, stable_since = current, now
                if stable_since is not None and now - stable_since >= self.warmup:
                    self._ready_instance, self._last_check = current, now
                    return
            last_poll = now
            self.sleep(min(self.poll, self.timeout - (now - start)))

    def require_running(self):
        """No launch/retry here. A loss of Mail pauses the shared queue, not 26 jobs."""
        if self._ready_instance is None or self._last_check is None:
            raise RefreshError('mail_preflight_required')
        now = self.clock()
        if (not math.isfinite(now) or now < self._last_check
                or now - self._last_check > self.max_gap):
            self.invalidate()
            raise RefreshError('mail_preflight_stale')
        try:
            current = self._probe()
        except RefreshError:
            self.invalidate()
            raise
        if current != self._ready_instance:
            self.invalidate()
            raise RefreshError('mail_stopped_resume_required')
        self._last_check = now
