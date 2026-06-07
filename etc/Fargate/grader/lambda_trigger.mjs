import { ECSClient, RunTaskCommand } from "@aws-sdk/client-ecs";

const ecsClient = new ECSClient({ region: "ap-northeast-2" });

// ─────────────────────────────────────────
// 환경변수 (Lambda 콘솔에서 설정)
// ─────────────────────────────────────────
const CLUSTER         = process.env.ECS_CLUSTER;      // syslab-grader-cluster
const TASK_DEFINITION = process.env.TASK_DEFINITION;  // syslab-grader-task
const SUBNET_ID       = process.env.SUBNET_ID;        // Private Subnet ID
const SECURITY_GROUP  = process.env.SECURITY_GROUP;   // SG-Fargate-Grader ID
const DB_HOST         = process.env.DB_HOST;
const DB_PASSWORD     = process.env.DB_PASSWORD;

export const handler = async (event) => {

    for (const record of event.Records) {
        let job;

        // ① SQS 메시지 파싱
        try {
            job = JSON.parse(record.body);
            console.log(`[INFO] 채점 요청 수신: submissionId=${job.submissionId}`);
        } catch (e) {
            console.error(`[ERROR] 메시지 파싱 실패: ${record.body}`);
            throw e;
        }

        // ② 필수 필드 검증
        const required = ['submissionId', 's3Key', 'language', 'problemId', 'timeLimit', 'testcaseCount'];
        for (const field of required) {
            if (job[field] === undefined || job[field] === null) {
                throw new Error(`[ERROR] 필수 필드 누락: ${field}`);
            }
        }

        // ③ 언어 검증
        const allowedLangs = ['python', 'c', 'cpp'];
        if (!allowedLangs.includes(job.language)) {
            throw new Error(`[ERROR] 지원하지 않는 언어: ${job.language}`);
        }

        // ④ Fargate RunTask 호출
        const command = new RunTaskCommand({
            cluster: CLUSTER,
            taskDefinition: TASK_DEFINITION,
            launchType: "FARGATE",
            networkConfiguration: {
                awsvpcConfiguration: {
                    subnets: [SUBNET_ID],
                    securityGroups: [SECURITY_GROUP],
                    assignPublicIp: "DISABLED",
                },
            },
            overrides: {
                containerOverrides: [
                    {
                        name: "grader",
                        environment: [
                            { name: "SUBMISSION_ID",   value: String(job.submissionId)   },
                            { name: "CODE_S3_KEY",     value: String(job.s3Key)          },
                            { name: "LANGUAGE",        value: String(job.language)       },
                            { name: "PROBLEM_ID",      value: String(job.problemId)      },
                            { name: "TIME_LIMIT",      value: String(job.timeLimit)      },
                            { name: "TESTCASE_COUNT",  value: String(job.testcaseCount)  },
                            { name: "DB_HOST",         value: DB_HOST                    },
                            { name: "DB_PASSWORD",     value: DB_PASSWORD                },
                        ],
                    },
                ],
            },
        });

        await ecsClient.send(command);
        console.log(`[INFO] Fargate 태스크 실행 완료: submissionId=${job.submissionId}`);

        // Lambda가 정상 처리 시 SQS 메시지 자동 삭제
    }
};
