"""Pure unit tests: no SSH, Docker daemon, AWS or database is started."""
import contextlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from diveon_vm_helper import Manager, Failure, Docker, atomic_write


class FakeDocker:
    def __init__(self):
        self.containers = {}
        self.calls = []
        self.fail = None

    def __call__(self, args):
        self.calls.append(args)
        operation = args[1]
        if operation == self.fail: raise Failure('daemon unavailable')
        if operation == 'ls':
            selector = args[-1]
            values = [c['id'] for c in self.containers.values() if
                      selector == 'id=' + c['id'] or selector == 'name=^' + c['name'] + '$']
            return '\n'.join(values)
        if operation == 'inspect': return json.dumps(self.containers[args[-1]])
        if operation == 'create':
            name = '/' + args[args.index('--name') + 1]
            if any(c['name'] == name for c in self.containers.values()): raise Failure('name exists')
            cid = f'{len(self.calls):064x}'
            labels = dict(args[i + 1].split('=', 1) for i, a in enumerate(args) if a == '--label')
            self.containers[cid] = dict(id=cid, name=name, status='created', labels=labels)
            return cid
        if operation == 'start': self.containers[args[-1]]['status'] = 'running'; return args[-1]
        if operation == 'rm': del self.containers[args[-1]]; return args[-1]
        raise AssertionError(args)


class LifecycleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.docker = FakeDocker()
        self.now = 1000
        self.manager = Manager(self.root, self.docker, lambda: self.now, lambda _: contextlib.nullcontext())
        self.base = dict(version=1, sessionId=10, userId=7, problemId=9, expiresAtEpochMs=8_200_000)

    def request(self, operation, **kw):
        request = dict(self.base, operation=operation)
        if operation == 'create': request['image'] = 'registry/practice:latest'
        request.update(kw)
        return self.manager.handle(request)

    def create(self): return self.request('create')['containerId']

    def test_create_is_idempotent_and_uses_resource_limits(self):
        cid = self.create()
        self.assertEqual(cid, self.create())
        creates = [c for c in self.docker.calls if c[1] == 'create']
        self.assertEqual(1, len(creates))
        for option, value in [('--network', 'none'), ('--memory', '256m'), ('--cpus', '0.3')]:
            self.assertEqual(value, creates[0][creates[0].index(option) + 1])
        self.assertTrue(self.root.joinpath('10.json').exists())

    def test_remove_checks_exact_identity_and_is_idempotent(self):
        self.create()
        self.assertEqual('MISSING', self.request('remove')['status'])
        self.assertEqual('MISSING', self.request('remove')['status'])
        self.assertTrue(self.root.joinpath('10.closed').exists())

    def test_inspect_never_creates_or_removes(self):
        self.assertEqual('MISSING', self.request('inspect')['status'])
        self.assertEqual(['ls'], [c[1] for c in self.docker.calls])

    def test_failed_delete_keeps_tombstone_and_retries(self):
        self.create(); self.docker.fail = 'rm'
        with self.assertRaises(Failure): self.request('remove')
        self.assertTrue(self.root.joinpath('10.closed').exists())
        self.docker.fail = None
        self.assertEqual('MISSING', self.request('remove')['status'])

    def test_delayed_create_after_remove_is_denied(self):
        self.request('remove')
        with self.assertRaises(Failure): self.create()
        self.assertFalse(self.docker.containers)

    def test_late_daemon_result_is_removed_by_manifest_recovery(self):
        cid = self.create(); late = self.docker.containers[cid].copy()
        self.request('remove')
        self.docker.containers[cid] = late
        self.manager.handle(dict(version=1, operation='reconcile'))
        self.assertFalse(self.docker.containers)

    def test_expired_session_cannot_create_and_is_reconciled(self):
        self.create(); self.now = 8200
        with self.assertRaises(Failure): self.create()
        self.manager.handle(dict(version=1, operation='reconcile'))
        self.assertFalse(self.docker.containers)

    def test_other_owner_cannot_inspect_or_remove_manifest(self):
        self.create()
        for operation in ['inspect', 'remove', 'create']:
            with self.subTest(operation=operation), self.assertRaises(Failure):
                self.request(operation, userId=8)
        self.assertTrue(self.docker.containers)

    def test_mismatched_labels_never_delete_container(self):
        cid = self.create()
        self.docker.containers[cid]['labels']['diveon.vm.userId'] = '8'
        with self.assertRaises(Failure): self.request('remove')
        self.assertNotIn('rm', [c[1] for c in self.docker.calls])

    def test_unknown_daemon_state_is_not_missing(self):
        self.docker.fail = 'ls'
        for operation in ['inspect', 'remove', 'create']:
            with self.subTest(operation=operation), self.assertRaises(Failure): self.request(operation)

    def test_legacy_requires_full_id_and_exact_old_name(self):
        cid = 'a' * 64
        self.docker.containers[cid] = dict(id=cid, name='/syslab-vm-7-9', status='running', labels={})
        self.assertEqual('RUNNING', self.request('inspect', legacyContainerId=cid)['status'])
        self.docker.containers[cid]['name'] = '/someone-else'
        with self.assertRaises(Failure): self.request('remove', legacyContainerId=cid)
        self.assertTrue(self.docker.containers)

    def test_invalid_inputs_never_reach_docker(self):
        for kw in [dict(sessionId=True), dict(sessionId=-1), dict(userId='7; touch x'),
                   dict(image='ubuntu;id'), dict(image='--privileged'), dict(image='$(id)'),
                   dict(legacyContainerId='a' * 12), dict(unexpected='value')]:
            with self.subTest(kw=kw), self.assertRaises(Failure): self.request('create', **kw)
        self.assertFalse(self.docker.calls)

    def test_exited_container_is_not_restarted(self):
        cid = self.create(); self.docker.containers[cid]['status'] = 'exited'
        with self.assertRaises(Failure): self.create()
        self.assertEqual(1, len([c for c in self.docker.calls if c[1] == 'start']))

    def test_manifest_written_before_uncertain_create(self):
        self.docker.fail = 'create'
        with self.assertRaises(Failure): self.create()
        self.assertTrue(self.root.joinpath('10.json').exists())

    def test_failed_recovery_advances_cursor_and_retries_after_wrap(self):
        self.create(); self.now = 8200; self.docker.fail = 'rm'
        with self.assertRaises(Failure): self.manager.reconcile()
        self.assertEqual(10, json.loads(self.root.joinpath('recovery.cursor').read_text()))
        self.manager.reconcile()  # empty tail resets rotation
        self.docker.fail = None; self.manager.reconcile()
        self.assertFalse(self.docker.containers)

    def test_new_session_is_untouched_by_old_session_stop(self):
        old = self.create()
        new = self.request('create', sessionId=11)['containerId']
        self.request('remove')
        self.assertNotIn(old, self.docker.containers)
        self.assertIn(new, self.docker.containers)

    def test_reconcile_does_not_adopt_or_delete_unrecorded_containers(self):
        self.docker.containers['b' * 64] = dict(id='b' * 64, name='/unrelated', status='running', labels={})
        self.manager.reconcile()
        self.assertTrue(self.docker.containers)
        self.assertFalse(self.docker.calls)

    def test_process_death_still_advances_recovery_cursor(self):
        atomic_write(self.root / '10.json', self.manager.identity(self.base))
        self.now = 8200
        with patch.object(self.manager, 'handle', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt): self.manager.reconcile()
        self.assertEqual(10, json.loads((self.root / 'recovery.cursor').read_text()))


if __name__ == '__main__': unittest.main()
