"""Linux-only subprocess sandbox. No environment-only or host-exec fallback."""
import os
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path
from execution_protocol import ExecutionFailure, MAX_OUTPUT


class Sandbox:
    def __init__(self, root='/work/jail', base='/jail', launcher='/opt/runner/launcher'):
        if sys.platform != 'linux' or os.geteuid() != 0:
            raise ExecutionFailure('Linux sandbox privileges unavailable')
        self.root, self.launcher = Path(root), launcher
        if not Path(launcher).is_file() or not Path(base).is_dir() or self.root.exists():
            raise ExecutionFailure('Invalid sandbox installation')
        # Immutable toolchain only; no /proc, secrets, host devices or shared mounts.
        shutil.copytree(base, self.root, symlinks=True)
        for forbidden in ('proc', 'sys', 'dev'):
            if (self.root / forbidden).exists():
                raise ExecutionFailure('Unexpected sandbox mount point')

    def reset(self, name, contents, executable=False):
        for name_dir in ('job', 'tmp'):
            path = self.root / name_dir
            if path.exists():
                shutil.rmtree(path)
            path.mkdir(mode=0o700)
            os.chown(path, 10001, 10001)
        path = self.root / 'job' / name
        path.write_bytes(contents)
        path.chmod(0o555 if executable else 0o444)

    def binary(self):
        path = self.root / 'job' / 'solution'
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 16 * 1024 * 1024:
                raise ExecutionFailure('Invalid compiler artifact')
            return stream.read(16 * 1024 * 1024 + 1)

    def execute(self, command, stdin, seconds, memory_mb, compiler=False):
        # Separate setup-error pipe distinguishes a broken jail from user exit(125).
        read_error, write_error = os.pipe()
        proc = None
        selector = selectors.DefaultSelector()
        start = time.monotonic()
        output, setup_error = bytearray(), bytearray()
        total = 0
        verdict = 'OK'
        usage = None
        try:
            proc = subprocess.Popen([self.launcher, str(self.root), str(seconds), str(memory_mb),
                'compile' if compiler else 'run', str(write_error), *command],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env={}, close_fds=True, pass_fds=(write_error,), start_new_session=True)
            os.close(write_error); write_error = -1
            for stream, kind in ((proc.stdout, 'stdout'), (proc.stderr, 'stderr')):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, kind)
            os.set_blocking(read_error, False)
            selector.register(read_error, selectors.EVENT_READ, 'setup')
            pending = memoryview(stdin)
            if pending:
                os.set_blocking(proc.stdin.fileno(), False)
                selector.register(proc.stdin, selectors.EVENT_WRITE, 'stdin')
            else:
                proc.stdin.close()
            while True:
                elapsed = time.monotonic() - start
                if elapsed >= seconds:
                    verdict = 'TIME_LIMIT_EXCEEDED'
                    break
                pid, status, current_usage = os.wait4(proc.pid, os.WNOHANG)
                if pid:
                    proc.returncode = os.waitstatus_to_exitcode(status)
                    usage = current_usage
                    # Compiler descendants cannot escape this process group.
                    self._kill_group(proc.pid)
                for key, _ in selector.select(min(0.02, max(0, seconds - elapsed))):
                    if key.data == 'stdin':
                        try:
                            count = os.write(key.fd, pending[:65536]); pending = pending[count:]
                        except BrokenPipeError:
                            pending = memoryview(b'')
                        if not pending:
                            selector.unregister(key.fileobj); proc.stdin.close()
                        continue
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    if key.data == 'setup':
                        setup_error.extend(chunk[:1])
                    else:
                        total += len(chunk)
                        if total > MAX_OUTPUT:
                            verdict = 'OUTPUT_LIMIT_EXCEEDED'; break
                        if key.data == 'stdout': output.extend(chunk)
                if verdict != 'OK' or (usage is not None and not selector.get_map()):
                    break
                if usage is not None:
                    # wait4 must only reap the leader once; drain pipes after death.
                    while selector.get_map():
                        for key, _ in selector.select(0.02):
                            if key.data == 'stdin':
                                selector.unregister(key.fileobj); proc.stdin.close(); continue
                            chunk = os.read(key.fd, 65536)
                            if not chunk: selector.unregister(key.fileobj); continue
                            if key.data == 'setup': setup_error.extend(chunk[:1])
                            else:
                                total += len(chunk)
                                if total > MAX_OUTPUT: verdict = 'OUTPUT_LIMIT_EXCEEDED'; break
                                if key.data == 'stdout': output.extend(chunk)
                        if verdict != 'OK' or time.monotonic() - start >= seconds: break
                    break
            if setup_error:
                raise ExecutionFailure('Sandbox setup failed')
            if verdict == 'OK' and proc.returncode != 0:
                verdict = 'RUNTIME_ERROR'
            return dict(verdict=verdict, runtimeMs=min(int((time.monotonic() - start) * 1000), (seconds + 2) * 1000),
                        memoryKb=int(usage.ru_maxrss) if usage else 0,
                        output=bytes(output) if verdict == 'OK' else b'')
        finally:
            selector.close()
            if write_error >= 0: os.close(write_error)
            os.close(read_error)
            if proc:
                self._kill_group(proc.pid)
                if proc.returncode is None: proc.wait(timeout=3)
                for stream in (proc.stdin, proc.stdout, proc.stderr):
                    if stream and not stream.closed: stream.close()

    @staticmethod
    def _kill_group(pid):
        try: os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError: pass
