import { createHash } from "node:crypto";

// Separate from the SDK adapter so failure handling can be tested without calling AWS.
export function buildRunTaskInput(job, recordId, env) {
    for (const field of ['submissionId', 's3Key', 'language', 'problemId', 'timeLimit', 'testcaseCount']) {
        if (job[field] === undefined || job[field] === null) throw new Error(`Missing field: ${field}`);
    }
    if (!['python', 'c', 'cpp'].includes(job.language)) throw new Error('Unsupported language');
    if (job.attemptNo != null && (!Number.isInteger(job.attemptNo) || job.attemptNo < 1 || job.attemptNo > 3)) {
        throw new Error('Invalid grading attempt');
    }
    const environment = [
        ['SUBMISSION_ID', job.submissionId], ['CODE_S3_KEY', job.s3Key], ['LANGUAGE', job.language],
        ['PROBLEM_ID', job.problemId], ['TIME_LIMIT', job.timeLimit], ['TESTCASE_COUNT', job.testcaseCount],
        ['DB_HOST', env.DB_HOST], ['DB_PASSWORD', env.DB_PASSWORD],
    ].map(([name, value]) => ({ name, value: String(value) }));
    if (job.attemptNo != null) environment.push({ name: 'GRADING_ATTEMPT', value: String(job.attemptNo) });
    // Keep the contest context already present in the deployed Lambda.
    if (job.contestContext != null) {
        const { contestId, contestProblemId, points } = job.contestContext;
        if (contestId == null || contestProblemId == null || points == null) throw new Error('Invalid contest context');
        environment.push(
            { name: 'CONTEST_ID', value: String(contestId) },
            { name: 'CONTEST_PROBLEM_ID', value: String(contestProblemId) },
            { name: 'CONTEST_POINTS', value: String(points) },
        );
    }
    const attemptKey = job.attemptNo == null ? `legacy:${recordId}` : `attempt:${job.attemptNo}`;
    return {
        clientToken: createHash('sha256').update(`${job.submissionId}:${attemptKey}`).digest('hex'),
        cluster: env.ECS_CLUSTER,
        taskDefinition: env.TASK_DEFINITION, // Pin an immutable revision during retries.
        launchType: 'FARGATE',
        count: 1,
        networkConfiguration: { awsvpcConfiguration: {
            subnets: [env.SUBNET_ID], securityGroups: [env.SECURITY_GROUP], assignPublicIp: 'DISABLED',
        } },
        overrides: { containerOverrides: [{ name: 'grader', environment }] },
    };
}

export async function launchBatch(event, send, env) {
    for (const record of event.Records) {
        const job = JSON.parse(record.body);
        const result = await send(buildRunTaskInput(job, record.messageId, env));
        // ECS can respond HTTP 200 with failures instead of a running task.
        if (result.failures?.length || result.tasks?.length !== 1 || !result.tasks[0].taskArn) {
            throw new Error('ECS did not accept the grading task');
        }
        console.log(`[ACCEPTED] submissionId=${job.submissionId}, attempt=${job.attemptNo ?? 'legacy'}`);
    }
}
