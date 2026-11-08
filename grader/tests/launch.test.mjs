import test from 'node:test';
import assert from 'node:assert/strict';
import { buildRunTaskInput, launchBatch } from '../launch.mjs';

const job = { submissionId: '101', s3Key: 'submissions/101/Main.py', language: 'python',
    problemId: '2', timeLimit: 1, memoryLimit: 256, testcaseCount: 3, attemptNo: 1 };
const env = { ECS_CLUSTER: 'test', TASK_DEFINITION: 'test:1', SUBNET_ID: 'subnet-test',
    SECURITY_GROUP: 'sg-test', DB_HOST: 'localhost', DB_PASSWORD: 'fixture' };
const event = value => ({ Records: [{ messageId: 'message-1', body: JSON.stringify(value) }] });
const accepted = { tasks: [{ taskArn: 'arn:fixture' }], failures: [] };

test('redelivery shares token; next attempt uses another token', () => {
    const first = buildRunTaskInput(job, 'one', env);
    assert.equal(first.clientToken, buildRunTaskInput(job, 'two', env).clientToken);
    assert.equal(first.clientToken.length, 64);
    assert.notEqual(first.clientToken, buildRunTaskInput({ ...job, attemptNo: 2 }, 'one', env).clientToken);
});
test('contest context and attempt reach the worker', () => {
    const input = buildRunTaskInput({ ...job, contestContext: { contestId: 4, contestProblemId: 9, points: 100 } }, 'one', env);
    const vars = Object.fromEntries(input.overrides.containerOverrides[0].environment.map(x => [x.name, x.value]));
    assert.equal(vars.GRADING_ATTEMPT, '1');
    assert.equal(vars.CONTEST_ID, '4');
    assert.equal(vars.CONTEST_PROBLEM_ID, '9');
    assert.equal(vars.CONTEST_POINTS, '100');
    assert.equal(vars.MEMORY_LIMIT, '256');
    assert.equal(vars.DB_HOST, undefined);
    assert.equal(vars.DB_PASSWORD, undefined);
});

test('missing, fractional and out-of-range limits never reach ECS', async () => {
    for (const changes of [{ memoryLimit: undefined }, { memoryLimit: 0 }, { memoryLimit: 2048 },
        { timeLimit: 0.5 }, { timeLimit: 31 }, { testcaseCount: 101 }, { timeLimit: 30, testcaseCount: 100 }]) {
        let calls = 0;
        await assert.rejects(launchBatch(event({ ...job, ...changes }), async () => { calls++; return accepted; }, env));
        assert.equal(calls, 0);
    }
});
test('HTTP success with ECS failures does not acknowledge SQS', async () => {
    await assert.rejects(launchBatch(event(job), async () => ({ tasks: [], failures: [{ reason: 'capacity' }] }), env));
});
test('empty ECS response does not acknowledge SQS', async () => {
    await assert.rejects(launchBatch(event(job), async () => ({}), env));
});
test('transport exception propagates for SQS retry', async () => {
    await assert.rejects(launchBatch(event(job), async () => { throw new Error('network'); }, env));
});
test('accepted task succeeds and malformed attempt never reaches AWS', async () => {
    await launchBatch(event(job), async () => accepted, env);
    let called = false;
    await assert.rejects(launchBatch(event({ ...job, attemptNo: 4 }), async () => { called = true; return accepted; }, env));
    assert.equal(called, false);
});
test('whole batch retry preserves tokens of previously accepted records', async () => {
    const batch = { Records: [...event(job).Records, { messageId: 'two', body: JSON.stringify({ ...job, submissionId: '102' }) }] };
    const first = [];
    await assert.rejects(launchBatch(batch, async input => {
        first.push(input.clientToken);
        if (first.length === 2) throw new Error('network');
        return accepted;
    }, env));
    const second = [];
    await launchBatch(batch, async input => { second.push(input.clientToken); return accepted; }, env);
    assert.deepEqual(first, second);
});
