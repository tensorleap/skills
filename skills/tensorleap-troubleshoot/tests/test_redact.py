"""tl_collect.sh --redact must remove every secret below and keep the diagnostic lines.

Run: python3 skills/tensorleap-troubleshoot/tests/test_redact.py   (or pytest)
"""
import os
import subprocess
import tempfile

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "tl_collect.sh")
ESC = "\x1b"

SECRETS = [
    "sa-private-material-XYZ",           # inline PEM inside a GCP service-account JSON
    "MIIEvQIBADANBgkqhkiG9w0BAQEFAASC",  # multi-line PEM body
    "foobarbazqux",                       # MINIO_ROOT_PASSWORD env value
    "s3cr3t-auth-value",                  # AUTH_SECRET env value
    "ghp_abcdefghijklmnop",               # GITHUB_TOKEN=
    "tok-1234567890abcdef",               # Bearer token
    "dXNlcjpwYXNzd29yZA==",               # Basic auth header
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
    "AKIAABCDEFGHIJKLMNOP",
    "a1b2c3d4e5f6signature",              # X-Amz-Signature
    "hunter2pass",                        # user:pass@ in a URL
    "yaml-api-key-value",                 # YAML apiKey:
    "json-client-secret",                 # JSON "client_secret"
]

SAMPLE = f"""{ESC}[36mINFO{ESC}[0m Installation information:
    AUTH_SECRET:  {{"type":"service_account","private_key":"-----BEGIN PRIVATE KEY-----\\nsa-private-material-XYZ\\n-----END PRIVATE KEY-----\\n"}}
      MINIO_ROOT_PASSWORD:  foobarbazqux
      AUTH_SECRET=s3cr3t-auth-value
export GITHUB_TOKEN=ghp_abcdefghijklmnop
key: |
    -----BEGIN RSA PRIVATE KEY-----
    MIIEvQIBADANBgkqhkiG9w0BAQEFAASC
    -----END RSA PRIVATE KEY-----
Authorization: Bearer tok-1234567890abcdef
Authorization: Basic dXNlcjpwYXNzd29yZA==
cookie=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U
aws_access_key_id AKIAABCDEFGHIJKLMNOP
GET http://minio:9000/session/x.json?X-Amz-Signature=a1b2c3d4e5f6signature&X-Amz-Expires=600
mongodb://admin:hunter2pass@mongodb:27017/tensorleap
  apiKey: yaml-api-key-value
{{"client_secret": "json-client-secret", "user": "keep-me@example.com"}}
      KEYCLOAK_URL:  http://keycloak-http:80/auth
port: 4589
OOMKilled: Main engine process ran out of memory. Consider enabling auto-settings in the Engine Settings Dialog.
Warning  FailedScheduling  0/1 nodes are available: 1 Insufficient memory.
"""

KEEP = [
    "Installation information:",
    "KEYCLOAK_URL:  http://keycloak-http:80/auth",
    "port: 4589",
    "OOMKilled: Main engine process ran out of memory.",
    "1 Insufficient memory.",
    "keep-me@example.com",
    "X-Amz-Expires=600",
    "mongodb:27017/tensorleap",
]


def redact(text):
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "sample.log")
        with open(path, "w") as f:
            f.write(text)
        subprocess.run(["bash", SCRIPT, "--redact", path], check=True, capture_output=True)
        with open(path) as f:
            return f.read()


def test_secrets_removed_and_diagnostics_kept():
    out = redact(SAMPLE)
    leaked = [s for s in SECRETS if s in out]
    assert not leaked, "leaked: %s\n---\n%s" % (leaked, out)
    missing = [k for k in KEEP if k not in out]
    assert not missing, "over-redacted: %s\n---\n%s" % (missing, out)
    assert ESC not in out, "terminal color codes left in the output"


def test_binary_file_untouched():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "blob.bin")
        data = b"\x00\x01PASSWORD=x\x00"
        with open(path, "wb") as f:
            f.write(data)
        subprocess.run(["bash", SCRIPT, "--redact", path], check=True, capture_output=True)
        with open(path, "rb") as f:
            assert f.read() == data


if __name__ == "__main__":
    test_secrets_removed_and_diagnostics_kept()
    test_binary_file_untouched()
    print("ok")
