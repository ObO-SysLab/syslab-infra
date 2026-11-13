"""Installed on the VM host only after approval. No shell interpolation or broad deletion."""
import contextlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path('/var/lib/diveon-vm')
HELPER_LABEL = 'diveon.vm.manager'
IDENTITY = {'sessionId', 'userId', 'problemId', 'expiresAtEpochMs'}
# Set by the practice build (vm/codebuild) on images restricted to allowed commands. Their lab account
# may write only to these mounts, all noexec, so it cannot run programs it writes itself.
RESTRICTION_LABEL = 'net.diveon.restriction'
RESTRICTED_OPTIONS = [
    '--tmpfs', '/tmp:rw,noexec,nosuid,nodev,size=64m',
    '--tmpfs', '/var/tmp:rw,noexec,nosuid,nodev,size=16m',
    '--tmpfs', '/home/lab/work:rw,noexec,nosuid,nodev,size=64m,uid=10001,gid=10001,mode=0700',
    '--security-opt', 'no-new-privileges', '--pids-limit', '256',
]


class Failure(RuntimeError):
    pass


@contextlib.contextmanager
def file_lock(path):
    import fcntl
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def atomic_write(path, value):
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as stream:
            json.dump(value, stream, separators=(',', ':'))
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
        if hasattr(os, 'O_DIRECTORY'):
            fd = os.open(path.parent, os.O_DIRECTORY)
            try: os.fsync(fd)
            finally: os.close(fd)
    finally:
        if temporary.exists(): temporary.unlink()


class Docker:
    def __call__(self, args):
        # File capture avoids allocating an unbounded stdout/stderr buffer in the helper.
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as error:
            process = subprocess.Popen(['docker', *args], stdin=subprocess.DEVNULL, stdout=output, stderr=error)
            try: code = process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=3)
                raise Failure('Docker outcome unknown') from None
            output.seek(0); value = output.read(65537)
            if code != 0 or len(value) > 65536: raise Failure('Docker operation unavailable')
            return value.decode('utf-8').strip()


