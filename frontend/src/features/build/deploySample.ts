import type { AccessInfo, AccessPlan, BuildState } from "../../lib/flow";

/** Sample data for the /design gallery (visual QA of the deploy and AWS access screens in every theme). */
const P = "orkestra-orders-dev";
const plan: AccessPlan = {
  status: "active", prefix: P, services: ["apigateway", "iam", "lambda", "logs", "sqs"], unsupported: [], state_bucket: "orkestra-tfstate-0123456789ab",
  region: "eu-west-1", boundary: "arn:aws:iam::144831534428:policy/orkestra-agent-boundary", platform_role: "arn:aws:iam::144831534428:role/orkestra-host-role",
  problem: null, version: "v1.2", drafted_at: "2026-10-01T16:00:00Z", granted_at: "2026-10-01T16:02:00Z",
  roles: [
    { agent: "tp", persona: "Terra", role: "orkestra-0123456789ab-terra", arn: "arn:aws:iam::144831534428:role/orkestra-0123456789ab-terra",
      purpose: "Build, change and tear down this project's AWS resources (terraform plan/apply/destroy)",
      can: [`Create, change and delete CloudWatch log groups, Lambda functions and layers, SQS queues named ${P}*`,
        "Create and change API Gateway APIs tagged project_id = prj_0123456789ab (and only those)",
        `Create IAM roles named ${P}* for the flow, always capped by the crew boundary`, "Read and write the Terraform state in s3://orkestra-tfstate-0123456789ab"],
      cannot: ["Touch anything whose name doesn't start with the project prefix", "Work outside eu-west-1", "Go beyond the crew boundary", "Change its own permissions"],
      policy: { Version: "2012-10-17", Statement: [{ Sid: "BuildSqs", Effect: "Allow", Action: ["sqs:*"], Resource: [`arn:aws:sqs:eu-west-1:144831534428:${P}*`] }] } },
    { agent: "de", persona: "Dev", role: "orkestra-0123456789ab-dev", arn: "", purpose: "Read this project's live logs and function settings, to debug bugs found in AWS",
      can: [`Read the log groups of ${P}* (live errors)`, `Read the settings of Lambda functions ${P}*`], cannot: [], policy: {} },
    { agent: "qa", persona: "Quinn", role: "orkestra-0123456789ab-quinn", arn: "", purpose: "Call this project's deployed flow and read its queues and logs",
      can: [`Invoke Lambda functions ${P}*`, `Send, read and delete test messages on SQS queues ${P}*`], cannot: [], policy: {} },
  ],
};

export const SAMPLE_BUILD: BuildState = {
  infra: { state: null, preview: null },
  code: { state: null, data: null, gates: { min_coverage_percent: 70, rules: [] } },
  qa: { state: null, data: null },
  deploy: {
    orion: null, access: plan, live: {
      summary: "All six worked examples went through the live API into the FIFO queue with the expected bodies; invalid XML is rejected with 400 and nothing is queued.",
      version: "v1.2", deployed_version: "v1.2", calls: 23, role: "orkestra-0123456789ab-quinn",
      checks: [
        { id: "LIVE-01", title: "Valid order lands in the queue as the mapped JSON", passed: true, evidence: "POST /orders → 200; queue message body matches example ex_valid_order; MessageGroupId=ORD1001" },
        { id: "LIVE-02", title: "Malformed XML is rejected, nothing queued", passed: true, evidence: "POST /orders → 400 {\"error\":\"invalid XML\"}; queue empty after 10 s" },
        { id: "LIVE-03", title: "Correlation id is logged", passed: false, evidence: "Log lines have no correlation_id field" },
      ],
      bugs: [{ id: "LIVE-001", status: "open", severity: "minor", title: "Correlation id missing from logs", test_id: "live_001", check_id: "LIVE-03",
        steps: "POST /orders with a valid order, read /aws/lambda logs", expected: "correlation_id in every line", actual: "missing", live: true }],
    },
    state: {
      status: "deployed", version: "v1.2", applied_at: "2026-10-01T16:05:00Z", error: null,
      outputs: { api_url: "https://abc123.execute-api.eu-west-1.amazonaws.com/dev/orders", queue_url: `https://sqs.eu-west-1.amazonaws.com/144831534428/${P}-orders.fifo` },
      plan: { version: "v1.2", role: "orkestra-0123456789ab-terra", planned_at: "2026-10-01T16:03:00Z", counts: { create: 13, update: 1, replace: 0, delete: 0 },
        layers: [{ layer: "lxml", zip: "build/layers/lxml.zip", kb: 5120, cached: true }],
        changes: [{ address: "aws_lambda_function.transform", type: "aws_lambda_function", name: `${P}-transform`, action: "create" },
          { address: "aws_sqs_queue.orders", type: "aws_sqs_queue", name: `${P}-orders.fifo`, action: "create" },
          { address: "aws_api_gateway_stage.dev", type: "aws_api_gateway_stage", name: "dev", action: "update" }] },
    },
  },
};

export const SAMPLE_ACCESS: AccessInfo = {
  platform: { role: "orkestra-host-role", arn: plan.platform_role, policy: { Version: "2012-10-17", Statement: [] },
    can: ["Call Claude on Amazon Bedrock", "Create, change and delete only orkestra-* resources in eu-west-1", "Create the crew's roles, always with the crew boundary"],
    cannot: ["Change anything that isn't named orkestra-*", "Create a role without the crew boundary", "Change its own permissions"] },
  boundary: { arn: plan.boundary, policy: { Version: "2012-10-17", Statement: [] } }, plan, deploy: SAMPLE_BUILD.deploy.state,
  calls: [
    { ts: "2026-10-01T16:02:01Z", agent: "tp", role: "orkestra-0123456789ab-terra", action: "sts:AssumeRole", target: "orkestra-0123456789ab-terra", ok: true, detail: "" },
    { ts: "2026-10-01T16:03:10Z", agent: "tp", role: "orkestra-0123456789ab-terra", action: "terraform plan", target: "13 to add, 1 to change, 0 to destroy", ok: true, detail: "" },
    { ts: "2026-10-01T16:05:00Z", agent: "tp", role: "orkestra-0123456789ab-terra", action: "terraform apply", target: "13 to add, 1 to change, 0 to destroy", ok: true, detail: "" },
  ],
};
