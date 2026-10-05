"""Write the crew boundary (orkestra-agent-boundary.json next to this file) from a compact spec and report its size
(IAM counts characters without whitespace; limit 6144).

Usage: python build_boundary.py            then validate with Access Analyzer and the simulator (remote/simulate_access.py)
and apply as a new policy version (docs/03-infrastructure.md). Edit this file, never the JSON by hand.
"""
import json
import sys
from pathlib import Path

A = "144831534428"
# services whose ARNs carry the resource name: anything named orkestra-* (one fully qualified vendor per pattern; IAM refuses
# a wildcard vendor). Everything else Orkestra creates is reachable through its created_by tag.
NAMED = ["lambda", "sqs", "sns", "s3", "logs", "events", "scheduler", "states", "dynamodb", "ecs", "ecr", "elasticloadbalancing",
         "autoscaling", "elasticache", "rds", "mq", "secretsmanager", "ssm", "cloudwatch"]
NO_ID = ["iam:*", "sts:*", "organizations:*", "account:*"]
ours = {"StringEquals": {"aws:RequestTag/created_by": "orkestra"}}
ec2 = lambda t: f"arn:aws:ec2:*:*:{t}"  # noqa: E731
st = [
    {"Sid": "Read", "Effect": "Allow", "Resource": "*", "Action": [
        "lambda:List*", "sqs:ListQueues", "sns:List*", "apigateway:GET", "logs:Describe*", "cloudwatch:Describe*",
        "s3:ListAllMyBuckets", "dynamodb:List*", "events:List*", "states:List*", "tag:Get*", "ec2:Describe*", "ecs:Describe*", "ecs:List*",
        "ecr:Describe*", "ecr:GetAuthorizationToken", "elasticloadbalancing:Describe*", "autoscaling:Describe*", "application-autoscaling:Describe*",
        "elasticache:Describe*", "mq:List*", "rds:Describe*", "iam:GetRole*", "iam:ListRole*", "iam:ListAttachedRolePolicies", "iam:GetPolicy*", "iam:GetInstanceProfile",
        "sts:GetCallerIdentity", "xray:Put*", "cloudwatch:PutMetricData", "ssm:UpdateInstanceInformation", "ssmmessages:*", "ec2messages:*"]},
    {"Sid": "Named", "Effect": "Allow", "NotAction": NO_ID, "Resource": [f"arn:aws:{s}:*:*:*orkestra-*" if s != "s3" else "arn:aws:s3:::orkestra-*" for s in NAMED]},
    {"Sid": "Tagged", "Effect": "Allow", "NotAction": NO_ID, "Resource": "*",
     "Condition": {"StringEquals": {"aws:ResourceTag/created_by": "orkestra"}}},
    {"Sid": "CreateTagged", "Effect": "Allow", "Resource": "*", "Condition": ours, "Action": [
        "ec2:RunInstances", "ec2:CreateVolume", "ec2:CreateLaunchTemplate", "ec2:CreateVpc", "ec2:CreateInternetGateway", "ec2:AllocateAddress",
        "ec2:CreateKeyPair", "ec2:ImportKeyPair", "ecs:CreateCluster", "ecs:RegisterTaskDefinition",
        "ecs:CreateCapacityProvider", "mq:CreateBroker", "mq:CreateConfiguration",
        "application-autoscaling:RegisterScalableTarget"]},
    {"Sid": "CreateInVpcTagged", "Effect": "Allow", "Condition": ours, "Action": [
        "ec2:CreateSecurityGroup", "ec2:CreateNetworkInterface", "ec2:CreateNatGateway", "ec2:CreateVpcEndpoint", "ec2:CreateNetworkAcl",
        "ec2:CreateSubnet"], "Resource": [ec2(f"{t}/*") for t in ("security-group", "network-interface", "natgateway", "vpc-endpoint", "network-acl", "subnet")]},
    {"Sid": "RouteTablesTagged", "Effect": "Allow", "Action": "ec2:CreateRouteTable", "Resource": ec2("route-table/*"), "Condition": ours},
    {"Sid": "Ec2TagOnCreate", "Effect": "Allow", "Action": "ec2:CreateTags", "Resource": "*",
     "Condition": {**ours, "Null": {"ec2:CreateAction": "false"}}},
    {"Sid": "EcsTagOnCreate", "Effect": "Allow", "Action": "ecs:TagResource", "Resource": "*",
     "Condition": {**ours, "Null": {"ecs:CreateAction": "false"}}},
    {"Sid": "UseExisting", "Effect": "Allow", "Action": ["ec2:RunInstances", "ec2:CreateNetworkInterface", "ec2:CreateSecurityGroup"],
     "Resource": [ec2(f"{t}/*") for t in ("subnet", "security-group", "vpc", "network-interface", "key-pair", "launch-template")]
     + ["arn:aws:ec2:*::image/*", "arn:aws:ec2:*::snapshot/*"]},
    {"Sid": "DefaultGroups", "Effect": "Allow", "Action": ["elasticache:Create*", "rds:Create*"],
     "Resource": ["arn:aws:elasticache:*:*:*:default*", "arn:aws:rds:*:*:*:default*"]},
    {"Sid": "FlowRoleEnis", "Effect": "Allow", "Resource": "*", "Condition": {"Null": {"aws:PrincipalTag/agent": "true"}},
     "Action": ["ec2:CreateNetworkInterface", "ec2:DeleteNetworkInterface", "ec2:AssignPrivateIpAddresses", "ec2:UnassignPrivateIpAddresses"]},
    {"Sid": "LambdaTriggers", "Effect": "Allow", "Resource": "*",
     "Action": ["lambda:CreateEventSourceMapping", "lambda:UpdateEventSourceMapping", "lambda:DeleteEventSourceMapping"],
     "Condition": {"ArnLike": {"lambda:FunctionArn": "arn:aws:lambda:*:*:function:orkestra-*"}}},
    {"Sid": "LambdaTriggerTags", "Effect": "Allow", "Action": "lambda:TagResource", "Resource": "arn:aws:lambda:*:*:event-source-mapping:*", "Condition": ours},
    {"Sid": "ApiCreateTagged", "Effect": "Allow", "Action": "apigateway:POST",
     "Resource": ["arn:aws:apigateway:*::/restapis", "arn:aws:apigateway:*::/apis"], "Condition": ours},
    {"Sid": "OwnRoles", "Effect": "Allow", "Action": ["iam:*Role", "iam:*RolePolicy", "iam:*InstanceProfile"],
     "Resource": [f"arn:aws:iam::{A}:role/orkestra-*", f"arn:aws:iam::{A}:instance-profile/orkestra-*"]},
    {"Sid": "ServiceLinkedRoles", "Effect": "Allow", "Action": "iam:CreateServiceLinkedRole", "Resource": f"arn:aws:iam::{A}:role/aws-service-role/*",
     "Condition": {"StringLike": {"iam:AWSServiceName": ["ecs*", "elasticache*", "rds*", "mq*", "elasticloadbalancing*", "autoscaling*", "spot*"]}}},
    {"Sid": "RolesNeedThisBoundary", "Effect": "Deny", "Action": ["iam:CreateRole", "iam:PutRolePermissionsBoundary"], "Resource": "*",
     "Condition": {"StringNotEquals": {"iam:PermissionsBoundary": f"arn:aws:iam::{A}:policy/orkestra-agent-boundary"}}},
    {"Sid": "NoLoosening", "Effect": "Deny", "Resource": "*",
     "Action": ["iam:DeleteRolePermissionsBoundary", "iam:CreatePolicy", "iam:CreatePolicyVersion", "iam:DeletePolicy", "iam:SetDefaultPolicyVersion"]},
    {"Sid": "NotThePlatform", "Effect": "Deny", "Action": "*",
     "Resource": [f"arn:aws:iam::{A}:role/orkestra-host-role", f"arn:aws:iam::{A}:instance-profile/orkestra-host*", "arn:aws:s3:::orkestra-deploy-*"]},
    # objects only in orkestra-* buckets; without "/*" so bucket settings named *Object* (object lock, which Terraform
    # reads on every refresh) stay allowed on our own buckets
    {"Sid": "S3ObjectsOnlyOurs", "Effect": "Deny", "Action": "s3:*Object*", "NotResource": "arn:aws:s3:::orkestra-*"},
    {"Sid": "IrelandOnly", "Effect": "Deny", "Resource": "*", "Condition": {"StringNotEquals": {"aws:RequestedRegion": "eu-west-1"}},
     "NotAction": ["iam:*", "sts:*", "tag:*", "s3:ListAllMyBuckets", "xray:*", "cloudwatch:PutMetricData"]},
]
doc = {"Version": "2012-10-17", "Statement": st}
size = len(json.dumps(doc, separators=(",", ":")))
print("chars without whitespace:", size, "(limit 6144)")
lines = ['{', '  "Version": "2012-10-17",', '  "Statement": [']
lines += [",\n".join("    " + json.dumps(s, ensure_ascii=False) for s in st)]
lines += ['  ]', '}', '']
out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("orkestra-agent-boundary.json")
out.write_text("\n".join(lines), encoding="utf-8", newline="\n")
if size > 6144:
    sys.exit("too big for IAM: shorten the spec")
