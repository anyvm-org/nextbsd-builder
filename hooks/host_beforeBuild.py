# host_beforeBuild.py -- resolve the rolling upstream ISO and make sure the
# firmware it boots under is on the host.
#
# NextBSD publishes no versioned releases. Builds are started by hand upstream
# and each one REPLACES the assets of a rolling prerelease tag; every asset
# name carries that build's stamp (NextBSD-Server-amd64-27.0.0-192.iso), so a
# URL written into the conf would 404 after the next upstream build. The conf
# carries the release COORDINATES (VM_NEXTBSD_REPO / _TAG / _ASSET_PREFIX /
# _ASSET_SUFFIX) and this hook turns them into the current VM_ISO_LINK.
#
# Upstream layout since 2026-11-22..25 (nextbsd/nextbsd #679, #682, #684):
#   * the repo moved from nextbsd-redux/nextbsd to nextbsd/nextbsd (the old
#     name still redirects, but the conf names the real one);
#   * the single `continuous` tag is GONE (404 since then -- that is what the
#     watcher reported in issue #5). It was split into `continuous-server`
#     (Server Preview) and `continuous-desktop` (Desktop Preview);
#   * amd64 and arm64 ship ONLY live ISOs now. The .img.zip disk image this
#     builder used to boot directly no longer exists (only the Raspberry Pi 5
#     image is still an .img.zip), so every image is now INSTALLED from the
#     ISO -- see hooks/host_installOpts.py.
#
# Matching: <prefix><stamp><suffix> with nothing else in between, the stamp
# being <version>-<build> (27.0.0-192; older builds used a <YYYYMMDD>-<HHMMSS>
# datestamp, which the same pattern takes). Anchoring on the stamp is what
# keeps a board or flavour variant out by construction: an rpi5 image carries
# a token between the arch and the stamp and can never match an arch prefix.
# The prefix names the variant and the arch together
# ("NextBSD-Server-amd64-"), because that mapping is upstream's naming, i.e.
# data, and it belongs next to the other coordinates in the conf. Exactly one
# hit is required: a second one is a naming change to look at, not something
# to pick from.
#
# beforeBuild is the right hook point: it runs before setup() and long before
# the ISO is downloaded, and exec()'ing into build.py's globals means simply
# assigning os.environ here is enough.

_nb_repo = env("VM_NEXTBSD_REPO")
_nb_tag = env("VM_NEXTBSD_TAG")
_nb_prefix = env("VM_NEXTBSD_ASSET_PREFIX")
_nb_suffix = env("VM_NEXTBSD_ASSET_SUFFIX")
if not (_nb_repo and _nb_tag and _nb_prefix and _nb_suffix):
    log("FATAL: VM_NEXTBSD_REPO / VM_NEXTBSD_TAG / VM_NEXTBSD_ASSET_PREFIX / "
        "VM_NEXTBSD_ASSET_SUFFIX must all be set by the conf")
    sys.exit(1)

_nb_api = "https://api.github.com/repos/%s/releases/tags/%s" % (_nb_repo, _nb_tag)
log("resolving the current %s<stamp>%s asset from %s"
    % (_nb_prefix, _nb_suffix, _nb_api))

_nb_req = urllib.request.Request(_nb_api, headers={
    "Accept": "application/vnd.github+json",
    "User-Agent": "anyvm-nextbsd-builder",
})
# Anonymous API calls share a 60/hour quota per IP with every other job on
# the runner. GitHub Actions exports GITHUB_TOKEN to the job; local builds
# fall back to anonymous (one call per build).
_nb_token = env("GITHUB_TOKEN")
# A token with whitespace or control bytes would make http.client raise an
# error that quotes the header value repr()-escaped -- which no plain
# replace() of the raw token would catch -- so such a token is refused
# up front, without printing it.
if _nb_token and not re.fullmatch(r"[\x21-\x7e]+", _nb_token):
    log("FATAL: GITHUB_TOKEN contains whitespace or control characters "
        "(value not shown); fix or unset it")
    sys.exit(1)
