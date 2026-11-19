#!/bin/sh
# Re-trust the sandbox egress proxy CA for yt-dlp.
#
# The sandbox MITMs TLS: yt-dlp (via requests/certifi) needs the proxy CA
# in certifi's bundle. The proxy CA can ROTATE on a runtime restart —
# if every yt-dlp call suddenly fails with
# "SSL: CERTIFICATE_VERIFY_FAILED: self-signed certificate in chain",
# run this script and re-run the pipeline. It appends the current
# proxy CA bundle to the venv's certifi bundle (duplicates are harmless).
set -e
HERE=$(dirname "$0")
CA_BUNDLE="${SSL_CERT_FILE:-/run/hatch/egress-tls/ca-bundle.pem}"
CACERT="$HERE/venv/lib/python3.12/site-packages/certifi/cacert.pem"
if [ ! -f "$CA_BUNDLE" ]; then
    echo "proxy CA bundle not found: $CA_BUNDLE" >&2
    exit 1
fi
if [ ! -f "$CACERT" ]; then
    echo "certifi bundle not found: $CACERT" >&2
    exit 1
fi
cat "$CA_BUNDLE" >> "$CACERT"
echo "appended $CA_BUNDLE to certifi bundle ($(grep -c 'BEGIN CERTIFICATE' "$CACERT") certs total)"
