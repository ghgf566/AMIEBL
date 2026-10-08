"""Reassemble the exact locally verified v1.0.0 files in an existing draft.

This job does not build, execute, or publish release files. Temporary chunks
are removed only after GitHub verifies the complete files' fixed SHA-256.
"""
import hashlib
import json
import os
import re
from pathlib import Path
from urllib.parse import quote

import httpx

API = "https://api.github.com/repos/ghgf566/AMIEBL"
RELEASE_ID = 407118858
SOURCE = "eab5dc1451bf38d1c008558ba8d49c85954c04bb"
MANIFEST = "amiebl-v1-relay-manifest.json"
EXPECTED = {
    "AMIEBL-v1.0.0-win-x64-portable.zip": (
        789887501, "297a8b4663fa8a268c117a9e4a64976fdf8d2f9f6a6dc9fd2c905652c962514a"
    ),
    "AMIEBL-v1.0.0-win-x64-setup.exe": (
        716583621, "d3e0d3d29268cbcc128687096ee071d3673417c94a67d745541b5a36791a53be"
    ),
    "SHA256SUMS.txt": (
        201, "7528e05dfe0533a6ace907bbeef0e7286ca254cef2f45446c7aa8fc10788fc05"
    ),
}


def api(client, method, path, **kwargs):
    try:
        response = client.request(method, API + path, **kwargs)
    except httpx.HTTPError as error:
        raise RuntimeError(f"GitHub network error: {type(error).__name__}") from None
    if response.status_code >= 400:
        raise RuntimeError(f"GitHub {method} failed: HTTP {response.status_code}")
    return response.json() if response.content else None


def draft(client):
    info = api(client, "GET", f"/releases/{RELEASE_ID}")
    if not info["draft"] or info["tag_name"] != "v1.0.0":
        raise RuntimeError("Relay requires the designated v1.0.0 draft")
    return info


def assets(client):
    result = {}
    page = 1
    while True:
        batch = api(client, "GET", f"/releases/{RELEASE_ID}/assets?per_page=100&page={page}")
        for asset in batch:
            if asset["name"] in result:
                raise RuntimeError("Duplicate asset name")
            result[asset["name"]] = asset
        if len(batch) < 100:
            return result
        page += 1


def matches(asset, size, sha):
    return bool(asset and asset["state"] == "uploaded" and asset["size"] == size
                and asset.get("digest") == "sha256:" + sha)


def download(client, asset, output):
    sha = hashlib.sha256()
    count = 0
    try:
        with client.stream("GET", API + f"/releases/assets/{asset['id']}",
                           headers={"Accept": "application/octet-stream"}) as response:
            if response.status_code != 200:
                raise RuntimeError(f"Asset download HTTP {response.status_code}")
            for block in response.iter_bytes(1024 * 1024):
                output.write(block)
                sha.update(block)
                count += len(block)
                if count > asset["size"]:
                    raise RuntimeError("Asset exceeds its declared size")
    except httpx.HTTPError as error:
        raise RuntimeError(f"Asset network error: {type(error).__name__}") from None
    if not matches(asset, count, sha.hexdigest()):
        raise RuntimeError("Downloaded chunk SHA-256 or size mismatch")


def verify_tag(client):
    target = api(client, "GET", "/git/ref/tags/v1.0.0")["object"]
    if target["type"] == "tag":
        target = api(client, "GET", "/git/tags/" + target["sha"])["object"]
    if target["type"] != "commit" or target["sha"] != SOURCE:
        raise RuntimeError("Release tag changed")


