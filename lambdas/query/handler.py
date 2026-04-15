"""
Query Lambda — called by API Gateway when a user asks a question.
Think of this as the reference librarian: takes your question,
finds the most relevant pages in the index, then writes you a clear answer
based only on what those pages say.
"""

import json
import os
import boto3

bedrock = boto3.client("bedrock-runtime", region_name=os.environ["AWS_REGION"])
opensearch_endpoint = os.environ["OPENSEARCH_ENDPOINT"]
index_name = os.environ.get("INDEX_NAME", "ask-my-docs")

EMBEDDING_MODEL = "amazon.titan-embed-text-v1"
GENERATION_MODEL = "anthropic.claude-3-haiku-20240307-v1:0"
TOP_K = 5   # number of chunks to retrieve


def embed_query(text: str) -> list[float]:
    """Turn the user's question into a vector so we can find similar chunks."""
    body = json.dumps({"inputText": text})
    response = bedrock.invoke_model(
        modelId=EMBEDDING_MODEL,
        contentType="application/json",
        accept="application/json",
        body=body,
    )
    return json.loads(response["body"].read())["embedding"]


def retrieve_chunks(query_embedding: list[float]) -> list[str]:
    """
    k-NN search in OpenSearch — find the TOP_K chunks most similar
    to the query embedding. Like finding the index cards closest
    in meaning to your question.
    """
    import urllib3
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest
    import botocore.session

    session = botocore.session.get_session()
    credentials = session.get_credentials().get_frozen_credentials()

    search_body = {
        "size": TOP_K,
        "_source": ["text", "source"],
        "query": {
            "knn": {
                "embedding": {
                    "vector": query_embedding,
                    "k": TOP_K,
                }
            }
        },
    }

    url = f"https://{opensearch_endpoint}/{index_name}/_search"
    request = AWSRequest(
        method="POST",
        url=url,
        data=json.dumps(search_body),
        headers={"Content-Type": "application/json"},
    )
    SigV4Auth(credentials, "aoss", os.environ["AWS_REGION"]).add_auth(request)

    http = urllib3.PoolManager()
    response = http.request(
        "POST",
        url,
        body=json.dumps(search_body).encode(),
        headers=dict(request.headers),
    )

    if response.status >= 300:
        raise Exception(f"OpenSearch search failed: {response.status} {response.data}")

    hits = json.loads(response.data)["hits"]["hits"]
    return [hit["_source"]["text"] for hit in hits]


def generate_answer(question: str, context_chunks: list[str]) -> str:
    """
    Call Claude with the retrieved chunks as context.
    This is the RAG moment — Claude only answers using the provided context,
    not its own training data. Grounded, not hallucinated.
    """
    context = "\n\n---\n\n".join(context_chunks)
    prompt = f"""You are a helpful assistant answering questions about a document.
Use ONLY the context below to answer. If the answer isn't in the context, say so clearly.

CONTEXT:
{context}

QUESTION:
{question}

ANSWER:"""

    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": prompt}],
    })

    response = bedrock.invoke_model(
        modelId=GENERATION_MODEL,
        contentType="application/json",
        accept="application/json",
        body=body,
    )
    result = json.loads(response["body"].read())
    return result["content"][0]["text"]


def handler(event, context):
    """Entry point — called by API Gateway."""
    try:
        body = json.loads(event.get("body", "{}"))
        question = body.get("question", "").strip()

        if not question:
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "Missing 'question' in request body"}),
                "headers": {"Content-Type": "application/json"},
            }

        print(f"Question: {question}")

        # Step 1: embed the question
        query_embedding = embed_query(question)

        # Step 2: find most relevant chunks
        chunks = retrieve_chunks(query_embedding)
        print(f"Retrieved {len(chunks)} chunks")

        if not chunks:
            return {
                "statusCode": 200,
                "body": json.dumps({"answer": "No relevant content found. Have you uploaded a PDF yet?"}),
                "headers": {"Content-Type": "application/json"},
            }

        # Step 3: generate grounded answer
        answer = generate_answer(question, chunks)
        print(f"Generated answer: {answer[:100]}...")

        return {
            "statusCode": 200,
            "body": json.dumps({"answer": answer, "sources_used": len(chunks)}),
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
        }

    except Exception as e:
        print(f"Error: {e}")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)}),
            "headers": {"Content-Type": "application/json"},
        }
