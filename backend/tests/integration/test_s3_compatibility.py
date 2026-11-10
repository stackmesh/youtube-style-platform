import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

import boto3
import pytest
from botocore import UNSIGNED
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.client import Config
from botocore.exceptions import ClientError

ENDPOINT = os.environ["S3_ENDPOINT_URL"]
PUBLIC_ENDPOINT = os.environ["S3_PUBLIC_ENDPOINT_URL"]
ACCESS_KEY = os.environ["S3_ACCESS_KEY"]
SECRET_KEY = os.environ["S3_SECRET_KEY"]
BUCKET = os.environ["S3_BUCKET"]
REGION = os.environ.get("S3_REGION", "us-east-1")
ORIGIN = os.environ.get("PUBLIC_APP_URL", "http://localhost")


def client(endpoint: str = ENDPOINT, access_key: str = ACCESS_KEY, secret_key: str = SECRET_KEY):
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=REGION,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def anonymous():
    return boto3.client(
        "s3",
        endpoint_url=ENDPOINT,
        aws_access_key_id="",
        aws_secret_access_key="",
        region_name=REGION,
        config=Config(signature_version=UNSIGNED, s3={"addressing_style": "path"}),
    )


def fetch(url: str, method: str = "GET", data: bytes | None = None, headers: dict | None = None):
    request = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def test_bucket_exists_and_application_cannot_create_another() -> None:
    s3 = client()
    s3.head_bucket(Bucket=BUCKET)
    with pytest.raises(ClientError) as caught:
        s3.create_bucket(Bucket="not-allowed-for-the-app")
    assert caught.value.response["ResponseMetadata"]["HTTPStatusCode"] in {403, 401}


def test_object_upload_download_metadata_and_delete() -> None:
    s3 = client()
    key = "tests/object-roundtrip.txt"
    body = b"seaweedfs-roundtrip"
    s3.put_object(Bucket=BUCKET, Key=key, Body=body, ContentType="text/plain")
    head = s3.head_object(Bucket=BUCKET, Key=key)
    assert head["ContentLength"] == len(body)
    assert head["ContentType"] == "text/plain"
    assert head["ETag"]
    downloaded = s3.get_object(Bucket=BUCKET, Key=key)
    assert downloaded["Body"].read() == body
    s3.delete_object(Bucket=BUCKET, Key=key)
    with pytest.raises(ClientError) as caught:
        s3.head_object(Bucket=BUCKET, Key=key)
    assert caught.value.response["ResponseMetadata"]["HTTPStatusCode"] == 404


def test_anonymous_and_bad_credentials_are_rejected() -> None:
    s3 = client()
    key = "tests/private-object.bin"
    s3.put_object(Bucket=BUCKET, Key=key, Body=b"secret-bytes")
    with pytest.raises(ClientError) as anonymous_error:
        anonymous().get_object(Bucket=BUCKET, Key=key)
    assert anonymous_error.value.response["ResponseMetadata"]["HTTPStatusCode"] == 403
    wrong = client(access_key=ACCESS_KEY, secret_key="not-the-real-secret")
    with pytest.raises(ClientError) as wrong_error:
        wrong.get_object(Bucket=BUCKET, Key=key)
    assert wrong_error.value.response["ResponseMetadata"]["HTTPStatusCode"] in {403, 401}
    s3.delete_object(Bucket=BUCKET, Key=key)


def test_multipart_upload_list_complete_and_abort() -> None:
    s3 = client()
    key = "tests/multipart.bin"
    first = b"a" * (5 * 1024 * 1024)
    last = b"tail"
    created = s3.create_multipart_upload(Bucket=BUCKET, Key=key)
    upload_id = created["UploadId"]
    part_one = s3.upload_part(Bucket=BUCKET, Key=key, UploadId=upload_id, PartNumber=1, Body=first)
    part_two = s3.upload_part(Bucket=BUCKET, Key=key, UploadId=upload_id, PartNumber=2, Body=last)
    listed = s3.list_parts(Bucket=BUCKET, Key=key, UploadId=upload_id)
    sizes = {part["PartNumber"]: part["Size"] for part in listed["Parts"]}
    assert sizes == {1: len(first), 2: len(last)}
    s3.complete_multipart_upload(
        Bucket=BUCKET,
        Key=key,
        UploadId=upload_id,
        MultipartUpload={
            "Parts": [
                {"PartNumber": 1, "ETag": part_one["ETag"]},
                {"PartNumber": 2, "ETag": part_two["ETag"]},
            ]
        },
    )
    assert s3.head_object(Bucket=BUCKET, Key=key)["ContentLength"] == len(first) + len(last)
    s3.delete_object(Bucket=BUCKET, Key=key)

    aborted = s3.create_multipart_upload(Bucket=BUCKET, Key=key)
    s3.upload_part(
        Bucket=BUCKET,
        Key=key,
        UploadId=aborted["UploadId"],
        PartNumber=1,
        Body=b"small-last-part",
    )
    s3.abort_multipart_upload(Bucket=BUCKET, Key=key, UploadId=aborted["UploadId"])
    with pytest.raises(ClientError):
        s3.list_parts(Bucket=BUCKET, Key=key, UploadId=aborted["UploadId"])


