"""AWS deployment of the platform - the production twin of docker-compose.yml.

    Building devices --MQTT/X.509--> IoT Core --topic rule--> Ingest Lambda --> Aurora PostgreSQL
                                        |  (error action)          |
                                        v                          v
                                  S3 raw bucket  <------------  raw archive

The ingest Lambda is the exact code in lambdas/ingest that runs locally behind the MQTT bridge.
"""

from __future__ import annotations

from pathlib import Path

import aws_cdk as cdk
from aws_cdk import aws_budgets as budgets
from aws_cdk import aws_cloudwatch as cloudwatch
from aws_cdk import aws_cloudwatch_actions as cw_actions
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_iot as iot
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_rds as rds
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_lambda_destinations as destinations
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sqs as sqs
from aws_cdk import aws_sns_subscriptions as subs
from constructs import Construct

REPO_ROOT = Path(__file__).resolve().parents[2]
LAMBDA_SRC = REPO_ROOT / "lambdas" / "ingest"
TELEMETRY_TOPIC = "campus/+/+/+/telemetry"
AURORA_VERSION = "16.15"


class SmartCampusPlatformStack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, *, env_name: str, site_id: str,
                 alarm_email: str | None = None, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        is_prod = env_name == "prod"
        removal = cdk.RemovalPolicy.RETAIN if is_prod else cdk.RemovalPolicy.DESTROY
        # Demo-only: "<ip>/32" lets one workstation reach Aurora directly to bootstrap, load and
        # run dbt. Never set for prod - there, use SSM Session Manager or a VPN into the VPC.
        demo_db_access = self.node.try_get_context("demo_db_access")
        if demo_db_access and is_prod:
            raise ValueError("demo_db_access must not be used for prod")

        # ------------------------------------------------------------------ network
        # Private subnets only. No NAT gateway: the Lambda reaches S3 and Secrets Manager
        # through VPC endpoints, which is cheaper and keeps traffic off the internet.
        subnets = [ec2.SubnetConfiguration(name="data", subnet_type=ec2.SubnetType.PRIVATE_ISOLATED, cidr_mask=24)]
        if demo_db_access:  # public subnets (internet gateway only, still no NAT) to host a reachable DB
            subnets.append(ec2.SubnetConfiguration(name="demo", subnet_type=ec2.SubnetType.PUBLIC, cidr_mask=24))
        vpc = ec2.Vpc(self, "Vpc", max_azs=2, nat_gateways=0, subnet_configuration=subnets)
        vpc.add_gateway_endpoint("S3Endpoint", service=ec2.GatewayVpcEndpointAwsService.S3)
        vpc.add_interface_endpoint("SecretsEndpoint", service=ec2.InterfaceVpcEndpointAwsService.SECRETS_MANAGER)

        # ------------------------------------------------------------------ raw landing zone
        raw_bucket = s3.Bucket(
            self, "RawBucket",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            versioned=True,
            removal_policy=removal,
            auto_delete_objects=not is_prod,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="tier-raw-telemetry",
                    prefix="telemetry/",
                    transitions=[
                        s3.Transition(storage_class=s3.StorageClass.INFREQUENT_ACCESS,
                                      transition_after=cdk.Duration.days(30)),
                        s3.Transition(storage_class=s3.StorageClass.GLACIER_INSTANT_RETRIEVAL,
                                      transition_after=cdk.Duration.days(180)),
                    ],
                ),
                s3.LifecycleRule(id="expire-old-versions",
                                 noncurrent_version_expiration=cdk.Duration.days(30)),
            ],
        )

        # ------------------------------------------------------------------ warehouse (PostgreSQL)
        # Default: Aurora PostgreSQL Serverless v2. `-c warehouse=rds` uses a single-AZ RDS PostgreSQL
        # db.t4g.micro instead: AWS Free-plan accounts cannot create Aurora through CloudFormation
        # (it requires "express configuration", a CLI-only option). Same engine major, SQL and dbt.
        warehouse = self.node.try_get_context("warehouse") or "aurora"
        if warehouse not in ("aurora", "rds"):
            raise ValueError(f"unknown warehouse {warehouse!r}: use 'aurora' or 'rds'")
        db_sg = ec2.SecurityGroup(self, "DbSg", vpc=vpc, description="Warehouse - ingress from platform compute only",
                                  allow_all_outbound=False)
        db_subnets = ec2.SubnetSelection(
            subnet_type=ec2.SubnetType.PUBLIC if demo_db_access else ec2.SubnetType.PRIVATE_ISOLATED)
        credentials = rds.Credentials.from_generated_secret("platform_admin")

        if warehouse == "aurora":
            cluster = rds.DatabaseCluster(
                self, "Warehouse",
                # Pinned explicitly: AWS retires minor versions (16.4 is gone), newer than this CDK's constants.
                engine=rds.DatabaseClusterEngine.aurora_postgres(
                    version=rds.AuroraPostgresEngineVersion.of(AURORA_VERSION, "16")),
                default_database_name="smart_campus",
                credentials=credentials,
                writer=rds.ClusterInstance.serverless_v2("writer", publicly_accessible=bool(demo_db_access)),
                serverless_v2_min_capacity=1 if is_prod else 0.5,
                serverless_v2_max_capacity=4 if is_prod else 2,
                vpc=vpc,
                vpc_subnets=db_subnets,
                security_groups=[db_sg],
                storage_encrypted=True,
                iam_authentication=True,
                backup=rds.BackupProps(retention=cdk.Duration.days(14 if is_prod else 1)),
                deletion_protection=is_prod,
                removal_policy=cdk.RemovalPolicy.SNAPSHOT if is_prod else cdk.RemovalPolicy.DESTROY,
                cloudwatch_logs_exports=["postgresql"],
                cloudwatch_logs_retention=logs.RetentionDays.ONE_MONTH,
            )
            if not is_prod:
                # Escape hatch: 0 ACU = auto-pause after 5 idle minutes (Aurora PostgreSQL 16.3+), so a
                # dev/demo cluster costs no compute between uses. This CDK version still validates >= 0.5.
                cfn_cluster = cluster.node.default_child
                cfn_cluster.add_property_override("ServerlessV2ScalingConfiguration.MinCapacity", 0)
                cfn_cluster.add_property_override("ServerlessV2ScalingConfiguration.SecondsUntilAutoPause", 300)
            db_secret, db_host, db_target = cluster.secret, cluster.cluster_endpoint.hostname, cluster
        else:
            if is_prod:
                raise ValueError("warehouse=rds is for Free-plan demos; prod uses Aurora")
            instance = rds.DatabaseInstance(
                self, "WarehouseInstance",
                engine=rds.DatabaseInstanceEngine.postgres(
                    version=rds.PostgresEngineVersion.of(AURORA_VERSION, "16")),
                instance_type=ec2.InstanceType.of(ec2.InstanceClass.T4G, ec2.InstanceSize.MICRO),  # free tier
                allocated_storage=20,                                                              # free tier
                storage_type=rds.StorageType.GP2,
                multi_az=False,
                database_name="smart_campus",
                credentials=credentials,
                vpc=vpc,
                vpc_subnets=db_subnets,
                publicly_accessible=bool(demo_db_access),
                security_groups=[db_sg],
                storage_encrypted=True,
                iam_authentication=True,
                backup_retention=cdk.Duration.days(1),
                delete_automated_backups=True,
                deletion_protection=False,
                removal_policy=cdk.RemovalPolicy.DESTROY,
                enable_performance_insights=False,
                cloudwatch_logs_exports=["postgresql"],
                cloudwatch_logs_retention=logs.RetentionDays.ONE_WEEK,
            )
            db_secret, db_host, db_target = instance.secret, instance.db_instance_endpoint_address, instance

        # Least-privilege service credential for the ingest path (role created by db/init scripts).
        ingest_secret = rds.DatabaseSecret(self, "IngestDbSecret", username="ingest_writer",
                                           master_secret=db_secret)
        # Attaching adds host / port / dbname to the secret - without it the Lambda has a password
        # but nowhere to connect (found on the first live run: KeyError 'host').
        ingest_secret = ingest_secret.attach(db_target)
        if demo_db_access:
            db_sg.add_ingress_rule(ec2.Peer.ipv4(demo_db_access), ec2.Port.tcp(5432), "demo workstation (temporary)")

        # ------------------------------------------------------------------ ingest Lambda
        lambda_sg = ec2.SecurityGroup(self, "IngestSg", vpc=vpc, description="Ingest Lambda")
        db_sg.add_ingress_rule(lambda_sg, ec2.Port.tcp(5432), "ingest Lambda to warehouse")

        # Lambda layer with psycopg (arm64). Three ways to build it:
        #   -c layer_zip=<path>      prebuilt by `make lambda-layer` (no Docker needed by the CDK CLI)
        #   default                  pip-installed inside the Lambda build image (needs Docker)
        #   -c skip_bundling=true    no dependencies - synth/tests only, never for deploys
        layer_zip = self.node.try_get_context("layer_zip")
        if layer_zip:
            layer_code = lambda_.Code.from_asset(layer_zip)
        else:
            bundling = None if self.node.try_get_context("skip_bundling") else cdk.BundlingOptions(
                image=lambda_.Runtime.PYTHON_3_12.bundling_image,
                platform="linux/arm64",
                command=["bash", "-c", "pip install 'psycopg[binary]==3.2.3' -t /asset-output/python"],
            )
            layer_code = lambda_.Code.from_asset(str(LAMBDA_SRC), bundling=bundling)
        deps_layer = lambda_.LayerVersion(
            self, "IngestDeps",
            description="psycopg for the ingest Lambda",
            compatible_runtimes=[lambda_.Runtime.PYTHON_3_12],
            compatible_architectures=[lambda_.Architecture.ARM_64],
            code=layer_code,
        )
        # New accounts often have a Lambda concurrency quota of 10, which leaves nothing to reserve.
        reserved = self.node.try_get_context("lambda_reserved_concurrency")
        reserved_concurrency = 20 if reserved is None else (int(reserved) or None)

        # Messages that still fail after Lambda's async retries land here instead of being dropped
        # (the handler may fail before it can archive to S3). Replay with `sqs receive-message`.
        ingest_dlq = sqs.Queue(
            self, "IngestDeadLetters",
            encryption=sqs.QueueEncryption.SQS_MANAGED,
            enforce_ssl=True,
            retention_period=cdk.Duration.days(14),
        )

        ingest_fn = lambda_.Function(
            self, "IngestFunction",
            retry_attempts=2,
            on_failure=destinations.SqsDestination(ingest_dlq),
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.ARM_64,
            handler="handler.handler",
            code=lambda_.Code.from_asset(str(LAMBDA_SRC), exclude=["__pycache__", "*.pyc"]),
            layers=[deps_layer],
            memory_size=256,
            timeout=cdk.Duration.seconds(30),
            # Caps DB connections from Lambda; Aurora Serverless v2 at 0.5 ACU allows ~100.
            reserved_concurrent_executions=reserved_concurrency,
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED),
            security_groups=[lambda_sg],
            environment={
                "DB_SECRET_ARN": ingest_secret.secret_arn,
                "RAW_BUCKET": raw_bucket.bucket_name,
                "LOG_LEVEL": "INFO",
            },
            log_retention=logs.RetentionDays.ONE_MONTH,
            tracing=lambda_.Tracing.ACTIVE,
        )
        ingest_secret.grant_read(ingest_fn)
        raw_bucket.grant_put(ingest_fn, "telemetry/*")

        # ------------------------------------------------------------------ IoT Core
        # Messages the rule cannot deliver (e.g. Lambda throttled) are kept in S3, never dropped.
        iot_error_role = iam.Role(self, "IotErrorActionRole",
                                  assumed_by=iam.ServicePrincipal("iot.amazonaws.com"))
        raw_bucket.grant_put(iot_error_role, "iot-errors/*")

        rule = iot.CfnTopicRule(
            self, "TelemetryRule",
            topic_rule_payload=iot.CfnTopicRule.TopicRulePayloadProperty(
                sql=f"SELECT * FROM '{TELEMETRY_TOPIC}'",
                aws_iot_sql_version="2016-03-23",
                rule_disabled=False,
                description="Route building telemetry to the ingest Lambda",
                actions=[iot.CfnTopicRule.ActionProperty(
                    lambda_=iot.CfnTopicRule.LambdaActionProperty(function_arn=ingest_fn.function_arn))],
                error_action=iot.CfnTopicRule.ActionProperty(
                    s3=iot.CfnTopicRule.S3ActionProperty(
                        bucket_name=raw_bucket.bucket_name,
                        key="iot-errors/${parse_time('yyyy-MM-dd', timestamp())}/${newuuid()}.json",
                        role_arn=iot_error_role.role_arn,
                    )),
            ),
        )
        ingest_fn.add_permission(
            "AllowIotInvoke",
            principal=iam.ServicePrincipal("iot.amazonaws.com"),
            source_arn=rule.attr_arn,
        )

        # Each gateway/device is an IoT Thing named after its building and may only publish
        # telemetry for that building - a compromised device cannot spoof other buildings.
        iot.CfnPolicy(
            self, "BuildingGatewayPolicy",
            policy_name=f"smart-campus-{env_name}-building-gateway",
            policy_document={
                "Version": "2012-10-17",
                "Statement": [
                    {"Effect": "Allow", "Action": "iot:Connect",
                     "Resource": f"arn:aws:iot:{self.region}:{self.account}:client/"
                                 "${iot:Connection.Thing.ThingName}"},
                    {"Effect": "Allow", "Action": "iot:Publish",
                     "Resource": f"arn:aws:iot:{self.region}:{self.account}:topic/campus/{site_id}/"
                                 "${iot:Connection.Thing.ThingName}/*"},
                ],
            },
        )

        # A site gateway (one BMS head-end forwarding for many buildings) may publish for every
        # building of the site stored in its Thing attributes - and for no other site.
        gateway_policy = iot.CfnPolicy(
            self, "SiteGatewayPolicy",
            policy_name=f"smart-campus-{env_name}-site-gateway",
            policy_document={
                "Version": "2012-10-17",
                "Statement": [
                    {"Effect": "Allow", "Action": "iot:Connect",
                     "Resource": f"arn:aws:iot:{self.region}:{self.account}:client/"
                                 "${iot:Connection.Thing.ThingName}"},
                    {"Effect": "Allow", "Action": "iot:Publish",
                     "Resource": f"arn:aws:iot:{self.region}:{self.account}:topic/campus/"
                                 "${iot:Connection.Thing.Attributes[site]}/*"},
                ],
            },
        )
        gateway = iot.CfnThing(
            self, "SiteGateway",
            thing_name=f"{site_id.lower()}-gateway-{env_name}",
            attribute_payload=iot.CfnThing.AttributePayloadProperty(attributes={"site": site_id}),
        )
        # The gateway's X.509 certificate is created and attached by scripts/aws_demo.sh, so the
        # private key never passes through CloudFormation.

        # ------------------------------------------------------------------ monitoring
        alarm_topic = sns.Topic(self, "PlatformAlarms", display_name="Smart Campus platform alarms")
        if alarm_email:
            alarm_topic.add_subscription(subs.EmailSubscription(alarm_email))
        notify = cw_actions.SnsAction(alarm_topic)

        alarms = {
            "IngestErrors": ingest_fn.metric_errors(period=cdk.Duration.minutes(5)),
            "IngestThrottles": ingest_fn.metric_throttles(period=cdk.Duration.minutes(5)),
            "DeadLetterDepth": ingest_dlq.metric_approximate_number_of_messages_visible(
                period=cdk.Duration.minutes(5), statistic="Maximum"),
        }
        for name, metric in alarms.items():
            alarm = cloudwatch.Alarm(self, name, metric=metric, threshold=1, evaluation_periods=1,
                                     treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING)
            alarm.add_alarm_action(notify)

        # Telemetry silence: no invocations for 30 minutes means devices or the rule are down.
        silence = cloudwatch.Alarm(
            self, "TelemetrySilence",
            metric=ingest_fn.metric_invocations(period=cdk.Duration.minutes(30), statistic="Sum"),
            threshold=1, evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.LESS_THAN_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.BREACHING,
            alarm_description="No telemetry ingested for 30 minutes",
        )
        silence.add_alarm_action(notify)

        # ------------------------------------------------------------------ cost guardrail
        # Emails at the first cent of real (post-credit) spend. AWS Budgets is free for 2 budgets.
        budget_email = self.node.try_get_context("budget_email")
        if budget_email:
            budgets.CfnBudget(
                self, "ZeroSpendBudget",
                budget=budgets.CfnBudget.BudgetDataProperty(
                    budget_name=f"smart-campus-{env_name}-zero-spend",
                    budget_type="COST",
                    time_unit="MONTHLY",
                    budget_limit=budgets.CfnBudget.SpendProperty(amount=1, unit="USD"),
                    cost_types=budgets.CfnBudget.CostTypesProperty(include_credit=False),
                ),
                notifications_with_subscribers=[
                    budgets.CfnBudget.NotificationWithSubscribersProperty(
                        notification=budgets.CfnBudget.NotificationProperty(
                            notification_type="ACTUAL", comparison_operator="GREATER_THAN",
                            threshold=1, threshold_type="PERCENTAGE"),  # 1% of $1 = $0.01
                        subscribers=[budgets.CfnBudget.SubscriberProperty(
                            subscription_type="EMAIL", address=budget_email)],
                    ),
                ],
            )

        # ------------------------------------------------------------------ outputs
        cdk.CfnOutput(self, "RawBucketName", value=raw_bucket.bucket_name)
        cdk.CfnOutput(self, "ClusterEndpoint", value=db_host)
        cdk.CfnOutput(self, "AdminSecretArn", value=db_secret.secret_arn)
        cdk.CfnOutput(self, "IngestSecretArn", value=ingest_secret.secret_arn)
        cdk.CfnOutput(self, "IngestFunctionName", value=ingest_fn.function_name)
        cdk.CfnOutput(self, "TelemetryRuleName", value=rule.ref)
        cdk.CfnOutput(self, "GatewayThingName", value=gateway.ref)
        cdk.CfnOutput(self, "GatewayPolicyName", value=gateway_policy.ref)
