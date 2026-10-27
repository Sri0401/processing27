# 0009: Deploy the AWS stack briefly, on a Free-plan account, for a live demo

**Status:** Accepted

## Context
The CDK stack (`infra/`) was verified by template tests and `cdk synth` only, but the role asks for *demonstrated* experience operating AWS data platforms. Aurora Serverless v2 and interface VPC endpoints are billed by the hour, and the project rule is **no real-money spend**. A new AWS account on the **Free plan** gets up to $200 of credits for 6 months; when they run out, the account closes rather than billing.

## Decision
Deploy the *same* stack for a day or two (`scripts/aws_demo.sh up`), prove the IoT Core → Lambda → Aurora path end to end, capture evidence, then delete everything (`scripts/aws_demo.sh down`).

Demo-only switches, all **off by default** and covered by tests:

| Context flag | Why | Production equivalent |
|---|---|---|
| `demo_db_access=<ip>/32` | Load data and run dbt from one workstation: public subnets (internet gateway, still no NAT), Aurora publicly accessible, port 5432 open to that single address. Refused for `prod` | SSM Session Manager or VPN into the private subnets |
| `lambda_reserved_concurrency=0` | New accounts often have a Lambda concurrency quota of 10, which leaves none to reserve | Reserve 20 to cap Aurora connections |
| `layer_zip=<path>` | The arm64 psycopg layer is prebuilt by `make lambda-layer`, so the CDK CLI needs no Docker | Docker bundling in CI |
| `budget_email=<addr>` | AWS Budgets alert at **$0.01 of spend excluding credits** | Team budget with forecasts |
| `warehouse=rds` | **Free-plan accounts cannot create Aurora through CloudFormation**: RDS rejects it unless *WithExpressConfiguration* is set, an option only the CLI/API offers. The demo uses RDS PostgreSQL 16.15 on `db.t4g.micro` (free tier, single AZ, encrypted) in the same private-VPC design. Refused for `prod` | Aurora PostgreSQL Serverless v2 (the default, covered by tests) |

The simulator publishes through **IoT Core over MQTT/TLS** with a real X.509 certificate for a *site gateway* Thing. The gateway's IoT policy allows publishing only to `campus/${iot:Connection.Thing.Attributes[site]}/*`, so a gateway cannot spoof another site. The certificate is created by the script, so the private key never passes through CloudFormation.

Aurora has no TimescaleDB: `db/init` creates the hypertable only when the extension exists (ADR 0007), so the same scripts run locally, on Azure and on Aurora.

## Cost (to be confirmed in Billing after the demo)
The `db.t4g.micro` instance and 20 GB of storage are free-tier usage. One interface endpoint in 2 AZs comes to about $0.50 a day, **paid from Free-plan credits**. Lambda, IoT Core, S3, Secrets Manager and CloudWatch are pennies. `down` removes the stack, the certificate, leftover log groups **and** the CDK bootstrap stack with its retained bucket and repository, so nothing is left behind.

## Consequences
- "Designed and tested" becomes "deployed and operated": a real IoT rule invoking a VPC Lambda writing to Aurora.
- The demo window must be short, and `down` must actually be run. The zero-spend budget is the safety net.