def test_multipart_part_number_above_ten_thousand_is_rejected() -> None:
    s3 = client()
    key = "tests/part-limit.bin"
    created = s3.create_multipart_upload(Bucket=BUCKET, Key=key)
    try:
        with pytest.raises(ClientError) as caught:
            s3.upload_part(
                Bucket=BUCKET,
                Key=key,
                UploadId=created["UploadId"],
                PartNumber=10001,
                Body=b"nope",
            )
        assert caught.value.response["ResponseMetadata"]["HTTPStatusCode"] in {400, 403}
    finally:
        s3.abort_multipart_upload(Bucket=BUCKET, Key=key, UploadId=created["UploadId"])


def test_presigned_put_and_get_use_separate_endpoints_and_expire() -> None:
    internal = client()
    signer = client(endpoint=PUBLIC_ENDPOINT)
    key = "tests/presigned.txt"
    body = b"presigned-body"
    put_url = signer.generate_presigned_url(
        "put_object",
        Params={"Bucket": BUCKET, "Key": key, "ContentType": "text/plain"},
        ExpiresIn=120,
    )
    assert urlparse(put_url).hostname == urlparse(PUBLIC_ENDPOINT).hostname
    assert urlparse(put_url).hostname != urlparse(ENDPOINT).hostname
    status, _headers, _payload = fetch(
        put_url,
        method="PUT",
        data=body,
        headers={"Content-Type": "text/plain"},
    )
    assert status in {200, 204}, status
    assert internal.head_object(Bucket=BUCKET, Key=key)["ContentLength"] == len(body)

    get_url = signer.generate_presigned_url(
        "get_object",
        Params={"Bucket": BUCKET, "Key": key},
        ExpiresIn=120,
    )
    status, headers, payload = fetch(get_url, headers={"Origin": ORIGIN})
    assert status == 200
    assert payload == body
    exposed = headers.get("Access-Control-Expose-Headers", "")
    assert "etag" in exposed.lower()

    expired = signer.generate_presigned_url(
        "get_object",
        Params={"Bucket": BUCKET, "Key": key},
        ExpiresIn=1,
    )
    time.sleep(2)
    status, _headers, _payload = fetch(expired)
    assert status in {400, 403}
    internal.delete_object(Bucket=BUCKET, Key=key)


def test_cors_preflight_allows_one_origin_and_put() -> None:
    key = "tests/cors.txt"
    status, headers, _payload = fetch(
        f"{ENDPOINT}/{BUCKET}/{key}",
        method="OPTIONS",
        headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": "PUT",
            "Access-Control-Request-Headers": "content-type,content-length",
        },
    )
    assert status in {200, 204}, status
    assert headers.get("Access-Control-Allow-Origin") == ORIGIN
    assert "PUT" in headers.get("Access-Control-Allow-Methods", "")
    assert "*" != headers.get("Access-Control-Allow-Origin")


def test_application_credentials_cannot_read_bucket_quota() -> None:
    request = AWSRequest(
        method="GET",
        url=f"{ENDPOINT.rstrip('/')}/{BUCKET}?seaweedfs-quota",
        headers={"x-amz-content-sha256": hashlib.sha256(b"").hexdigest()},
    )
    session = boto3.Session(
        aws_access_key_id=ACCESS_KEY,
        aws_secret_access_key=SECRET_KEY,
        region_name=REGION,
    )
    credentials = session.get_credentials()
    assert credentials is not None
    SigV4Auth(credentials.get_frozen_credentials(), "s3", REGION).add_auth(request)
    prepared = request.prepare()
    outbound = urllib.request.Request(
        prepared.url,
        method="GET",
        headers={key: value for key, value in prepared.headers.items()},
    )
    try:
        with urllib.request.urlopen(outbound, timeout=10) as response:
            body = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        body = exc.read()
        status = exc.code
    assert status in {401, 403}, body[:200]
    assert b"quota_size" not in body


def test_internal_endpoint_is_not_the_public_endpoint() -> None:
    assert urlparse(ENDPOINT).hostname != urlparse(PUBLIC_ENDPOINT).hostname
    assert json.loads(json.dumps({"endpoint": ENDPOINT}))["endpoint"] == ENDPOINT
