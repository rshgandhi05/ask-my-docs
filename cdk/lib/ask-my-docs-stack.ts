import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as iam from "aws-cdk-lib/aws-iam";
import * as apigw from "aws-cdk-lib/aws-apigateway";
import * as opensearchserverless from "aws-cdk-lib/aws-opensearchserverless";
import * as s3n from "aws-cdk-lib/aws-s3-notifications";
import * as path from "path";

export class AskMyDocsStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // ── S3 Bucket (the filing cabinet) ──────────────────────────────────────
    const pdfBucket = new s3.Bucket(this, "PdfBucket", {
      bucketName: `ask-my-docs-pdfs-${this.account}`,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
      encryption: s3.BucketEncryption.S3_MANAGED,
    });

    // ── OpenSearch Serverless collection (the smart index) ───────────────────
    const encryptionPolicy = new opensearchserverless.CfnSecurityPolicy(this, "EncryptionPolicy", {
      name: "ask-my-docs-enc",
      type: "encryption",
      policy: JSON.stringify({
        Rules: [{ ResourceType: "collection", Resource: ["collection/ask-my-docs"] }],
        AWSOwnedKey: true,
      }),
    });

    const networkPolicy = new opensearchserverless.CfnSecurityPolicy(this, "NetworkPolicy", {
      name: "ask-my-docs-net",
      type: "network",
      policy: JSON.stringify([
        {
          Rules: [
            { ResourceType: "collection", Resource: ["collection/ask-my-docs"] },
            { ResourceType: "dashboard", Resource: ["collection/ask-my-docs"] },
          ],
          AllowFromPublic: true,
        },
      ]),
    });

    const collection = new opensearchserverless.CfnCollection(this, "VectorCollection", {
      name: "ask-my-docs",
      type: "VECTORSEARCH",
    });
    collection.addDependency(encryptionPolicy);
    collection.addDependency(networkPolicy);

    // ── Shared Lambda role ───────────────────────────────────────────────────
    const lambdaRole = new iam.Role(this, "LambdaRole", {
      assumedBy: new iam.ServicePrincipal("lambda.amazonaws.com"),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName("service-role/AWSLambdaBasicExecutionRole"),
      ],
    });

    // Allow calling Bedrock models
    lambdaRole.addToPolicy(new iam.PolicyStatement({
      actions: ["bedrock:InvokeModel"],
      resources: [
        `arn:aws:bedrock:${this.region}::foundation-model/amazon.titan-embed-text-v1`,
        `arn:aws:bedrock:${this.region}::foundation-model/anthropic.claude-3-haiku-20240307-v1:0`,
      ],
    }));

    // Allow S3 read
    pdfBucket.grantRead(lambdaRole);

    // Allow OpenSearch Serverless API access
    lambdaRole.addToPolicy(new iam.PolicyStatement({
      actions: ["aoss:APIAccessAll"],
      resources: [collection.attrArn],
    }));

    const opensearchEndpoint = cdk.Fn.select(
      2,
      cdk.Fn.split("/", collection.attrCollectionEndpoint)
    );

    const commonEnv = {
      OPENSEARCH_ENDPOINT: opensearchEndpoint,
      INDEX_NAME: "ask-my-docs",
    };

    // ── Ingest Lambda ────────────────────────────────────────────────────────
    const ingestLambda = new lambda.Function(this, "IngestLambda", {
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: "handler.handler",
      code: lambda.Code.fromAsset(path.join(__dirname, "../../lambdas/ingest"), {
        bundling: {
          image: lambda.Runtime.PYTHON_3_11.bundlingImage,
          command: [
            "bash", "-c",
            "pip install -r requirements.txt -t /asset-output && cp -r . /asset-output",
          ],
        },
      }),
      role: lambdaRole,
      timeout: cdk.Duration.minutes(5),
      memorySize: 1024,
      environment: commonEnv,
    });

    // Trigger ingest Lambda on S3 upload
    pdfBucket.addEventNotification(
      s3.EventType.OBJECT_CREATED,
      new s3n.LambdaDestination(ingestLambda),
      { suffix: ".pdf" }
    );

    // ── Query Lambda ─────────────────────────────────────────────────────────
    const queryLambda = new lambda.Function(this, "QueryLambda", {
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: "handler.handler",
      code: lambda.Code.fromAsset(path.join(__dirname, "../../lambdas/query"), {
        bundling: {
          image: lambda.Runtime.PYTHON_3_11.bundlingImage,
          command: [
            "bash", "-c",
            "pip install -r requirements.txt -t /asset-output && cp -r . /asset-output",
          ],
        },
      }),
      role: lambdaRole,
      timeout: cdk.Duration.seconds(30),
      memorySize: 512,
      environment: commonEnv,
    });

    // ── OpenSearch data access policy ────────────────────────────────────────
    new opensearchserverless.CfnAccessPolicy(this, "DataAccessPolicy", {
      name: "ask-my-docs-access",
      type: "data",
      policy: JSON.stringify([
        {
          Rules: [
            {
              ResourceType: "index",
              Resource: ["index/ask-my-docs/*"],
              Permission: ["aoss:CreateIndex", "aoss:WriteDocument", "aoss:ReadDocument", "aoss:DescribeIndex"],
            },
            {
              ResourceType: "collection",
              Resource: ["collection/ask-my-docs"],
              Permission: ["aoss:CreateCollectionItems", "aoss:DescribeCollectionItems"],
            },
          ],
          Principal: [lambdaRole.roleArn],
        },
      ]),
    });

    // ── API Gateway ──────────────────────────────────────────────────────────
    const api = new apigw.RestApi(this, "AskMyDocsApi", {
      restApiName: "ask-my-docs",
      defaultCorsPreflightOptions: {
        allowOrigins: apigw.Cors.ALL_ORIGINS,
        allowMethods: apigw.Cors.ALL_METHODS,
      },
    });

    const queryResource = api.root.addResource("query");
    queryResource.addMethod("POST", new apigw.LambdaIntegration(queryLambda));

    // ── Outputs ──────────────────────────────────────────────────────────────
    new cdk.CfnOutput(this, "BucketName", {
      value: pdfBucket.bucketName,
      description: "Upload your PDFs here",
    });

    new cdk.CfnOutput(this, "ApiUrl", {
      value: api.url,
      description: "POST /query with {question: '...'}",
    });

    new cdk.CfnOutput(this, "CollectionEndpoint", {
      value: collection.attrCollectionEndpoint,
      description: "OpenSearch Serverless endpoint",
    });
  }
}
