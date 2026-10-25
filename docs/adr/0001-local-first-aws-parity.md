# 0001: Build for AWS, run locally on open-source stand-ins

**Status:** Accepted

## Context
The target platform is AWS (IoT Core, Lambda, Aurora PostgreSQL). Development and demos need to cost nothing, run on one laptop (8 GB RAM, WSL2 capped at 5 GB), and be reproducible by anyone with Docker.

## Decision
- Define the production platform in **AWS CDK** (`infra/`), with tests on the synthesised template.
- Run a **local twin** with Docker Compose. Each service stands in for one AWS service:
  Mosquitto → IoT Core, SeaweedFS → S3, PostgreSQL + TimescaleDB → Aurora PostgreSQL, Grafana → dashboards.
- Keep the **ingest Lambda handler identical** in both environments. Locally, `smart_campus.bridge` subscribes to MQTT and calls `handler.handler()`, the way an IoT topic rule invokes the Lambda. Environment-specific behaviour is limited to configuration (`DB_SECRET_ARN` vs `DB_*`, `S3_ENDPOINT_URL`).
- Do **not** use LocalStack. Its free tier has narrowed, and the parts we need (IoT rules, Lambda in a VPC) are the parts it emulates least faithfully. The CDK template is what we trust for AWS behaviour.

## Consequences
- Business logic (validation, SQL, dbt) is tested locally against the real engines.
- IAM, VPC networking and the IoT rule are only verified by template assertions and `cdk synth` until a real deployment. This is an accepted gap.
- **Object store:** MinIO was the first choice, but MinIO stopped publishing public container images in 2025. SeaweedFS (Apache-2.0) provides the S3 API instead. Because the code only speaks the S3 API through boto3, swapping stores is a configuration change.
- Local MQTT allows anonymous connections on a local port. In AWS, every gateway authenticates with an X.509 certificate, and an IoT policy restricts it to publishing for its own building (`campus/<site>/<thing-name>/*`).
