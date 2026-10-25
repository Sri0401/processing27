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
from aws_cdk import aws_cloudwatch as cloudwatch
from aws_cdk import aws_cloudwatch_actions as cw_actions
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_iot as iot
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_rds as rds
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sns_subscriptions as subs
from constructs import Construct

REPO_ROOT = Path(__file__).resolve().parents[2]
LAMBDA_SRC = REPO_ROOT / "lambdas" / "ingest"
TELEMETRY_TOPIC = "campus/+/+/+/telemetry"


class SmartCampusPlatformStack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, *, env_name: str, site_id: str,
                 alarm_email: str | None = None, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        is_prod = env_name == "prod"
        removal = cdk.RemovalPolicy.RETAIN if is_prod else cdk.RemovalPolicy.DESTROY

        # ------------------------------------------------------------------ network
        # Private subnets only. No NAT gateway: the Lambda reaches S3 and Secrets Manager
        # through VPC endpoints, which is cheaper and keeps traffic off the internet.
        vpc = ec2.Vpc(
            self, "Vpc",
            max_azs=2,
            nat_gateways=0,
            subnet_configuration=[
                ec2.SubnetConfiguration(name="data", subnet_type=ec2.SubnetType.PRIVATE_ISOLATED, cidr_mask=24),
            ],
        )
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

        # ------------------------------------------------------------------ Aurora PostgreSQL
        db_sg = ec2.SecurityGroup(self, "DbSg", vpc=vpc, description="Aurora - ingress from platform compute only",
                                  allow_all_outbound=False)
        cluster = rds.DatabaseCluster(
            self, "Warehouse",
            engine=rds.DatabaseClusterEngine.aurora_postgres(version=rds.AuroraPostgresEngineVersion.VER_16_4),
            default_database_name="smart_campus",
            credentials=rds.Credentials.from_generated_secret("platform_admin"),
            writer=rds.ClusterInstance.serverless_v2("writer"),
            serverless_v2_min_capacity=0.5,
            serverless_v2_max_capacity=4 if is_prod else 2,
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED),
            security_groups=[db_sg],
            storage_encrypted=True,
            iam_authentication=True,
            backup=rds.BackupProps(retention=cdk.Duration.days(14 if is_prod else 1)),
            deletion_protection=is_prod,
            removal_policy=cdk.RemovalPolicy.SNAPSHOT if is_prod else cdk.RemovalPolicy.DESTROY,
            cloudwatch_logs_exports=["postgresql"],
            cloudwatch_logs_retention=logs.RetentionDays.ONE_MONTH,
        )
        # Least-privilege service credential for the ingest path (role created by db/init scripts).
        ingest_secret = rds.DatabaseSecret(self, "IngestDbSecret", username="ingest_writer",
                                           master_secret=cluster.secret)

        # ------------------------------------------------------------------ ingest Lambda
        lambda_sg = ec2.SecurityGroup(self, "IngestSg", vpc=vpc, description="Ingest Lambda")
        db_sg.add_ingress_rule(lambda_sg, ec2.Port.tcp(5432), "ingest Lambda to Aurora")

        # Dependencies are pip-installed inside the Lambda build image (needs Docker).
        # `-c skip_bundling=true` synthesises without Docker, e.g. in CI - never for deploys.
        bundling = None if self.node.try_get_context("skip_bundling") else cdk.BundlingOptions(
            image=lambda_.Runtime.PYTHON_3_12.bundling_image,
            command=["bash", "-c", "pip install 'psycopg[binary]==3.2.3' -t /asset-output/python"],
        )
        deps_layer = lambda_.LayerVersion(
            self, "IngestDeps",
            description="psycopg for the ingest Lambda",
            compatible_runtimes=[lambda_.Runtime.PYTHON_3_12],
            code=lambda_.Code.from_asset(str(LAMBDA_SRC), bundling=bundling),
        )

        ingest_fn = lambda_.Function(
            self, "IngestFunction",
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.ARM_64,
            handler="handler.handler",
            code=lambda_.Code.from_asset(str(LAMBDA_SRC), exclude=["__pycache__", "*.pyc"]),
            layers=[deps_layer],
            memory_size=256,
            timeout=cdk.Duration.seconds(30),
            # Caps DB connections from Lambda; Aurora Serverless v2 at 0.5 ACU allows ~100.
            reserved_concurrent_executions=20,
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

        # ------------------------------------------------------------------ monitoring
        alarm_topic = sns.Topic(self, "PlatformAlarms", display_name="Smart Campus platform alarms")
        if alarm_email:
            alarm_topic.add_subscription(subs.EmailSubscription(alarm_email))
        notify = cw_actions.SnsAction(alarm_topic)

        alarms = {
            "IngestErrors": ingest_fn.metric_errors(period=cdk.Duration.minutes(5)),
            "IngestThrottles": ingest_fn.metric_throttles(period=cdk.Duration.minutes(5)),
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

        # ------------------------------------------------------------------ outputs
        cdk.CfnOutput(self, "RawBucketName", value=raw_bucket.bucket_name)
        cdk.CfnOutput(self, "ClusterEndpoint", value=cluster.cluster_endpoint.hostname)
        cdk.CfnOutput(self, "IngestFunctionName", value=ingest_fn.function_name)
        cdk.CfnOutput(self, "TelemetryRuleName", value=rule.ref)
