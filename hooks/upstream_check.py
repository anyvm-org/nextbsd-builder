#!/usr/bin/env python3
# Watch the NextBSD releases page. Prints "continuous" -- the one and only
# thing this builder's VM_RELEASE can ever be -- when everything upstream
# still looks the way the confs expect. Empty output means "nothing
# detected" and is not an error; a non-zero exit means detection found
# something a human has to deal with, and must be reported by the caller,
# never swallowed. A failure must NEVER print a plausible-but-wrong version.
#
# Source of truth: the GitHub releases API for nextbsd-redux/nextbsd, i.e.
# the machine-readable form of
# https://github.com/nextbsd-redux/nextbsd/releases
#
# WHY THIS HOOK IS SHAPED DIFFERENTLY FROM EVERY OTHER ONE
#
# NextBSD publishes no versions. There is ONE mutable `continuous`
# prerelease tag, refreshed on every push to main, and the build timestamp
# lives in the asset NAME (NextBSD-amd64-20260814-222643.img.zip), not in a
# tag. Every conf here therefore carries the literal VM_RELEASE=continuous
# forever, so watch.py's version comparison has nothing to compare: this
# hook reports "continuous", decide() finds a conf for it and correctly
# concludes "already has a conf, nothing to do". No conf will ever be
# auto-landed for this builder, and that is the right outcome -- there is
# no new release to land.
#
# What CAN change on that page, and what this hook actually watches, is the
# ASSET SET. Both ways it can move have already bitten, on the same
# upstream push (2026-08-14):
#
#   * A NEW ARCH appeared -- NextBSD-arm64-*.img.zip alongside amd64.
#     Nothing noticed. The arm64 image went unbuilt until a human happened
#     to open the releases page.
#   * THE MATCH STOPPED RESOLVING. hooks/host_beforeBuild.py picks the
#     download by matching asset names; when it matched on the ".img.zip"
#     suffix alone, the new arm64 asset made that match find 2 candidates
#     and EVERY amd64 build died with "expected exactly 1 asset" -- the
#     v2.0.1 release build included.
#
# So the hook re-runs host_beforeBuild.py's match for every conf and fails
# if any conf stops resolving to exactly one asset, and fails if upstream
# publishes an image asset that no conf claims. Those are precisely the two
# events that break or under-build this builder, and neither is visible in
# a version string.
#
# It deliberately does NOT treat a new SNAPSHOT as an event. Upstream
# refreshes the tag on every push to main; the confs resolve the download
# at BUILD time, so a new snapshot needs no file change here. The only
# response would be "cut a new builder release", which is a human's call,
# and routing it through this hook would mean failing the watcher on
# purpose -- opening the "upstream watcher: nextbsd-builder" issue, which
# says the watcher is BROKEN and, while it stays open, suppresses the
# report of a real breakage.
#
# No gendata.natural_key import, unlike every other hook in the fleet:
# that import exists so a hook orders CANDIDATE VERSIONS the same way the
# engine does. This hook has no candidates to order -- there is exactly one
# possible answer -- so importing it would only add a failure mode.
#
# stdlib only (urllib.request, json, os, re, sys) -- no external
# dependencies.

import json
import os
import re
import sys
import urllib.request

CONF_DIR = "conf"
CONF_RE = re.compile(r"^nextbsd-.*\.conf$")
# The build timestamp that separates a generic image from a board-specific
# one. Mirrors hooks/host_beforeBuild.py, deliberately: this hook's whole
# job is to report a change BEFORE the next build hits it, which it can
# only do by resolving the download exactly the way that hook does.
TIMESTAMP = r"\d{8}-\d{6}"
# Image assets this builder will never build, with the reason. Anything not
# listed here that no conf claims is reported, so a genuinely new arch still
# raises the alarm.
#
# rpi5: a Raspberry Pi 5 (BCM2712) image. QEMU has no machine for it -- the
# 8.2.2 that ubuntu-24.04 installs in CI stops at raspi3b (`qemu-system-
# aarch64 -M help` lists raspi0/1ap/2b/3ap/3b and nothing newer), and anyvm
# boots this guest on `virt` anyway. Revisit if the runners ever ship a QEMU
# with a raspi5 machine.
IGNORE_RE = re.compile(r"^NextBSD-arm64-rpi5-")
ASSIGN_RE = re.compile(
    r'^\s*(VM_NEXTBSD_REPO|VM_NEXTBSD_TAG|VM_NEXTBSD_ASSET_PREFIX'
    r'|VM_NEXTBSD_ASSET_SUFFIX)=(.*)$')
API = "https://api.github.com/repos/%s/releases/tags/%s"
TIMEOUT = 60
USER_AGENT = "anyvm-org-upstream-watcher/1.0"


def strip_quotes(value):
    value = value.strip()
    # Drop a trailing comment only when the value is quoted -- an unquoted
    # value cannot contain '#' in these keys anyway, and a URL legitimately
    # can elsewhere.
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value.split("#", 1)[0].strip()


def read_confs():
    """The (repo, tag, prefix, suffix) coordinates each conf downloads by.

    Read with a regex rather than by sourcing: this hook only needs four
    literal assignments, and sourcing a conf would run whatever else it
    contains.
    """
    coords = {}
    for name in sorted(os.listdir(CONF_DIR)):
        if not CONF_RE.match(name) or name == "all.release.conf":
            continue
        found = {}
        with open(os.path.join(CONF_DIR, name), "r", encoding="utf-8") as f:
            for line in f:
                m = ASSIGN_RE.match(line.rstrip("\n"))
                if m:
                    found[m.group(1)] = strip_quotes(m.group(2))
        if found:
            coords[name] = found
    return coords


