"""Single local scheduler for the installed Account Center; no UI or extra .app.

launchd periodically wakes the bounded queue runner; the queue performs at most one
monthly cycle per account and respects persisted retries/holds. No task is installed
until explicitly enabled AFTER a real login/cloud acceptance. Missing Mail pauses.
"""
from __future__ import annotations

import os
from pathlib import Path
import plistlib
import subprocess
import sys

from login_refresh_core import RefreshError

LABEL = 'com.enoch.glados-account-center.login-refresh'


class LocalSchedule:
    def __init__(self, bundle=None, *, home=None, run=subprocess.run):
        self.home = Path(home) if home else Path.home()
        self.bundle = Path(bundle) if bundle else Path(__file__).resolve().parents[2]
        self.run = run
        self.path = self.home / 'Library/LaunchAgents' / (LABEL + '.plist')

    def spec(self):
        info_path = self.bundle / 'Info.plist'
        try:
            info = plistlib.loads(info_path.read_bytes())
        except (OSError, ValueError):
            raise RefreshError('installed_bundle_required') from None
        if info.get('CFBundleIdentifier') != 'com.enoch.glados-account-center':
            raise RefreshError('installed_bundle_required')
        python = info.get('GLaDOSRefreshPython')
        script = self.bundle / 'Resources/LoginRefresh/login_refresh_app.py'
        if python != '/opt/homebrew/bin/python3' or not script.is_file():
            raise RefreshError('installed_bundle_required')
        return {'Label':LABEL, 'ProgramArguments':[python,'-I','-B',str(script),'--tick'],
                'StartInterval':900, 'RunAtLoad':True, 'ProcessType':'Background',
                'StandardOutPath':'/dev/null', 'StandardErrorPath':'/dev/null',
                'EnvironmentVariables':{'HOME':str(self.home),'PATH':'/opt/homebrew/bin:/usr/bin:/bin',
                                        'PYTHONDONTWRITEBYTECODE':'1'}, 'ThrottleInterval':60}

    def _launchctl(self, args, allow_failure=False):
        result = self.run(['/bin/launchctl', *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=15, check=False)
        if result.returncode and not allow_failure:
            raise RefreshError('schedule_registration_failed')
        return result.returncode == 0

    def enable(self):
        spec = self.spec()
        data = plistlib.dumps(spec)
        self.path.parent.mkdir(exist_ok=True)
        if self.path.is_symlink():
            raise RefreshError('schedule_path_conflict')
        if self.path.exists():
            try:
                existing = plistlib.loads(self.path.read_bytes())
            except (ValueError, OSError):
                raise RefreshError('schedule_path_conflict') from None
            if existing != spec:
                raise RefreshError('schedule_path_conflict')
            if self._launchctl(['print',f'gui/{os.getuid()}/{LABEL}'],allow_failure=True):
                return
        else:
            fd = os.open(self.path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            with os.fdopen(fd,'wb') as f:
                f.write(data); f.flush(); os.fsync(f.fileno())
        self._launchctl(['bootstrap',f'gui/{os.getuid()}',str(self.path)])

    def disable(self):
        if self.path.is_symlink():
            raise RefreshError('schedule_path_conflict')
        if self.path.exists():
            try:
                spec = plistlib.loads(self.path.read_bytes())
                if spec.get('Label') != LABEL:
                    raise RefreshError('schedule_path_conflict')
            except (OSError,ValueError):
                raise RefreshError('schedule_path_conflict') from None
            self._launchctl(['bootout',f'gui/{os.getuid()}/{LABEL}'],allow_failure=True)
            # Only this exact owned job; don't touch any other login items.
            self.path.unlink()
