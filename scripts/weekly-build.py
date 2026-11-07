#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Prepare and verify the weekly OpenWrt 23.05 ARM64 build."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import struct
import subprocess
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = "https://release.66666.host/"
UPSTREAM = "gdy666/luci-app-lucky"
SDK_IMAGE = "ghcr.io/openwrt/sdk@sha256:c68469b1c4891bb2218777f05143a2a6cfa78ef01d936c761638653c0e6814f1"
PACKAGE_ARCH = "aarch64_cortex-a53"
VERSION = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
MAX_ARCHIVE = 256 * 1024 * 1024


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def open_url(url: str):
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "Lucky-weekly-build"})
            return urllib.request.urlopen(request, timeout=60)
        except urllib.error.HTTPError as exc:
            if exc.code < 500 or attempt == 2:
                raise
        except (OSError, urllib.error.URLError):
            if attempt == 2:
                raise
        time.sleep(attempt + 1)
    raise RuntimeError("unreachable")


def fetch_text(url: str) -> str:
    with open_url(url) as response:
        data = response.read(2 * 1024 * 1024 + 1)
    if len(data) > 2 * 1024 * 1024:
        raise ValueError(f"directory/checksum response too large: {url}")
    return data.decode("utf-8")


def links(html: str, base: str) -> set[str]:
    result = set()
    origin = urllib.parse.urlsplit(base)

    class Parser(HTMLParser):
        def handle_starttag(self, tag, attrs):
            if tag != "a":
                return
            href = dict(attrs).get("href", "")
            url = urllib.parse.urljoin(base, href)
            parsed = urllib.parse.urlsplit(url)
            if (parsed.scheme, parsed.netloc) == (origin.scheme, origin.netloc) and not parsed.query and not parsed.fragment:
                result.add(url)

    Parser().feed(html)
    return result


def checksum_for(text: str, filename: str) -> str:
    matches = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[1].removeprefix("*") == filename:
            if not re.fullmatch(r"[0-9a-fA-F]{64}", fields[0]):
                raise ValueError(f"invalid SHA-256 for {filename}")
            matches.append(fields[0].lower())
    if len(matches) != 1:
        raise ValueError(f"expected exactly one checksum for {filename}, found {len(matches)}")
    return matches[0]


def discover(fetch=fetch_text) -> dict:
    versions = set()
    for url in links(fetch(BASE_URL), BASE_URL):
        match = re.fullmatch(re.escape(BASE_URL) + rf"v({VERSION})/", url)
        if match:
            versions.add(match[1])
    for version in sorted(versions, key=lambda value: tuple(map(int, value.split("."))), reverse=True):
        version_url = f"{BASE_URL}v{version}/"
        edition_url = f"{version_url}{version}_wanji/"
        if edition_url not in links(fetch(version_url), version_url):
            continue
        filename = f"lucky_{version}_Linux_arm64_wanji.tar.gz"
        archive_url = edition_url + filename
        checksums_url = edition_url + "checksums.txt"
        entries = links(fetch(edition_url), edition_url)
        if archive_url not in entries or checksums_url not in entries:
            raise ValueError(f"latest Wanji release {version} is incomplete: ARM64 archive/checksums missing")
        return {
            "version": version,
            "edition": "wanji",
            "filename": filename,
            "url": archive_url,
            "checksums_url": checksums_url,
            "archive_sha256": checksum_for(fetch(checksums_url), filename),
        }
    raise ValueError("no stable release with a Wanji edition found")


