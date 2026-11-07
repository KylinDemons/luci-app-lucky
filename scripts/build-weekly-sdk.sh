#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail

cd /builder
finish() {
	status=$?
	trap - EXIT
	mkdir -p /work/output/logs/sdk
	[ ! -d logs ] || cp -a logs/. /work/output/logs/sdk/
	[ ! -f .config ] || cp .config /work/output/logs/sdk.config
	[ ! -f feeds.conf ] || cp feeds.conf /work/output/logs/feeds.conf
	for feed in /builder/feeds/*; do
		if [ -d "$feed/.git" ]; then
			printf '%s\t%s\n' "$(basename "$feed")" "$(git -C "$feed" rev-parse HEAD)"
		fi
	done > /work/output/logs/feed-revisions.tsv
	printf '%s\n' "$status" > /work/output/logs/exit-code
	exit "$status"
}
trap finish EXIT

# The host has already downloaded and checked this one ARM64 source archive.
mapfile -t archives < <(find /work/output/downloads -maxdepth 1 -type f -name 'lucky_*_Linux_arm64_wanji.tar.gz' -print)
test "${#archives[@]}" -eq 1
cp "${archives[0]}" /builder/dl/
git config --global --add safe.directory /work/source
bash /work/automation/scripts/build-sdk.sh /builder /work/source
grep -qx 'CONFIG_TARGET_ARCH_PACKAGES="aarch64_cortex-a53"' .config

mapfile -t packages < <(find /builder/bin/packages -type f \
	\( -name 'lucky_*.ipk' -o -name 'luci-app-lucky_*.ipk' -o -name 'luci-i18n-lucky-zh-cn_*.ipk' \) -print)
test "${#packages[@]}" -eq 3
cp -v "${packages[@]}" /work/output/packages/
