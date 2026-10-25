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


def synth(env_name: str = "dev", **context) -> Template:
    app = cdk.App(context={"skip_bundling": True, **context})
    stack = SmartCampusPlatformStack(app, "Demo", env_name=env_name, site_id="Lamb",
                                     env=cdk.Environment(account="111111111111", region="eu-west-2"))
    return Template.from_stack(stack)


def test_database_is_private_by_default(template):
    template.has_resource_properties("AWS::RDS::DBInstance", {"PubliclyAccessible": False})
    # No security group opens PostgreSQL to an IP range (only the Lambda's SG may connect)
    template.resource_properties_count_is("AWS::EC2::SecurityGroup", {
        "SecurityGroupIngress": Match.array_with([Match.object_like(
            {"CidrIp": Match.any_value(), "FromPort": 5432})])}, 0)


def test_demo_db_access_is_limited_to_one_address():
    t = synth(demo_db_access="203.0.113.7/32")
    t.has_resource_properties("AWS::RDS::DBInstance", {"PubliclyAccessible": True})
    t.has_resource_properties("AWS::EC2::SecurityGroup", {
        "SecurityGroupIngress": Match.array_with([Match.object_like(
            {"CidrIp": "203.0.113.7/32", "FromPort": 5432, "ToPort": 5432})])})
    t.resource_count_is("AWS::EC2::NatGateway", 0)  # still no NAT


def test_demo_db_access_is_refused_for_prod():
    with pytest.raises(ValueError, match="prod"):
        synth(demo_db_access="203.0.113.7/32", env_name="prod")


def test_reserved_concurrency_can_be_disabled_for_new_accounts():
    t = synth(lambda_reserved_concurrency=0)
    lambdas = t.find_resources("AWS::Lambda::Function", {"Properties": {"Handler": "handler.handler"}})
    assert all("ReservedConcurrentExecutions" not in r["Properties"] for r in lambdas.values())


def test_zero_spend_budget_excludes_credits():
    t = synth(budget_email="me@example.com")
    t.has_resource_properties("AWS::Budgets::Budget", {
        "Budget": Match.object_like({"CostTypes": {"IncludeCredit": False}})})


def test_site_gateway_may_only_publish_for_its_own_site(template):
    template.has_resource_properties("AWS::IoT::Thing", {
        "AttributePayload": {"Attributes": {"site": "Lamb"}}})
    template.has_resource_properties("AWS::IoT::Policy", {
        "PolicyDocument": {"Statement": Match.array_with([Match.object_like({
            "Action": "iot:Publish",
            "Resource": Match.string_like_regexp(r"topic/campus/\$\{iot:Connection\.Thing\.Attributes\[site\]\}/\*"),
        })])}})
