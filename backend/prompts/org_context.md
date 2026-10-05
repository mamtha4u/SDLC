# Organisation context (facts Echo and the crew already know; never ask the user for these)

## Platform
- Everything is built in ONE AWS sandbox account (144831534428), region **eu-west-1 (Ireland)**. The region is fixed: don't ask about it or suggest another.
- Environments: this platform builds **dev** by default unless the user says otherwise.
- Every resource the crew creates is tagged `created_by=orkestra` and `project_id=<id>`.

## How this team usually builds integrations (from their reference project MS2I-511 "Standby Aircraft")
Use these as smart defaults and suggestions ("your team usually…"), never as requirements the user didn't agree to.
- Pattern: source system → integration layer on AWS → target system. Messages are often XML; transformation happens in a **Python Lambda** that reads from an input queue and writes to an output queue.
- Queues: **SQS FIFO** when order matters, with a **dead-letter queue**. Typical settings: visibility timeout 15 min, retention 7 days, maxReceiveCount ~3 (1 initial try + 2 retries).
- Failed messages: a DLQ Lambda writes them to an S3 "error" bucket for investigation.
- Audit: input and output messages copied to an S3 audit bucket. Lifecycle: move to Glacier after ~12 days, expire after 90 days.
- Naming convention: `<type>-<platform>-<env>-euwe1-<interface>-<purpose>-01`, e.g. `lmb-mint-dev-euwe1-ac-stby-rtu-process-01` (Lambda), `sqs-mint-dev-euwe1-ac-stby-rtu-input-01` (queue), `s3b-mint-dev-euwe1-ac-stby-rtu-dlq-01` (bucket). Suggest this style, filled in with the user's interface ID, unless they have their own.
- **Sandbox prefix:** in this shared sandbox account every resource the crew builds is named `orkestra-<the team's name>` (e.g. `orkestra-ac-cd-dl-551-dev-orders-dlq.fifo`): the crew's AWS permissions only allow `orkestra-*` names, so colleagues' resources stay untouchable. Whenever you write a resource name, write it with the prefix. The user can change any name later in the project's Names tab.
- Infrastructure as code: **Terraform** (state in S3). Lambdas in Python with shared layers.
- **Services the team uses all the time** (any AWS service is allowed; these are the usual ones): SQS, SNS, S3, EventBridge (rules and Scheduler), DynamoDB, Lambda (**zip or container image**), **ECS** (Fargate), **EC2**, **ECR**, **ALB** (with ECS or Lambda targets), **Amazon MQ**, **ElastiCache**, **RDS**, and networking: **VPC, public/private subnets, NAT gateway, VPC endpoints**, security groups. Transformations run on **Lambda, ECS or EC2**; pick by the requirement (Lambda for short event-driven work under 15 min; ECS Fargate for long-running or containerised services; EC2 only when the requirement needs a server).
- **What the crew may build in this shared sandbox**: anything named `orkestra-<prefix>-…` or tagged with this project's id (the platform tags everything). The crew may **use** existing VPCs and subnets (launch into them, add its own security groups) but never change them; for NAT gateways, VPC endpoints, route tables and its own subnets it creates its **own `orkestra-*` VPC**. No account-wide settings, no default-VPC takeover, no IAM users, no global services (CloudFront, Route 53 public zones).
- **Running costs**: Lambda, SQS, SNS, S3, DynamoDB on demand and EventBridge cost almost nothing at this volume. These bill **every hour even with no traffic**, and the user must be told: EC2 (t3.micro ≈ $8/month), ECS Fargate tasks (0.25 vCPU ≈ $9/month each), ALB ≈ $16/month, NAT gateway ≈ $35/month + data, interface VPC endpoints ≈ $8/month each per AZ, Amazon MQ (mq.t3.micro ≈ $25/month), ElastiCache (cache.t4g.micro ≈ $12/month), RDS (db.t4g.micro ≈ $13/month + storage), MSK (≈ $150+/month).
- Logging: Python `logging` to CloudWatch, one line per event: `<time> <request id> <level> <class> <unique id> <interface> <message type> <error code> <description>`. Datadog is used for dashboards and alerts in production.
- Credentials (MQ, partner APIs) live in **AWS Secrets Manager**, referenced by name.
- Non-functional expectations seen before: processing under 1 s per message, zero data loss, recovery within 4 hours, no partial or corrupt messages ever sent on.
- Data mapping is documented per target element (mandatory/conditional/optional, datatype, sample value, transformation logic, source path). That is Atlas's job after Echo.
