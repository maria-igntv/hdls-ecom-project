"""Small SigV4 client for logical backups of immutable local demo objects."""
import datetime
import hashlib
import hmac
import urllib.parse
import urllib.request

ACCESS = "GK" + "0" * 24
SECRET = "1" * 64


def request(method, key, endpoint="http://127.0.0.1:13900", body=b"", content_type="application/octet-stream"):
    now = datetime.datetime.now(datetime.timezone.utc)
    timestamp = now.strftime("%Y%m%dT%H%M%SZ")
    day = now.strftime("%Y%m%d")
    host = urllib.parse.urlsplit(endpoint).netloc
    path = "/product-images/" + urllib.parse.quote(key, safe="/~")
    digest = hashlib.sha256(body).hexdigest()
    headers = {"host": host, "x-amz-content-sha256": digest, "x-amz-date": timestamp}
    signed = ";".join(sorted(headers))
    canonical = method + "\n" + path + "\n\n" + "".join(k + ":" + headers[k] + "\n" for k in sorted(headers)) + "\n" + signed + "\n" + digest
    scope = day + "/garage/s3/aws4_request"
    message = "AWS4-HMAC-SHA256\n" + timestamp + "\n" + scope + "\n" + hashlib.sha256(canonical.encode()).hexdigest()
    signing = ("AWS4" + SECRET).encode()
    for part in (day, "garage", "s3", "aws4_request"):
        signing = hmac.new(signing, part.encode(), hashlib.sha256).digest()
    signature = hmac.new(signing, message.encode(), hashlib.sha256).hexdigest()
    headers["Authorization"] = "AWS4-HMAC-SHA256 Credential=" + ACCESS + "/" + scope + ", SignedHeaders=" + signed + ", Signature=" + signature
    headers["Content-Type"] = content_type
    headers["Cache-Control"] = "public,max-age=31536000,immutable"
    req = urllib.request.Request(endpoint + path, data=body if method == "PUT" else None, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=15) as response:
        return response.read(), dict(response.headers)
