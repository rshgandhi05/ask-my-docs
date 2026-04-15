#!/usr/bin/env node
import "source-map-support/register";
import * as cdk from "aws-cdk-lib";
import { AskMyDocsStack } from "../lib/ask-my-docs-stack";

const app = new cdk.App();
new AskMyDocsStack(app, "AskMyDocsStack", {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.CDK_DEFAULT_REGION ?? "us-east-1",
  },
});
