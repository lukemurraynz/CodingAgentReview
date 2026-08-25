"""Resolve OCI image index -> manifest -> config.Cmd for the running mcpserver image."""
import json
import os
import subprocess

AZ = os.environ.get("AZ_EXE", r"C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin\az.cmd")
REPO = "agentic-engineering-harness/mcpserver-harness-dev"
REG = "acrharnessdevlm.azurecr.io"
TAG = "azd-deploy-1787651080"


def blob(digest: str) -> dict:
    raw = subprocess.run(
        [AZ, "rest", "--method", "GET", "--url",
         f"https://{REG}/v2/{REPO}/blobs/{digest}"],
        capture_output=True, text=True, shell=True).stdout
    return json.loads(raw)


index_blob = blob(TAG)  # tag resolves to index for multi-arch builds
if "manifests" in index_blob:
    entry = next(m for m in index_blob["manifests"]
                 if m.get("platform", {}).get("architecture") == "amd64")
    manifest = blob(entry["digest"])
else:
    manifest = index_blob

cfg_digest = manifest["config"]["digest"]
config = blob(cfg_digest).get("config", {})
print("Cmd:", json.dumps(config.get("Cmd")))
print("Entrypoint:", config.get("Entrypoint"))
print("WorkingDir:", config.get("WorkingDir"))