if _nb_token:
    # Unredirected: urllib copies ordinary headers onto a redirect, to any
    # origin; the token is meant for api.github.com only. (A renamed repo
    # answers with a same-origin redirect, which then simply goes anonymous.)
    _nb_req.add_unredirected_header("Authorization", "Bearer %s" % _nb_token)


def _nb_scrub(_text):
    """Error text with any bearer credential cut out, raw or escaped."""
    _text = str(_text)
    if _nb_token:
        _text = _text.replace(_nb_token, "<GITHUB_TOKEN>")
    return re.sub(r"(?i)bearer\s+[^\s'\"]+", "Bearer <GITHUB_TOKEN>", _text)


try:
    with urllib.request.urlopen(_nb_req, timeout=60) as _nb_resp:
        _nb_rel = json.loads(_nb_resp.read().decode("utf-8"))
except urllib.error.HTTPError as _nb_err:
    log("FATAL: cannot read the %s release of %s: HTTP %s %s"
        % (_nb_tag, _nb_repo, _nb_err.code, _nb_scrub(_nb_err.reason)))
    sys.exit(1)
except Exception as _nb_err:
    log("FATAL: cannot read the %s release of %s: %s: %s"
        % (_nb_tag, _nb_repo, type(_nb_err).__name__, _nb_scrub(_nb_err)))
    sys.exit(1)
# The token is not needed again; do not leave it in build.py's globals.
del _nb_token, _nb_req

_nb_assets = _nb_rel.get("assets") if isinstance(_nb_rel, dict) else None
if not isinstance(_nb_assets, list):
    log("FATAL: the %s release of %s has no asset list" % (_nb_tag, _nb_repo))
    sys.exit(1)
# fullmatch, not ^...$: `$` also matches before a trailing newline.
_nb_asset_re = re.compile(r"%s\d+(?:\.\d+)*-\d+%s"
                          % (re.escape(_nb_prefix), re.escape(_nb_suffix)))
_nb_hits = [a for a in _nb_assets if isinstance(a, dict)
            and isinstance(a.get("name"), str)
            and _nb_asset_re.fullmatch(a["name"])]
if len(_nb_hits) != 1:
    log("FATAL: expected exactly 1 asset named %s<version>-<build>%s on %s@%s, "
        "found %d: %s"
        % (_nb_prefix, _nb_suffix, _nb_repo, _nb_tag, len(_nb_hits),
           [a.get("name") for a in _nb_assets if isinstance(a, dict)]))
    sys.exit(1)
_nb_asset = _nb_hits[0]

# The download URL must be the plain release-download URL of exactly this
# asset -- nothing the API could hand back is passed on unchecked.
_nb_url = _nb_asset.get("browser_download_url")
_nb_url_want = "https://github.com/%s/releases/download/%s/%s" % (
    _nb_repo, _nb_tag, _nb_asset["name"])
if _nb_url != _nb_url_want:
    log("FATAL: asset %s has download URL %r, expected %s"
        % (_nb_asset["name"], _nb_url, _nb_url_want))
    sys.exit(1)
_nb_size = _nb_asset.get("size")
_nb_digest = str(_nb_asset.get("digest") or "")
_nb_sha = _nb_digest[len("sha256:"):] if _nb_digest.startswith("sha256:") else ""
if not (type(_nb_size) is int and _nb_size > 0
        and re.fullmatch(r"[0-9a-f]{64}", _nb_sha)):
    log("FATAL: asset %s has no usable size / sha256 digest (size=%r, "
        "digest=%r)" % (_nb_asset["name"], _nb_size, _nb_digest))
    sys.exit(1)

os.environ["VM_ISO_LINK"] = _nb_url
log("upstream build: %s (%d bytes, sha256 %s, updated %s)"
    % (_nb_asset["name"], _nb_size, _nb_sha,
       _nb_asset.get("updated_at", "?")))
log("VM_ISO_LINK=%s" % _nb_url)


