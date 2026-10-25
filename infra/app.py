#!/usr/bin/env python3
"""CDK app for the Smart Campus OT data platform.

    cdk synth                                   # CloudFormation template (no AWS account needed)
    cdk deploy -c env_name=dev -c alarm_email=you@example.ac.uk
"""

import os

import aws_cdk as cdk

from smart_campus_infra.platform_stack import SmartCampusPlatformStack

app = cdk.App()
env_name = app.node.try_get_context("env_name") or "dev"

stack = SmartCampusPlatformStack(
    app,
    f"SmartCampus-{env_name}",
    env_name=env_name,
    site_id=app.node.try_get_context("site_id") or "Lamb",
    alarm_email=app.node.try_get_context("alarm_email") or None,
    env=cdk.Environment(
        account=os.getenv("CDK_DEFAULT_ACCOUNT"),
        region=os.getenv("CDK_DEFAULT_REGION", "eu-west-2"),  # London: UK data residency
    ),
    description="Smart Campus OT telemetry & built-asset data platform",
)

for key, value in {
    "Project": "smart-campus",
    "Environment": env_name,
    "Owner": "campus-operations-data",
    "DataClassification": "internal",
}.items():
    cdk.Tags.of(stack).add(key, value)

app.synth()
