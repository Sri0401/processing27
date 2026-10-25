# 0008: Deploy a live Azure environment alongside the AWS design

**Status:** Accepted

## Context
The AWS design (`infra/`, CDK) is verified by template tests and `cdk synth`, but it is not deployed: Aurora and VPC endpoints are billed hourly. A free Azure for Students subscription is available. It is shared with another project, whose **guardrail policies** apply to the whole subscription:
- Australia East only.
- An allowlist of resource types. It covers Storage, PostgreSQL Flexible Server, Key Vault, Container Apps, Log Analytics, managed identities, role assignments and budgets. It excludes **IoT Hub, Functions, Event Hubs and Container Apps Jobs**.
- Free-tier SKUs only: PostgreSQL `Standard_B1ms` ≤ 32 GB, Standard_LRS storage, and ACR only as Standard (which is paid).

## Decision
Deploy a live Azure environment (`infra-azure/main.bicep`) within those guardrails, without relaxing them:

| Concern | AWS design | Azure deployment |
|---|---|---|
| Device ingress | IoT Core (MQTT, X.509) → topic rule | Container App `ingest-api` (HTTPS + API key); gateways POST **hourly batches** |
| Ingest logic | Lambda: `handler.handler` | **The same** `handler.handler`, via `smart_campus.ingest_api` |
| Raw archive | S3 (`RawArchive`) | Blob Storage (`BlobArchive`), managed identity, shared keys disabled |
| Warehouse | Aurora PostgreSQL Serverless v2 | PostgreSQL Flexible B1ms + TimescaleDB |
| Secrets | Secrets Manager | Key Vault (RBAC) is the source of truth; values are injected into each Container App's encrypted secret store at deploy time |
| Dashboards | (QuickSight / Grafana) | Container App running Grafana with the dashboards baked into the image |
| Images | ECR | GitHub Container Registry, published by CI with `GITHUB_TOKEN` |
| Cost control | — | Scale-to-zero apps, Log Analytics daily cap (0.2 GB), budget alert |

Which raw archive is used is decided by configuration (`raw_archive_from_env`), so the handler has no cloud-specific branches.

## Consequences
- **No managed device identity on Azure:** gateways share an API key, rather than one certificate per device as on AWS. That is acceptable for a demo; in production, IoT Hub (or Event Grid MQTT) would be allowlisted and the HTTPS API retired.
- **The database is reachable from all Azure IPs** (the `AllowAzureServices` rule), because consumption Container Apps have no fixed outbound IP and VNet integration needs resource types outside the allowlist. TLS is enforced, and every role has its own generated password from Key Vault. The deployer's IP is the only other address allowed.
- **dbt is run from the developer machine or CI**, not on a schedule in Azure, because Container Apps Jobs are not allowlisted. In production this would be a scheduled job.
- The Blob lifecycle policy (tiering raw data to Cool/Archive) is a separate resource type that is not allowlisted, so it is not configured.
- **No Key Vault references in Container Apps:** Azure provisioned the environment on the newer *express* tier, which rejects `keyVaultUrl` secrets. Secrets are passed as secure deployment parameters into each app's own encrypted store instead, so rotating a secret means redeploying the apps (`deploy.sh apps`). The apps' identity therefore has no Key Vault access at all, which is least privilege.
- Teardown (`deploy.sh down`) deletes the resource group and purges the Key Vault, so nothing keeps billing.