class Manager:
    def __init__(self, root=ROOT, docker=None, clock=time.time, locker=file_lock):
        self.root, self.docker, self.clock, self.locker = Path(root), docker or Docker(), clock, locker

    def validate(self, request):
        if not isinstance(request, dict) or type(request.get('version')) is not int or request['version'] != 1:
            raise Failure('Invalid protocol')
        operation = request.get('operation')
        fields = {'version', 'operation'} if operation == 'reconcile' else {'version', 'operation'} | IDENTITY
        if operation == 'create': fields |= {'image'}
        if 'legacyContainerId' in request: fields |= {'legacyContainerId'}
        if set(request) != fields or operation not in ('create', 'inspect', 'remove', 'reconcile'):
            raise Failure('Invalid operation')
        if operation == 'reconcile':
            if 'legacyContainerId' in request: raise Failure('Invalid reconciliation')
            return
        for key in IDENTITY:
            if type(request[key]) is not int or not (0 if key == 'expiresAtEpochMs' else 1) <= request[key] <= 2**63 - 1:
                raise Failure('Invalid identity')
        if 'legacyContainerId' in request and (operation == 'create' or not isinstance(request['legacyContainerId'], str)
                or not re.fullmatch('[a-f0-9]{64}', request['legacyContainerId'])):
            raise Failure('Invalid legacy identity')
        if operation == 'create' and (not isinstance(request['image'], str)
                or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9._:/@-]{0,499}', request['image'])):
            raise Failure('Invalid image')

    def identity(self, request):
        return {k: request[k] for k in IDENTITY | {'legacyContainerId'} if k in request}

    def name(self, request):
        return f"diveon-vm-s{request['sessionId']}"

    def labels(self, request):
        return {HELPER_LABEL: 'v1', **{f'diveon.vm.{k}': str(request[k]) for k in IDENTITY}}

    def hardening(self, image):
        """Extra options for images the practice build restricted to an allowed-command list."""
        template = '{{json .Config.Labels}}'
        try:
            raw = self.docker(['image', 'inspect', '--format', template, image])
        except Failure:
            # Not pulled yet: container create would pull it anyway; pull first to read its labels.
            self.docker(['image', 'pull', '--quiet', image])
            raw = self.docker(['image', 'inspect', '--format', template, image])
        labels = json.loads(raw)
        value = labels.get(RESTRICTION_LABEL) if isinstance(labels, dict) else None
        if value is None: return []
        if value != 'v1': raise Failure('Unknown image restriction')
        return list(RESTRICTED_OPTIONS)

    def lookup(self, request):
        legacy = request.get('legacyContainerId')
        target = legacy or self.name(request)
        selector = 'id=' + legacy if legacy else 'name=^/' + target + '$'
        found = self.docker(['container', 'ls', '-aq', '--no-trunc', '--filter', selector])
        if not found: return None
        if not re.fullmatch('[a-f0-9]{64}', found): raise Failure('Ambiguous container')
        template = '{"id":{{json .Id}},"name":{{json .Name}},"status":{{json .State.Status}},"labels":{{json .Config.Labels}}}'
        value = json.loads(self.docker(['container', 'inspect', '--format', template, found]))
        expected_name = f"/syslab-vm-{request['userId']}-{request['problemId']}" if legacy else '/' + target
        if value.get('id') != found or value.get('name') != expected_name: raise Failure('Container identity mismatch')
        if legacy:
            if found != legacy: raise Failure('Legacy identity mismatch')
        elif any((value.get('labels') or {}).get(k) != v for k, v in self.labels(request).items()):
            raise Failure('Container ownership mismatch')
        return value

    def handle(self, request):
        self.validate(request)
        if request['operation'] == 'reconcile': return self.reconcile()
        sid = request['sessionId']
        with self.locker(self.root / f'{sid}.lock'):
            manifest = self.root / f'{sid}.json'
            closed = self.root / f'{sid}.closed'
            identity = self.identity(request)
            if manifest.exists():
                if json.loads(manifest.read_text(encoding='utf-8')) != identity: raise Failure('Manifest mismatch')
            elif request['operation'] in ('create', 'remove'):
                atomic_write(manifest, identity) # Durable ownership record before Docker.
            if request['operation'] == 'create':
                remaining = request['expiresAtEpochMs'] - int(self.clock() * 1000)
                if closed.exists() or remaining <= 0 or remaining > 7_500_000: raise Failure('Session is closed or expired')
                current = self.lookup(request)
                if current is None:
                    args = ['container', 'create', '--name', self.name(request), '--memory', '256m',
                            '--cpus', '0.3', '--network', 'none', '--init', *self.hardening(request['image'])]
                    for key, value in self.labels(request).items(): args += ['--label', key + '=' + value]
                    args += [request['image'], 'sleep', 'infinity']
                    self.docker(args)
                    current = self.lookup(request)
                    if current is None: raise Failure('Create result missing')
                if current['status'] == 'created':
                    self.docker(['container', 'start', current['id']])
                current = self.lookup(request)
                if current is None or current['status'] != 'running': raise Failure('Container not running')
                return self.result(request, current)
            if request['operation'] == 'remove':
                # Keep the tombstone even when deletion fails or the container is currently missing.
                atomic_write(closed, {'closed': True})
                current = self.lookup(request)
                if current is not None:
                    self.docker(['container', 'rm', '--force', current['id']])
                if self.lookup(request) is not None: raise Failure('Removal unconfirmed')
                return self.result(request, None)
            return self.result(request, self.lookup(request))

    def result(self, request, current):
        return dict(version=1, sessionId=request['sessionId'],
            status='MISSING' if current is None else ('RUNNING' if current['status'] == 'running' else 'STOPPED'),
            containerId=None if current is None else current['id'])

    def reconcile(self):
        # Rotation also revisits STOPPED DB sessions whose timed-out Docker create completed late.
        with self.locker(self.root / 'recovery.lock'):
            cursor_file = self.root / 'recovery.cursor'
            cursor = int(cursor_file.read_text()) if cursor_file.exists() else 0
            paths = sorted((p for p in self.root.glob('*.json') if p.stem.isdigit()), key=lambda p: int(p.stem))
            selected = [p for p in paths if int(p.stem) > cursor][:50]
            failed = False
            for path in selected:
                # Persist progress before work: SSH timeout/process death must not starve later IDs.
                atomic_write(cursor_file, int(path.stem))
                try:
                    identity = json.loads(path.read_text(encoding='utf-8'))
                    if self.root.joinpath(path.stem + '.closed').exists() or identity['expiresAtEpochMs'] <= int(self.clock() * 1000):
                        self.handle(dict(version=1, operation='remove', **identity))
                except Exception: failed = True
                cursor = int(path.stem)
            atomic_write(cursor_file, cursor if selected else 0)
            if failed: raise Failure('Some sessions still need cleanup')
            return dict(version=1, status='OK')


def main():
    try:
        info = ROOT.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise Failure('Unsafe state directory')
        raw = sys.stdin.buffer.read(8193)
        if len(raw) > 8192: raise Failure('Request too large')
        response = Manager().handle(json.loads(raw))
        print(json.dumps(response, separators=(',', ':')))
        return 0
    except Exception:
        print('VM operation could not be verified', file=sys.stderr)
        return 1


if __name__ == '__main__': sys.exit(main())
