#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Regression checks for latest-version selection and release acceptance."""

import hashlib
import importlib.util
import io
import json
from pathlib import Path
import struct
import tarfile
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("weekly", Path(__file__).with_name("weekly-build.py"))
weekly = importlib.util.module_from_spec(spec)
spec.loader.exec_module(weekly)


def elf():
    binary = bytearray(128)
    binary[:6] = b"\x7fELF\x02\x01"
    struct.pack_into("<H", binary, 18, 183)
    return bytes(binary) + b"packed payload and trailer"


def tar_bytes(files):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for name, content in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            info.mode = 0o755
            archive.addfile(info, io.BytesIO(content))
    return output.getvalue()


def ipk(directory, name, version, architecture, files, depends=""):
    control = f"Package: {name}\nVersion: {version}\nArchitecture: {architecture}\nDepends: {depends}\n"
    path = directory / f"{name}_{version}_{architecture}.ipk"
    path.write_bytes(tar_bytes({
        "./debian-binary": b"2.0\n",
        "./control.tar.gz": tar_bytes({"./control": control.encode()}),
        "./data.tar.gz": tar_bytes(files),
    }))
    return path


class WeeklyTests(unittest.TestCase):
    def site(self, versions=("3.9.9", "3.10.0"), no_wanji=(), incomplete=()):
        base = weekly.BASE_URL
        pages = {base: "".join(f'<a href="./v{v}/">version</a>' for v in versions)
                 + '<a href="./v99.0.0-beta/">beta</a>'
                 + '<a href="https://other.example/v88.0.0/">foreign</a>'}
        for version in versions:
            url = f"{base}v{version}/"
            edition = f"{url}{version}_wanji/"
            filename = f"lucky_{version}_Linux_arm64_wanji.tar.gz"
            pages[url] = "" if version in no_wanji else f'<a href="./{version}_wanji/">Wanji</a>'
            pages[edition] = f'<a href="./{filename}">archive</a>'
            if version not in incomplete:
                pages[edition] += '<a href="./checksums.txt">checksum</a>'
            pages[edition + "checksums.txt"] = "a" * 64 + "  " + filename + "\r\n"
        return pages

    def test_numeric_latest_stable_and_origin(self):
        result = weekly.discover(self.site().__getitem__)
        self.assertEqual(result["version"], "3.10.0")
        self.assertEqual(result["archive_sha256"], "a" * 64)

    def test_selects_latest_version_with_wanji(self):
        pages = self.site(no_wanji=("3.10.0",))
        self.assertEqual(weekly.discover(pages.__getitem__)["version"], "3.9.9")

    def test_incomplete_newest_does_not_publish_an_older_version(self):
        with self.assertRaisesRegex(ValueError, "incomplete"):
            weekly.discover(self.site(incomplete=("3.10.0",)).__getitem__)

    def test_missing_and_ambiguous_checksums_fail(self):
        for text in ("", "x" * 64 + "  lucky.tar.gz", ("a" * 64 + "  lucky.tar.gz\n") * 2):
            with self.subTest(text=text), self.assertRaises(ValueError):
                weekly.checksum_for(text, "lucky.tar.gz")

    def test_corrupt_download_never_becomes_a_cached_archive(self):
        core = {"filename": "lucky.tar.gz", "url": "https://example.org/core",
                "archive_sha256": hashlib.sha256(b"expected").hexdigest()}
        with tempfile.TemporaryDirectory() as temp, patch.object(weekly, "open_url", return_value=io.BytesIO(b"wrong")):
            directory = Path(temp)
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                weekly.download(core, directory)
            self.assertEqual(list(directory.iterdir()), [])

    def test_cached_archive_is_reused_only_when_its_checksum_matches(self):
        core = {"filename": "lucky.tar.gz", "url": "https://example.org/core",
                "archive_sha256": hashlib.sha256(b"expected").hexdigest()}
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            (directory / core["filename"]).write_bytes(b"expected")
            with patch.object(weekly, "open_url", side_effect=AssertionError("unneeded download")):
                self.assertEqual(weekly.download(core, directory).read_bytes(), b"expected")

    def test_patch_original_recipe_and_preserve_binary(self):
        text = "\n".join(f"{name}:={value}" for name, value in {
            "PKG_VERSION": "3.5.0", "PKG_RELEASE": "1", "PKG_SOURCE": "old.tar.gz",
            "PKG_SOURCE_URL": "https://example.org", "PKG_HASH": "skip",
        }.items()) + "\ninclude $(INCLUDE_DIR)/package.mk\n"
        core = {"version": "3.7.1", "archive_sha256": "b" * 64}
        patched = weekly.patch_recipe(text, core)
        self.assertIn("PKG_VERSION:=3.7.1\n", patched)
        self.assertIn("PKG_HASH:=" + "b" * 64 + "\n", patched)
        self.assertIn("_wanji.tar.gz", patched)
        self.assertEqual(patched.count("RSTRIP:=:"), 1)
        self.assertEqual(weekly.patch_recipe(patched, core), patched)
        with self.assertRaisesRegex(ValueError, "PKG_HASH"):
            weekly.patch_recipe(text.replace("PKG_HASH:=skip\n", ""), core)

    def test_archive_rejects_traversal_and_wrong_elf(self):
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / "core.tar.gz"
            archive.write_bytes(tar_bytes({"lucky": elf(), "../outside": b"bad"}))
            with self.assertRaisesRegex(ValueError, "unsafe"):
                weekly.archive_binary(archive)
            binary = bytearray(elf())
            struct.pack_into("<H", binary, 18, 62)
            archive.write_bytes(tar_bytes({"lucky": bytes(binary)}))
            with self.assertRaisesRegex(ValueError, "AArch64"):
                weekly.archive_binary(archive)

    def packages(self, root, changed_core=False, wrong_arch=False, omit_translation=False):
        packages = root / "packages"
        packages.mkdir()
        (root / "logs").mkdir()
        (root / "logs/exit-code").write_text("0\n")
        binary = elf()
        manifest = {
            "core": {"version": "3.7.1", "url": "https://example.org/core.tar.gz",
                     "binary_sha256": hashlib.sha256(binary).hexdigest()},
            "upstream_commit_url": "https://github.com/gdy666/luci-app-lucky/commit/" + "a" * 40,
        }
        (root / "BUILD-INFO.json").write_text(json.dumps(manifest))
        ipk(packages, "lucky", "3.7.1-1", "x86_64" if wrong_arch else weekly.PACKAGE_ARCH, {
            "./usr/bin/lucky": binary + (b"changed" if changed_core else b""),
            "./usr/libexec/lucky-call": b"#!/bin/sh\n",
            "./usr/libexec/lucky-watchdog": b"#!/bin/sh\n",
            "./etc/init.d/lucky": b"#!/bin/sh\n",
            "./etc/config/lucky": b"config lucky\n",
            "./usr/share/lucky/arch": b"arm64\n",
        })
        ipk(packages, "luci-app-lucky", "3.0.0", "all", {
            "./usr/share/luci/menu.d/luci-app-lucky.json": b"{}",
            "./usr/share/rpcd/acl.d/luci-app-lucky.json": b"{}",
            "./usr/libexec/lucky-backup": b"#!/bin/sh\n",
            "./usr/libexec/lucky-diagnostics": b"#!/bin/sh\n",
            "./usr/libexec/lucky-luci-refresh": b"#!/bin/sh\n",
        }, "lucky, rpcd-mod-file")
        if not omit_translation:
            ipk(packages, "luci-i18n-lucky-zh-cn", "123", "all", {
                "./usr/lib/lua/luci/i18n/lucky.zh-cn.lmo": b"translation",
            }, "luci-app-lucky")

    def test_collect_three_complete_packages_with_manifest_checksums(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.packages(root)
            weekly.collect(root)
            release = root / "release"
            expected_packages = {
                "lucky_3.7.1-1_aarch64_cortex-a53.ipk",
                "luci-app-lucky_3.0.0_all.ipk",
                "luci-i18n-lucky-zh-cn_123_all.ipk",
            }
            self.assertEqual({file.name for file in release.iterdir()}, expected_packages | {"SHA256SUMS"})
            checksum_names = []
            for line in (release / "SHA256SUMS").read_text().splitlines():
                digest, name = line.split("  ")
                checksum_names.append(name)
                self.assertEqual(weekly.sha256(release / name), digest)
            self.assertCountEqual(checksum_names, expected_packages)
            notes = (root / "RELEASE-NOTES.txt").read_text(encoding="utf-8")
            self.assertIn("Lucky 3.7.1 万吉版（wanji）", notes)
            self.assertNotIn("逐字节一致", notes)
            self.assertNotIn("BUILD-INFO.json", notes)

    def test_modified_core_wrong_arch_and_missing_translation_block_release(self):
        for options in ({"changed_core": True}, {"wrong_arch": True}, {"omit_translation": True}):
            with self.subTest(options=options), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                self.packages(root, **options)
                with self.assertRaises(ValueError):
                    weekly.collect(root)
                self.assertFalse((root / "release").exists())

    def test_sdk_failure_blocks_release_even_if_all_ipks_exist(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.packages(root)
            (root / "logs/exit-code").write_text("2\n")
            with self.assertRaisesRegex(ValueError, "did not finish"):
                weekly.collect(root)
            self.assertFalse((root / "release").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
