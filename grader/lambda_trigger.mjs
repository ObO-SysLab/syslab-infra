import { ECSClient, RunTaskCommand } from "@aws-sdk/client-ecs";
import { launchBatch } from "./launch.mjs";

const ecsClient = new ECSClient({ region: "ap-northeast-2" });

// Package BOTH this file (as index.mjs) and launch.mjs in the deployment zip.
export const handler = (event) => launchBatch(
    event, input => ecsClient.send(new RunTaskCommand(input)), process.env,
);