def main():
    work = Path(os.environ["RUNNER_TEMP"]) / "amiebl-v1-relay"
    work.mkdir(exist_ok=True)
    with httpx.Client(
        headers={"Authorization": "Bearer " + os.environ["GH_TOKEN"],
                 "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28"},
        timeout=httpx.Timeout(connect=30, read=180, write=600, pool=30),
        follow_redirects=True, trust_env=False,
    ) as client:
        draft(client)
        verify_tag(client)
        available = assets(client)
        if os.environ.get("RELAY_PREFLIGHT") == "1":
            name = "amiebl-v1-relay-preflight.txt"
            body = b"AMIEBL relay preflight\n"
            sha = hashlib.sha256(body).hexdigest()
            existing = available.get(name)
            if existing:
                if not matches(existing, len(body), sha):
                    raise RuntimeError("Unexpected preflight attachment")
                api(client, "DELETE", f"/releases/assets/{existing['id']}")
            upload = draft(client)["upload_url"].split("{", 1)[0]
            if upload != f"https://uploads.github.com/repos/ghgf566/AMIEBL/releases/{RELEASE_ID}/assets":
                raise RuntimeError("Unexpected preflight endpoint")
            response = client.post(upload + "?name=" + quote(name), content=body,
                                   headers={"Content-Type": "application/octet-stream"})
            if response.status_code != 201 or not matches(response.json(), len(body), sha):
                raise RuntimeError("Preflight upload did not verify")
            with (work / name).open("wb") as file:
                download(client, response.json(), file)
            api(client, "DELETE", f"/releases/assets/{response.json()['id']}")
            print("PREFLIGHT PASS: draft access, exact tag, verified upload/download and own-asset deletion", flush=True)
            return
        marker = available.get(MANIFEST)
        if not marker or marker["size"] > 128 * 1024:
            raise RuntimeError("Missing or oversized completion manifest")
        manifest_path = work / MANIFEST
        with manifest_path.open("wb") as file:
            download(client, marker, file)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest["release_id"] != RELEASE_ID or manifest["source_commit"] != SOURCE:
            raise RuntimeError("Unexpected manifest origin")
        if {f["name"] for f in manifest["files"]} != set(EXPECTED) - {"SHA256SUMS.txt"}:
            raise RuntimeError("Unexpected final file list")
        temporary = {}
        for item in manifest["files"]:
            name = item["name"]
            size, sha = EXPECTED[name]
            if item["size"] != size or item["sha256"] != sha:
                raise RuntimeError("Manifest final SHA-256 differs from local acceptance")
            kind = "portable" if name.endswith(".zip") else "setup"
            if len(item["parts"]) != (size + 16777215) // 16777216:
                raise RuntimeError("Unexpected part count")
            for index, part in enumerate(item["parts"]):
                if (part["name"] != f"amiebl-v1-relay-{kind}-{index:03d}.bin"
                        or part["index"] != index
                        or part["size"] != min(16777216, size - index * 16777216)
                        or not re.fullmatch("[0-9a-f]{64}", part["sha256"])):
                    raise RuntimeError("Invalid part identity")
                temporary[part["name"]] = part
            if matches(available.get(name), size, sha):
                print(f"Already complete and verified: {name}", flush=True)
                continue
            existing = available.get(name)
            if existing:
                if existing["state"] != "starter" or existing["size"] != 0:
                    raise RuntimeError("Refusing to replace an existing valid final asset")
                api(client, "DELETE", f"/releases/assets/{existing['id']}")
            path = work / name
            with path.open("wb") as file:
                for part in item["parts"]:
                    asset = available.get(part["name"])
                    if not matches(asset, part["size"], part["sha256"]):
                        raise RuntimeError("Missing verified chunk")
                    download(client, asset, file)
            with path.open("rb") as file:
                actual_sha = hashlib.file_digest(file, "sha256").hexdigest()
            if actual_sha != sha or path.stat().st_size != size:
                raise RuntimeError("Reassembled file differs from locally tested bytes")
            upload = draft(client)["upload_url"].split("{", 1)[0]
            if upload != f"https://uploads.github.com/repos/ghgf566/AMIEBL/releases/{RELEASE_ID}/assets":
                raise RuntimeError("Unexpected upload endpoint")
            def content():
                with path.open("rb") as file:
                    while block := file.read(1024 * 1024):
                        yield block
            response = client.post(upload + "?name=" + quote(name), content=content(),
                                   headers={"Content-Type": "application/octet-stream",
                                            "Content-Length": str(size)})
            if response.status_code != 201 or not matches(response.json(), size, sha):
                raise RuntimeError("Final GitHub upload did not verify")
            print(f"GitHub verified exact locally tested bytes: {name} sha256={sha}", flush=True)
        available = assets(client)
        if not all(matches(available.get(name), size, sha) for name, (size, sha) in EXPECTED.items()):
            raise RuntimeError("Not all three final assets verify")
        draft(client)
        verify_tag(client)
        for name, part in temporary.items():
            asset = available.get(name)
            if asset:
                if not matches(asset, part["size"], part["sha256"]):
                    raise RuntimeError("Temporary asset changed; refusing deletion")
                api(client, "DELETE", f"/releases/assets/{asset['id']}")
        api(client, "DELETE", f"/releases/assets/{marker['id']}")
        remaining = assets(client)
        if set(remaining) != set(EXPECTED):
            raise RuntimeError("Unexpected remaining release attachments")
        print("RELAY PASS: three complete assets, exact SHA-256, no temporary chunks; still draft", flush=True)


if __name__ == "__main__":
    main()
