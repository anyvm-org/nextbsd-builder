# host_beforeBuild.py -- resolve the rolling upstream image URL.
#
# NextBSD publishes no versioned releases. Every push to main that passes the
# build + boot smoke-test refreshes ONE rolling tag ("continuous"), replacing
# its assets; each asset name carries that build's UTC timestamp, e.g.
# NextBSD-amd64-20260724-211803.img.zip. So a URL written into the conf would
# 404 the next time upstream pushes -- the conf carries the release
# COORDINATES (VM_NEXTBSD_REPO / _TAG / _ASSET_PREFIX / _ASSET_SUFFIX) and this
# hook turns them into the current VM_VHD_LINK.
#
# The PREFIX is what selects the architecture, and it is not optional: since
# 2026-08-14 the same rolling tag carries amd64 AND arm64 assets, plus an
# .iso.zip next to each .img.zip. Matching on the suffix alone found 2 hits
# and killed every build (run 31869194909). Each arch conf names its own
# prefix ("NextBSD-amd64-" / "NextBSD-arm64-") rather than deriving one from
# VM_ARCH here, because that mapping is upstream's naming, i.e. data, and it
# belongs next to the other coordinates in the conf.
#
# Prefix + suffix alone is still not enough, and that broke the arm64 build on
# 2026-08-25: upstream added NextBSD-arm64-rpi5-20260825-134541.img.zip (a
# Raspberry Pi 5 image), which starts with "NextBSD-arm64-" and ends with
# ".img.zip" exactly like the virt image does, so the match found 2 again.
# What separates them is that the generic image carries NOTHING between the
# arch and the timestamp, so the match is anchored on that timestamp: upstream
# stamps every build with one UTC <YYYYMMDD>-<HHMMSS> shared by the image
# name, /etc/os-release and nextbsd-version (observed 20260724-211803,
# 20260726-002924, 20260814-222643, 20260825-134538/-134547). Any future
# board-specific or flavoured variant gains a token there and is excluded by
# construction, rather than by a list of names to skip that grows every time
# upstream invents one.
#
# beforeBuild is the right hook point: it runs before setup() and long before
# _prep_vhd_disk() reads VM_VHD_LINK, and exec()'ing into build.py's globals
# means simply assigning os.environ here is enough.
#
# Integrity: no separate .sha256 check. _prep_vhd_disk unpacks the archive
# with zipfile, which verifies each member's CRC-32 while streaming it, so a
# truncated or corrupted download raises BadZipFile and takes the branch's
# single re-download-and-retry path rather than silently producing a bad
# qcow2.

_nb_repo = env("VM_NEXTBSD_REPO")
_nb_tag = env("VM_NEXTBSD_TAG")
_nb_prefix = env("VM_NEXTBSD_ASSET_PREFIX")
_nb_suffix = env("VM_NEXTBSD_ASSET_SUFFIX")
if not (_nb_repo and _nb_tag and _nb_prefix and _nb_suffix):
    log("FATAL: VM_NEXTBSD_REPO / VM_NEXTBSD_TAG / VM_NEXTBSD_ASSET_PREFIX / "
        "VM_NEXTBSD_ASSET_SUFFIX must all be set by the conf")
    sys.exit(1)

_nb_api = "https://api.github.com/repos/%s/releases/tags/%s" % (_nb_repo, _nb_tag)
log("resolving the current %s*%s asset from %s"
    % (_nb_prefix, _nb_suffix, _nb_api))

_nb_req = urllib.request.Request(_nb_api, headers={
    "Accept": "application/vnd.github+json",
    "User-Agent": "anyvm-nextbsd-builder",
})
# Anonymous API calls are rate-limited to 60/hour per IP. GitHub Actions
# exports GITHUB_TOKEN to the job, so use it when present and fall back to
# anonymous for local builds (one call per build is well inside the limit).
_nb_token = env("GITHUB_TOKEN")
if _nb_token:
    _nb_req.add_header("Authorization", "Bearer %s" % _nb_token)

try:
    with urllib.request.urlopen(_nb_req, timeout=60) as _nb_resp:
        _nb_rel = json.loads(_nb_resp.read().decode("utf-8"))
except Exception as _nb_err:
    log("FATAL: cannot read the %s release of %s: %s"
        % (_nb_tag, _nb_repo, _nb_err))
    sys.exit(1)

# <prefix><YYYYMMDD>-<HHMMSS><suffix>, with nothing else in between.
_nb_asset_re = re.compile(r"^%s\d{8}-\d{6}%s$"
                          % (re.escape(_nb_prefix), re.escape(_nb_suffix)))
_nb_hits = [a for a in _nb_rel.get("assets", [])
            if _nb_asset_re.match(a.get("name", ""))]
if len(_nb_hits) != 1:
    log("FATAL: expected exactly 1 asset named %s<timestamp>%s on %s@%s, "
        "found %d: %s"
        % (_nb_prefix, _nb_suffix, _nb_repo, _nb_tag, len(_nb_hits),
           [a.get("name") for a in _nb_rel.get("assets", [])]))
    sys.exit(1)

os.environ["VM_VHD_LINK"] = _nb_hits[0]["browser_download_url"]
log("upstream snapshot: %s (%d bytes, updated %s)"
    % (_nb_hits[0]["name"], _nb_hits[0].get("size", -1),
       _nb_hits[0].get("updated_at", "?")))
log("VM_VHD_LINK=%s" % os.environ["VM_VHD_LINK"])