def _nb_file_sha256(_path):
    _h = hashlib.sha256()
    with open(_path, "rb") as _fh:
        for _blk in iter(lambda: _fh.read(1 << 20), b""):
            _h.update(_blk)
    return _h.hexdigest()


# The ISO is downloaded and verified HERE, before createVM() boots it.
# createVM() skips its own download when build/<os>.iso exists, and only a
# SUCCESSFUL build removes that file again. All four confs share
# VM_OS_NAME (nextbsd), so the ISO a failed Server build leaves behind -- or
# a partial download, which build.py preallocates to full length -- would
# otherwise be booted by the next build. So: keep a pre-placed file only
# when its sha256 is the digest the API publishes for the asset just
# resolved; otherwise download to a .part file, check its digest, and only
# then move it into place. Nothing unverified is ever booted.
_nb_iso = wf("%s.iso" % env("VM_OS_NAME"))
if os.path.exists(_nb_iso):
    _nb_have = _nb_file_sha256(_nb_iso)
    if _nb_have == _nb_sha:
        log("nextbsd beforeBuild: reusing %s (sha256 matches the asset)"
            % _nb_iso)
    else:
        log("nextbsd beforeBuild: %s has sha256 %s, not the asset's; "
            "removing it" % (_nb_iso, _nb_have))
        os.remove(_nb_iso)
if not os.path.exists(_nb_iso):
    _nb_part = _nb_iso + ".part"
    download(_nb_url, _nb_part)
    _nb_have = (_nb_file_sha256(_nb_part) if os.path.exists(_nb_part)
                else "<no file>")
    if _nb_have != _nb_sha:
        log("FATAL: downloaded %s has sha256 %s, the asset's digest is %s"
            % (_nb_asset["name"], _nb_have, _nb_sha))
        try:
            os.remove(_nb_part)
        except OSError:
            pass
        sys.exit(1)
    os.replace(_nb_part, _nb_iso)
    log("nextbsd beforeBuild: %s downloaded and verified (sha256 %s)"
        % (_nb_iso, _nb_sha))


# --- UEFI firmware for amd64 -------------------------------------------------
# The INSTALL needs UEFI: only under OVMF is the live ISO's loader on the
# serial line (its efi console is the firmware's ConOut), which is what lets
# host_installOpts.py stop the countdown and set the serial console at all.
# The installed disk itself boots either way (GPT with both an ESP fallback
# loader and gptzfsboot in a freebsd-boot partition; checked on 27.0.0-192),
# so anyvm.py needs no UEFI for it. The amd64 confs point VM_BIOS at the
# combined OVMF image. ubuntu-24.04 ships /usr/share/ovmf/OVMF.fd in the ovmf
# package itself; on ubuntu-26.04 ovmf is a metapackage and the same path
# comes from ovmf-generic. Installing "ovmf" covers both. aarch64 needs
# nothing here: build.py already boots it under its own EDK2 search.
_nb_bios = env("VM_BIOS")
if _nb_bios and not os.path.exists(_nb_bios):
    log("nextbsd beforeBuild: %s missing, installing ovmf" % _nb_bios)
    _nb_env = dict(os.environ, DEBIAN_FRONTEND="noninteractive")
    for _nb_apt in (["sudo", "-E", "apt-get", "update", "-qq"],
                    ["sudo", "-E", "apt-get", "install", "-y", "-qq", "ovmf"]):
        try:
            subprocess.run(_nb_apt, env=_nb_env, timeout=900)
        except subprocess.TimeoutExpired:
            log("FATAL: %s did not finish within 900 s" % " ".join(_nb_apt))
            sys.exit(1)
    if not os.path.exists(_nb_bios):
        log("FATAL: %s still missing after installing ovmf" % _nb_bios)
        sys.exit(1)
if _nb_bios:
    log("nextbsd beforeBuild: UEFI firmware %s (%d bytes)"
        % (_nb_bios, os.path.getsize(_nb_bios)))