def download(core: dict, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / core["filename"]
    if target.is_file() and sha256(target) == core["archive_sha256"]:
        return target
    temporary = target.with_suffix(target.suffix + ".part")
    try:
        with open_url(core["url"]) as response, temporary.open("wb") as stream:
            size = 0
            for chunk in iter(lambda: response.read(1024 * 1024), b""):
                size += len(chunk)
                if size > MAX_ARCHIVE:
                    raise ValueError("core archive exceeds 256 MiB")
                stream.write(chunk)
        if sha256(temporary) != core["archive_sha256"]:
            raise ValueError("downloaded core archive checksum mismatch")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def check_elf(binary: bytes) -> None:
    if len(binary) < 64 or binary[:6] != b"\x7fELF\x02\x01" or struct.unpack_from("<H", binary, 18)[0] != 183:
        raise ValueError("expected a Linux ELF64 little-endian AArch64 executable")


def archive_binary(path: Path) -> bytes:
    with tarfile.open(path, "r:gz") as archive:
        candidates = []
        for member in archive.getmembers():
            name = PurePosixPath(member.name)
            if name.is_absolute() or ".." in name.parts or not (member.isfile() or member.isdir()):
                raise ValueError(f"unsafe core archive member: {member.name}")
            if member.size > MAX_ARCHIVE:
                raise ValueError("core archive member exceeds 256 MiB")
            if name.as_posix() == "lucky" and member.isfile():
                candidates.append(member)
        if len(candidates) != 1:
            raise ValueError("expected exactly one root lucky executable")
        binary = archive.extractfile(candidates[0]).read()
    check_elf(binary)
    return binary


def patch_recipe(text: str, core: dict) -> str:
    assignments = {
        "PKG_VERSION": core["version"],
        "PKG_RELEASE": "1",
        "PKG_SOURCE": "lucky_$(PKG_VERSION)_Linux_$(LUCKY_ARCH)_wanji.tar.gz",
        "PKG_SOURCE_URL": BASE_URL + "v$(PKG_VERSION)/$(PKG_VERSION)_wanji",
        "PKG_HASH": core["archive_sha256"],
    }
    for name, value in assignments.items():
        text, count = re.subn(rf"(?m)^{name}[ \t]*:=[^\n]*$", lambda _: f"{name}:={value}", text)
        if count != 1:
            raise ValueError(f"upstream recipe changed: expected one {name} assignment, found {count}")
    if re.search(r"(?m)^RSTRIP[ \t]*[:?+]?=", text):
        text, count = re.subn(r"(?m)^RSTRIP[ \t]*[:?+]?=[^\n]*$", "RSTRIP:=:", text)
        if count != 1:
            raise ValueError("ambiguous upstream RSTRIP definitions")
    else:
        marker = "include $(INCLUDE_DIR)/package.mk"
        if text.count(marker) != 1:
            raise ValueError("upstream package.mk include changed")
        text = text.replace(marker, "# Keep the packed upstream core byte-for-byte intact.\nRSTRIP:=:\n\n" + marker)
    return text


def fingerprint(source: Path) -> str:
    digest = hashlib.sha256()
    for directory in ("lucky", "luci-app-lucky"):
        for file in sorted((source / directory).rglob("*")):
            if file.is_file():
                digest.update(file.relative_to(source).as_posix().encode() + b"\0")
                digest.update(file.read_bytes() + b"\0")
    return digest.hexdigest()


def prepare(source: Path, output: Path) -> None:
    if not (source / "luci-app-lucky/Makefile").is_file():
        raise ValueError("upstream luci-app-lucky/Makefile is missing")
    commit = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("invalid upstream commit")
    core = discover()
    output.mkdir(parents=True, exist_ok=True)
    archive = download(core, output / "downloads")
    core["binary_sha256"] = hashlib.sha256(archive_binary(archive)).hexdigest()
    recipe = source / "lucky/Makefile"
    original = recipe.read_text(encoding="utf-8")
    recipe.write_text(patch_recipe(original, core), encoding="utf-8")
    for name in ("packages", "logs"):
        directory = output / name
        directory.mkdir(exist_ok=True)
        # The official SDK uses UID 1000; GitHub's runner has a different UID.
        directory.chmod(0o777)
    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "upstream_repository": UPSTREAM,
        "upstream_commit": commit,
        "upstream_commit_url": f"https://github.com/{UPSTREAM}/commit/{commit}",
        "prepared_source_sha256": fingerprint(source),
        "automation_commit": os.environ.get("GITHUB_SHA", ""),
        "openwrt_version": "23.05.6",
        "package_architecture": PACKAGE_ARCH,
        "sdk_image": SDK_IMAGE,
        "core": core,
    }
    (output / "BUILD-INFO.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as stream:
            stream.write(f"core_version={core['version']}\nsdk_image={SDK_IMAGE}\n")
    print(f"Prepared {UPSTREAM}@{commit} with Lucky {core['version']} wanji / arm64")


def members(archive) -> dict:
    result = {}
    for item in archive.getmembers():
        path = PurePosixPath(item.name)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"unsafe IPK path: {item.name}")
        name = path.as_posix()
        if name in result:
            raise ValueError(f"duplicate IPK entry: {name}")
        result[name] = item
    return result


def read_member(archive, entries, name: str) -> bytes:
    item = entries[name]
    if not item.isfile():
        raise ValueError(f"expected a regular IPK file: {name}")
    return archive.extractfile(item).read()


