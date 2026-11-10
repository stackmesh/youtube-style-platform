#!/bin/sh
# Writes the SeaweedFS S3 identity file, then starts the official entrypoint.
# Administrative and application secrets stay in this container. They are not printed.
set -eu

: "${S3_ADMIN_ACCESS_KEY:?S3_ADMIN_ACCESS_KEY is required}"
: "${S3_ADMIN_SECRET_KEY:?S3_ADMIN_SECRET_KEY is required}"
: "${S3_ACCESS_KEY:?S3_ACCESS_KEY is required}"
: "${S3_SECRET_KEY:?S3_SECRET_KEY is required}"
: "${S3_BUCKET:?S3_BUCKET is required}"

json_escape() {
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

admin_key=$(json_escape "$S3_ADMIN_ACCESS_KEY")
admin_secret=$(json_escape "$S3_ADMIN_SECRET_KEY")
app_key=$(json_escape "$S3_ACCESS_KEY")
app_secret=$(json_escape "$S3_SECRET_KEY")
bucket=$(json_escape "$S3_BUCKET")

mkdir -p /etc/seaweedfs
umask 077
cat > /etc/seaweedfs/s3.json <<EOF
{
  "identities": [
    {
      "name": "anonymous",
      "actions": []
    },
    {
      "name": "storage-admin",
      "credentials": [
        {
          "accessKey": "${admin_key}",
          "secretKey": "${admin_secret}"
        }
      ],
      "actions": [
        "Admin",
        "Read",
        "Write",
        "List",
        "Tagging",
        "PutBucketPolicy",
        "PutBucketQuota",
        "GetBucketQuota"
      ]
    },
    {
      "name": "media-app",
      "credentials": [
        {
          "accessKey": "${app_key}",
          "secretKey": "${app_secret}"
        }
      ],
      "actions": [
        "Read:${bucket}",
        "Write:${bucket}",
        "List:${bucket}",
        "Tagging:${bucket}",
        "ListBucketMultipartUploads:${bucket}",
        "ListMultipartUploadParts:${bucket}"
      ]
    }
  ]
}
EOF
chmod 755 /etc/seaweedfs
if id seaweed >/dev/null 2>&1; then
  chown seaweed:seaweed /etc/seaweedfs/s3.json
  chmod 600 /etc/seaweedfs/s3.json
else
  chmod 600 /etc/seaweedfs/s3.json
fi
exec /entrypoint.sh "$@"
