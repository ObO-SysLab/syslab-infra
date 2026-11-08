# Existing NAT EC2 network

Selected by the user on 2026-11-04 to avoid hourly interface endpoint costs.
This replaces the earlier endpoint-only network proposal; it does not change grading,
execution_protocol, sandbox limits or problem disclosure contracts.

## Resources

Region/account: ap-northeast-2 / 290373175858. Reuse VPC vpc-059c175e86a99bd3b,
NAT i-08761f745077023e3 (eni-0795de4b87a66e505, 10.0.1.107), public IGW route,
and S3 Gateway vpce-00e4d5879e9739088. Never move the API Elastic IP to the NAT.

Created network identifiers are in network.nat.json. They are a deployment input,
not proof that the coordinator/runner image, IAM, bucket and task definitions exist.
Set RUNNER_SUBNET_ID and RUNNER_SECURITY_GROUP from this file only after live review.
The coordinator must use its own network/role, not the runner's restricted subnet.

## Traffic contract

| Component | Allowed path | Denied / limitation |
| --- | --- | --- |
| NAT EC2 | Forward existing private workloads' HTTPS; VM HTTP package downloads | No public application ingress; temporary admin SSH /32 removed after inspection |
| ECS image and log transport | Public HTTPS via NAT, S3 layers via Gateway | Runner public IP DISABLED; no paid endpoints |
| Trusted worker | Exact-object regional S3 signed GET/POST via Gateway | No redirects/proxies; no task role, DB secrets or shared storage |
| Compiler and submitted program | Existing local stdin/stdout/files contract | seccomp denies socket/connect; chroot, UID10001, clean env and resource limits retained |
| Runner subnet | Outbound TCP443, inbound return ports1024–65535 | Dedicated NACL first denies 10/8, 172.16/12, 192.168/16 in both directions; other traffic including IPv6 defaults deny |

Security groups have allow rules only: HTTPS to 0.0.0.0/0 alone would also permit
private HTTPS. The dedicated subnet ACL supplies that private-address denial without
altering the existing VM/RDS ACL. AWS-provided DNS and platform metadata have special
filtering behavior: do not claim the ACL blocks them. Prove submitted-code socket denial
and absence of task credentials separately.

The trusted runner process can reach arbitrary public HTTPS destinations. If that process
or its sandbox is compromised, NAT is not a destination allowlist. This is the explicit
difference from the endpoint-only proposal. Endpoint policies also would not replace the
existing sandbox. Do not relax seccomp or fall back to the unrestricted grader on failure.

S3 exchange bucket policy must retain TLS/private access controls and allow the exact
Gateway endpoint used by the worker. Account for coordinator cleanup/monitor access before
adding a blanket source-endpoint deny. Do not change the shared Gateway policy in a way
that breaks existing code, image-layer or practice buckets.

## Operations

- NAT source/destination checking must remain disabled; Linux IP forwarding and MASQUERADE
  must survive boot. Existing iptables-restore.service loads /etc/iptables/rules.v4.
- NAT shares an AZ with the new runner subnet (ap-northeast-2a). Existing VM is in another
  AZ; its cross-AZ transfer is not eliminated by this change.
- NAT is a single point of failure and t3.nano has limited capacity. Monitor CPU credits,
  connectivity and concurrency; do not assume endpoint-level availability or throughput.
- NAT stop/start may change its public IPv4. Route to the stable ENI, never the public IP.
  Re-query the address before administration and verify the pinned SSH host key.
- No automatic 72-hour shutdown is installed by this network change. Start the three-day
  window only when full validation is ready; record an end time and responsible cleanup.
- A stopped NAT intentionally leaves default routes unusable. Do not mistake this for a
  reason to open the runner to an unrestricted fallback path.

## Validation still needed for production

Network bootstrap (private Fargate ECR pull and CloudWatch log output) is separate from
actual runner compatibility. Signed S3 GET/POST, runtime secret supply, Python/C/C++
seccomp tests, internal-address denial against known live controls, orphan cleanup and
end-to-end grading must be tested with the final runner/coordinator revisions.

Evidence: ../../../obo-harness/evidence/2026-11-04-nat-network/ (workspace sibling;
not part of this repository's deployable image).

References: [NAT instance](https://docs.aws.amazon.com/vpc/latest/userguide/VPC_NAT_Instance.html),
[SG limitations](https://docs.aws.amazon.com/vpc/latest/userguide/security-group-rules.html).
