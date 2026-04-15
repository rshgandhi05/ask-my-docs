"""
Ingest Lambda — triggered when a PDF lands in S3.
Think of this as the librarian: reads the book, cuts it into pages,
turns each page into a number-code (embedding), and files it in the index.
"""

import json
import os
import boto3
import hashlib
from io import BytesIO

s3 = boto3.client("s3")
bedrock = boto3.client("bedrock-runtime", region_name=os.environ["AWS_REGION"])
opensearch_endpoint = os.environ["OPENSEARCH_ENDPOINT"]
index_name = os.environ.get("INDEX_NAME", "ask-my-docs")

CHUNK_SIZE = 500        # characters per chunk
CHUNK_OVERLAP = 100     # overlap between chunks so context isn't lost at edges
EMBEDDING_MODEL = "amazon.titan-embed-text-v1"


def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """Extract raw text from PDF bytes using pypdf."""
    from pypdf import PdfReader
    reader = PdfReader(BytesIO(pdf_bytes))
    pages = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            pages.append(text.strip())
    return "\n\n".join(pages)


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """
    Split text into overlapping chunks.
    Like cutting a book into index cards — each card shares a few words
    with the previous one so meaning doesn't get cut off at the seam.
    """
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunks.append(text[start:end].strip())
        start += chunk_size - overlap
    return [c for c in chunks if len(c) > 50]


def embed_text(text: str) -> list[float]:
    """Call Bedrock Titan to turn text into a vector (list of numbers)."""
    body = json.dumps({"inputText": text})
    response = bedrock.invoke_model(
        modelId=EMBEDDING_MODEL,
        contentType="application/json",
        accept="application/json",
        body=body,
    )
    result = json.loads(response["body"].read())
    return result["embedding"]


def store_chunk(chunk: str, embedding: list[float], doc_id: str, chunk_index: int, source_key: str):
    """Store a chunk + its vector in OpenSearch Serverless."""
    import urllib3
    import urllib.request
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest
    from botocore.credentials import get_credentials
    import botocore.session

    session = botocore.session.get_session()
    credentials = session.get_credentials().get_frozen_credentials()

    doc = {
        "text": chunk,
        "embedding": embedding,
        "source": source_key,
        "chunk_index": chunk_index,
        "doc_id": doc_id,
    }

    url = f"https://{opensearch_endpoint}/{index_name}/_doc/{doc_id}_{chunk_index}"
    request = AWSRequest(method="PUT", url=url, data=json.dumps(doc), headers={"Content-Type": "application/json"})
    SigV4Auth(credentials, "aoss", os.environ["AWS_REGION"]).add_auth(request)

    http = urllib3.PoolManager()
    response = http.request(
        "PUT",
        url,
        body=json.dumps(doc).encode(),
        headers=dict(request.headers),
    )
    if response.status >= 300:
        raise Exception(f"OpenSearch store failed: {response.status} {response.data}")


def handler(event, context):
    """Entry point — called for each S3 put event."""
    for record in event["Records"]:
        bucket = record["s3"]["bucket"]["name"]
        key = record["s3"]["object"]["key"]

        if not key.lower().endswith(".pdf"):
            print(f"Skipping non-PDF: {key}")
            continue

        print(f"Processing: s3://{bucket}/{key}")

        # Download PDF
        obj = s3.get_object(Bucket=bucket, Key=key)
        pdf_bytes = obj["Body"].read()

        # Extract text
        text = extract_text_from_pdf(pdf_bytes)
        print(f"Extracted {len(text)} characters")

        # Chunk
        chunks = chunk_text(text)
        print(f"Split into {len(chunks)} chunks")

        # Unique ID for this document
        doc_id = hashlib.md5(key.encode()).hexdigest()[:12]

        # Embed and store each chunk
        for i, chunk in enumerate(chunks):
            embedding = embed_text(chunk)
            store_chunk(chunk, embedding, doc_id, i, key)
            print(f"Stored chunk {i+1}/{len(chunks)}")

        print(f"Done: {key} ingested as {len(chunks)} chunks")

    return {"statusCode": 200, "body": "Ingestion complete"}
