#!/usr/bin/env python3
"""Initialize single-node Garage; safe to repeat on existing stage-4 volumes."""
import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ["docker", "compose", "-p", os.environ.get("STAGE4_PROJECT", "hdls-stage4"), "-f", str(ROOT / "infra/stage4/compose.yaml")]


def command(*args, check=True):
    result = subprocess.run(COMPOSE + list(args), text=True, capture_output=True)
    if check and result.returncode:
        raise RuntimeError(result.stderr + result.stdout)
    return result.stdout + result.stderr


def garage(*args, check=True):
    return command("exec", "-T", "garage", "/garage", *args, check=check)


def initialize():
    status = garage("status")
    if "NO ROLE ASSIGNED" in status or "No nodes currently have a role" in status:
        node = re.search(r"^([0-9a-f]{16})\s", status, re.M).group(1)
        garage("layout", "assign", "-z", "local", "-c", "1G", node)
        garage("layout", "apply", "--version", "1")
    key = "GK" + "0" * 24
    existing = subprocess.run(COMPOSE + ["exec", "-T", "garage", "/garage", "key", "info", key], capture_output=True)
    if existing.returncode:
        garage("key", "import", "--yes", "-n", "commerce", key, "1" * 64)
    if "product-images" not in garage("bucket", "list"):
        garage("bucket", "create", "product-images")
    garage("bucket", "allow", "--read", "--write", "--owner", "--key", key, "product-images")
    garage("bucket", "website", "--allow", "product-images")
    print("Garage: product-images initialized (read/write key, public read-only website)")


if __name__ == "__main__":
    initialize()
