# Ask My Docs- Serverless RAG on AWS

> Chat with any PDF using 100% serverless AWS infrastructure. Upload a doc, ask questions, get answers grounded in your content. Spin it up in minutes, destroy it just as fast.

Built by an AWS Community Builder as a demo of event-driven, serverless RAG architecture using AWS CDK.

---

## Architecture

```
INGEST FLOW
PDF → S3 → Lambda (chunk + embed) → Bedrock Titan Embeddings → OpenSearch Serverless

QUERY FLOW
User → API Gateway → Lambda (retrieve + generate) → OpenSearch → Bedrock Claude → Answer
```

## Stack

| Service | Role |
|---|---|
| S3 | PDF storage |
| Lambda (ingest) | Chunk PDF, call Titan Embeddings, store vectors |
| Lambda (query) | Embed query, k-NN search, call Claude |
| Amazon Bedrock | Titan for embeddings, Claude for generation |
| OpenSearch Serverless | Vector index (k-NN) |
| API Gateway | REST endpoint for queries |
| AWS CDK | All infra as code — deploy & destroy in 1 command |

---

## Prerequisites

- AWS CLI configured (`aws configure`)
- Node.js 18+
- Python 3.11+
- AWS CDK v2 (`npm install -g aws-cdk`)
- Bedrock model access enabled in your region:
  - `amazon.titan-embed-text-v1`
  - `anthropic.claude-3-haiku-20240307-v1:0`

---

## Quickstart

```bash
# 1. Clone
git clone https://github.com/YOUR_USERNAME/ask-my-docs.git
cd ask-my-docs

# 2. Install CDK deps
cd cdk
npm install

# 3. Deploy everything
cdk bootstrap   # first time only
cdk deploy

# 4. Note the outputs — you'll get:
#    BucketName: ask-my-docs-pdfs-xxxx
#    ApiUrl: https://xxxx.execute-api.us-east-1.amazonaws.com/prod

# 5. Upload a PDF
aws s3 cp my-doc.pdf s3://YOUR_BUCKET_NAME/

# 6. Query it
curl -X POST YOUR_API_URL/query \
  -H "Content-Type: application/json" \
  -d '{"question": "What are the main topics in this document?"}'

# 7. Tear it all down (avoid ongoing costs)
cdk destroy
```

---

## Project Structure

```
ask-my-docs/
├── cdk/                    # CDK stack (TypeScript)
│   ├── bin/app.ts
│   ├── lib/ask-my-docs-stack.ts
│   └── package.json
├── lambdas/
│   ├── ingest/             # Triggered by S3 upload
│   │   ├── handler.py
│   │   └── requirements.txt
│   └── query/              # Called by API Gateway
│       ├── handler.py
│       └── requirements.txt
└── README.md
```

---

## Cost Notes

This is designed to be near-zero cost when idle:
- OpenSearch Serverless has a minimum OCU charge (~$0.24/hr per collection) — **destroy when not using**
- Lambda and API Gateway are pay-per-request
- Bedrock is pay-per-token
- `cdk destroy` removes everything cleanly

---

## Gotchas I ran into (real ones)

These are issues I actually hit while building this — documenting them so you don't waste time on the same things.

**1. OpenSearch Serverless isn't ready immediately after deploy.**
It takes 5–10 minutes after `cdk deploy` or CloudFormation finishes before OpenSearch actually accepts data. If you upload a PDF right away and get a `store failed: 403`, just wait and re-upload.

**2. The data access policy needs the exact Lambda role ARN.**
The CloudFormation template sets this up automatically, but if you're tweaking manually: go to IAM → find your Lambda's execution role → copy the full ARN and paste it into the OpenSearch data access policy Principal field. A partial ARN or account root won't work.

**3. Create the k-NN index before uploading your first PDF.**
OpenSearch won't auto-create a vector index. Open OpenSearch Dashboards → Dev Tools and run the index creation command in the README before uploading anything.

**4. The ingest Lambda needs pypdf which isn't available inline.**
The CloudFormation inline code version uses a basic text fallback. For production, use the CDK version which bundles dependencies properly.

---

## Create the k-NN index (do this once after deploy)

Open OpenSearch Dashboards → Dev Tools and run:

```json
PUT /ask-my-docs
{
  "settings": { "index": { "knn": true } },
  "mappings": {
    "properties": {
      "embedding": { "type": "knn_vector", "dimension": 1536 },
      "text": { "type": "text" },
      "source": { "type": "keyword" },
      "chunk_index": { "type": "integer" },
      "doc_id": { "type": "keyword" }
    }
  }
}
```

---

## Blog Post

Read the full walkthrough on Medium https://medium.com/@rshgandhi05/ask-my-docs-i-built-a-serverless-rag-system-on-aws-that-costs-0-when-youre-not-using-it-2cf0f4137005 

---