def inspect_ipk(path: Path, manifest: dict) -> dict:
    with tarfile.open(path, "r:gz") as outer:
        entries = members(outer)
        if read_member(outer, entries, "debian-binary").strip() != b"2.0":
            raise ValueError("invalid IPK format")
        control_bytes = read_member(outer, entries, "control.tar.gz")
        data_bytes = read_member(outer, entries, "data.tar.gz")
    with tarfile.open(fileobj=io.BytesIO(control_bytes), mode="r:gz") as control:
        text = read_member(control, members(control), "control").decode()
    metadata = dict(line.split(": ", 1) for line in text.splitlines() if ": " in line and not line.startswith(" "))
    name = metadata["Package"]
    with tarfile.open(fileobj=io.BytesIO(data_bytes), mode="r:gz") as data:
        entries = members(data)
        if name == "lucky":
            if metadata["Architecture"] != PACKAGE_ARCH or metadata["Version"] != manifest["core"]["version"] + "-1":
                raise ValueError("core IPK architecture/version mismatch")
            binary = read_member(data, entries, "usr/bin/lucky")
            check_elf(binary)
            if hashlib.sha256(binary).hexdigest() != manifest["core"]["binary_sha256"]:
                raise ValueError("packaged core differs from the verified upstream executable")
            required = ["usr/bin/lucky", "usr/libexec/lucky-call", "usr/libexec/lucky-watchdog", "etc/init.d/lucky"]
            read_member(data, entries, "etc/config/lucky")
            if read_member(data, entries, "usr/share/lucky/arch").strip() != b"arm64":
                raise ValueError("core architecture marker mismatch")
        elif name == "luci-app-lucky":
            dependencies = {part.strip().split()[0] for part in metadata["Depends"].split(",")}
            if metadata["Architecture"] != "all" or "lucky" not in dependencies:
                raise ValueError("LuCI metadata mismatch")
            for file in ("usr/share/luci/menu.d/luci-app-lucky.json", "usr/share/rpcd/acl.d/luci-app-lucky.json"):
                json.loads(read_member(data, entries, file))
            required = ["usr/libexec/lucky-backup", "usr/libexec/lucky-diagnostics", "usr/libexec/lucky-luci-refresh"]
        elif name == "luci-i18n-lucky-zh-cn":
            if metadata["Architecture"] != "all" or "luci-app-lucky" not in metadata["Depends"]:
                raise ValueError("translation metadata mismatch")
            if not read_member(data, entries, "usr/lib/lua/luci/i18n/lucky.zh-cn.lmo"):
                raise ValueError("empty Chinese translation")
            required = []
        else:
            raise ValueError(f"unexpected package: {name}")
        for file in required:
            read_member(data, entries, file)
            if entries[file].mode & 0o111 != 0o111:
                raise ValueError(f"missing executable permissions: {file}")
    return {"file": path.name, "sha256": sha256(path), "size": path.stat().st_size, "control": metadata}


def collect(output: Path) -> None:
    if (output / "logs/exit-code").read_text(encoding="utf-8").strip() != "0":
        raise ValueError("SDK build did not finish successfully")
    manifest = json.loads((output / "BUILD-INFO.json").read_text(encoding="utf-8"))
    paths = sorted((output / "packages").glob("*.ipk"))
    records = [inspect_ipk(path, manifest) for path in paths]
    expected = {"lucky", "luci-app-lucky", "luci-i18n-lucky-zh-cn"}
    if len(records) != 3 or {item["control"]["Package"] for item in records} != expected:
        raise ValueError("expected exactly the core, LuCI app, and zh-cn translation IPKs")
    release = output / "release"
    if release.exists():
        raise ValueError("release output already exists; use a fresh output directory")
    release.mkdir()
    for path in paths:
        shutil.copy2(path, release / path.name)
    manifest["packages"] = records
    revisions = output / "logs/feed-revisions.tsv"
    if revisions.is_file():
        manifest["sdk_feeds"] = dict(line.split("\t", 1) for line in revisions.read_text().splitlines())
    manifest["verification"] = "metadata, required files, permissions, ELF architecture and upstream binary identity passed"
    (release / "BUILD-INFO.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    notes = (
        f"Lucky {manifest['core']['version']} 玩机版（wanji）\n\n"
        "目标：OpenWrt 23.05 / aarch64_cortex-a53；官方 SDK：23.05.6。\n\n"
        f"LuCI / 服务脚本源码：{manifest['upstream_commit_url']}\n"
        f"核心二进制：{manifest['core']['url']}\n\n"
        "包含 Lucky 核心、LuCI 界面和简体中文语言包。核心未做 strip，已核对发布站 SHA-256，"
        "并确认 IPK 内程序与下载的上游程序逐字节一致。未在真实路由器上验收。\n\n"
        "将三个 IPK 上传至路由器 /tmp 后，执行：\n\n"
        "```sh\nopkg install /tmp/lucky_*.ipk /tmp/luci-app-lucky_*.ipk /tmp/luci-i18n-lucky-zh-cn_*.ipk\n```\n\n"
        "若已安装相同版本号的 LuCI 包，可对界面和语言包使用 --force-reinstall。"
        "具体源码提交、SDK 镜像、包版本和校验值见 BUILD-INFO.json / SHA256SUMS。\n"
    )
    (release / "RELEASE-NOTES.txt").write_text(notes, encoding="utf-8")
    checksums = "".join(f"{sha256(file)}  {file.name}\n" for file in sorted(release.iterdir()))
    (release / "SHA256SUMS").write_text(checksums, encoding="utf-8")
    print("Verified and collected:", ", ".join(item["file"] for item in records))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    prepare_parser = subcommands.add_parser("prepare")
    prepare_parser.add_argument("--source", required=True, type=Path)
    prepare_parser.add_argument("--output", required=True, type=Path)
    collect_parser = subcommands.add_parser("collect")
    collect_parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args.source.resolve(), args.output.resolve())
    else:
        collect(args.output.resolve())


if __name__ == "__main__":
    main()