def fetch(url):
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.github+json",
    }
    # Unauthenticated API calls are rate-limited to 60/hour per IP, which
    # hosted runners share. Use a token when the workflow already exports
    # one; its absence is not an error (one call per day is well inside
    # the anonymous limit).
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = "Bearer %s" % token
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read().decode("utf-8", "replace")


def main():
    if not os.path.isdir(CONF_DIR):
        sys.stderr.write("upstream_check: no %s/ here; run from the builder "
                         "repo root\n" % CONF_DIR)
        return 1

    coords = read_confs()
    if not coords:
        sys.stderr.write(
            "upstream_check: no conf declares VM_NEXTBSD_REPO / _TAG / "
            "_ASSET_PREFIX / _ASSET_SUFFIX. Either conf/ is empty or the "
            "conf keys were renamed without updating this hook and "
            "hooks/host_beforeBuild.py\n")
        return 1

    incomplete = sorted(n for n, c in coords.items() if len(c) != 4)
    if incomplete:
        sys.stderr.write(
            "upstream_check: conf(s) missing one of VM_NEXTBSD_REPO / _TAG / "
            "_ASSET_PREFIX / _ASSET_SUFFIX, so the download cannot be "
            "resolved: %s\n" % ", ".join(incomplete))
        return 1

    # One fetch per distinct (repo, tag): every conf points at the same
    # rolling tag today, and a second one would be a deliberate change.
    releases = {}
    for name in sorted(coords):
        key = (coords[name]["VM_NEXTBSD_REPO"], coords[name]["VM_NEXTBSD_TAG"])
        if key in releases:
            continue
        url = API % key
        try:
            body = fetch(url)
        except Exception as e:
            sys.stderr.write("upstream_check: fetch of %s failed: %s\n"
                             % (url, e))
            return 1
        try:
            rel = json.loads(body)
        except ValueError as e:
            sys.stderr.write("upstream_check: %s did not return JSON: %s\n"
                             % (url, e))
            return 1
        if not isinstance(rel, dict) or "assets" not in rel:
            sys.stderr.write(
                "upstream_check: %s returned no assets field; the API shape "
                "or the release itself may have changed\n" % url)
            return 1
        names = [a.get("name") or "" for a in rel.get("assets") or []
                 if isinstance(a, dict)]
        if not names:
            sys.stderr.write(
                "upstream_check: %s@%s carries no assets at all. Upstream "
                "may be mid-publish, or the rolling tag was recreated\n"
                % key)
            return 1
        releases[key] = names

    # 1. Every conf must still resolve to exactly ONE asset, which is the
    #    invariant host_beforeBuild.py enforces at build time. Checking it
    #    here means the watcher reports it the night it breaks instead of
    #    the next build doing so -- which, for a tag with no versions, is
    #    as close to "a new release broke us" as this builder can get.
    problems = []
    claimed = {}
    for name in sorted(coords):
        c = coords[name]
        key = (c["VM_NEXTBSD_REPO"], c["VM_NEXTBSD_TAG"])
        asset_re = re.compile(
            "^%s%s%s$" % (re.escape(c["VM_NEXTBSD_ASSET_PREFIX"]), TIMESTAMP,
                          re.escape(c["VM_NEXTBSD_ASSET_SUFFIX"])))
        hits = [a for a in releases[key] if asset_re.match(a)]
        claimed.setdefault(key, set()).update(hits)
        if len(hits) != 1:
            problems.append(
                "%s: expected exactly 1 asset named %s<timestamp>%s on "
                "%s@%s, found %d%s"
                % (name, c["VM_NEXTBSD_ASSET_PREFIX"],
                   c["VM_NEXTBSD_ASSET_SUFFIX"], key[0], key[1],
                   len(hits), (" (%s)" % ", ".join(hits)) if hits else ""))

    # 2. Any image asset no conf claims is an arch (or a variant) upstream
    #    publishes and this builder does not build. "Image asset" is
    #    defined by the suffixes the confs themselves use, so the .iso.zip
    #    and .sha256 assets are correctly ignored rather than pattern-
    #    matched against a hardcoded arch list.
    for key in sorted(releases):
        suffixes = set(c["VM_NEXTBSD_ASSET_SUFFIX"] for c in coords.values()
                       if (c["VM_NEXTBSD_REPO"], c["VM_NEXTBSD_TAG"]) == key)
        unclaimed = sorted(a for a in releases[key]
                           if any(a.endswith(s) for s in suffixes)
                           and a not in claimed.get(key, set())
                           and not IGNORE_RE.match(a))
        if unclaimed:
            problems.append(
                "%s@%s publishes image asset(s) no conf builds: %s. If this "
                "is a new architecture, add conf/nextbsd-continuous-<arch>."
                "conf with its VM_NEXTBSD_ASSET_PREFIX and VM_ARCH (arm64 "
                "was added this way on 2026-08-15)"
                % (key[0], key[1], ", ".join(unclaimed)))

    if problems:
        sys.stderr.write("upstream_check: the NextBSD releases page changed "
                         "in a way this builder cannot absorb by itself:\n")
        for p in problems:
            sys.stderr.write("  %s\n" % p)
        return 1

    print("continuous")
    return 0


if __name__ == "__main__":
    sys.exit(main())
