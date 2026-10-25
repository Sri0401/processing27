"""Policy-as-tests: assert the security and reliability properties of the synthesised template."""

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Match, Template
from smart_campus_infra.platform_stack import TELEMETRY_TOPIC, SmartCampusPlatformStack


@pytest.fixture(scope="module")
def template() -> Template:
    # Skip Docker bundling of the Lambda layer in unit tests
    app = cdk.App(context={"skip_bundling": True})
    stack = SmartCampusPlatformStack(app, "Test", env_name="dev", site_id="Lamb",
                                     env=cdk.Environment(account="111111111111", region="eu-west-2"))
    return Template.from_stack(stack)


def test_raw_bucket_is_private_encrypted_and_tls_only(template):
    template.has_resource_properties("AWS::S3::Bucket", {
        "BucketEncryption": Match.object_like({"ServerSideEncryptionConfiguration": Match.any_value()}),
        "PublicAccessBlockConfiguration": {
            "BlockPublicAcls": True, "BlockPublicPolicy": True,
            "IgnorePublicAcls": True, "RestrictPublicBuckets": True,
        },
    })
    template.has_resource_properties("AWS::S3::BucketPolicy", {
        "PolicyDocument": {"Statement": Match.array_with([Match.object_like({
            "Effect": "Deny", "Condition": {"Bool": {"aws:SecureTransport": "false"}}})])},
    })


def test_database_is_encrypted_and_not_public(template):
    template.has_resource_properties("AWS::RDS::DBCluster", {"StorageEncrypted": True, "Engine": "aurora-postgresql"})
    template.resource_properties_count_is("AWS::RDS::DBInstance", {"PubliclyAccessible": True}, 0)


def test_no_nat_gateway(template):
    template.resource_count_is("AWS::EC2::NatGateway", 0)


def test_iot_rule_routes_telemetry_with_error_action(template):
    template.has_resource_properties("AWS::IoT::TopicRule", {
        "TopicRulePayload": Match.object_like({
            "Sql": f"SELECT * FROM '{TELEMETRY_TOPIC}'",
            "ErrorAction": Match.object_like({"S3": Match.any_value()}),
        }),
    })


def test_ingest_lambda_is_in_vpc_with_capped_concurrency(template):
    template.has_resource_properties("AWS::Lambda::Function", {
        "Handler": "handler.handler",
        "Runtime": "python3.12",
        "ReservedConcurrentExecutions": 20,
        "VpcConfig": Match.any_value(),
        "Environment": {"Variables": Match.object_like({"DB_SECRET_ARN": Match.any_value()})},
    })


def test_alarms_exist(template):
    template.resource_count_is("AWS::CloudWatch::Alarm", 3)
