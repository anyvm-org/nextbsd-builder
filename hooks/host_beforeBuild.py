# host_beforeBuild.py -- resolve the rolling upstream image URL.
#
# NextBSD publishes no versioned releases. Every push to main that passes the
# build + boot smoke-test refreshes ONE rolling tag ("continuous"), replacing
# its assets; each asset name carries that build's UTC timestamp, e.g.
# NextBSD-amd64-20260724-211803.img.zip. So a URL written into the conf would
# 404 the next time upstream pushes -- the conf carries the release
# COORDINATES (VM_NEXTBSD_REPO / _TAG / _ASSET_SUFFIX) and this hook turns
# them into the current VM_VHD_LINK.
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
_nb_suffix = env("VM_NEXTBSD_ASSET_SUFFIX")
if not (_nb_repo and _nb_tag and _nb_suffix):
    log("FATAL: VM_NEXTBSD_REPO / VM_NEXTBSD_TAG / VM_NEXTBSD_ASSET_SUFFIX "
        "must all be set by the conf")
    sys.exit(1)

_nb_api = "https://api.github.com/repos/%s/releases/tags/%s" % (_nb_repo, _nb_tag)
log("resolving the current %s asset from %s" % (_nb_suffix, _nb_api))

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

_nb_hits = [a for a in _nb_rel.get("assets", [])
            if a.get("name", "").endswith(_nb_suffix)]
if len(_nb_hits) != 1:
    log("FATAL: expected exactly 1 asset ending in %s on %s@%s, found %d: %s"
        % (_nb_suffix, _nb_repo, _nb_tag, len(_nb_hits),
           [a.get("name") for a in _nb_rel.get("assets", [])]))
    sys.exit(1)

os.environ["VM_VHD_LINK"] = _nb_hits[0]["browser_download_url"]
log("upstream snapshot: %s (%d bytes, updated %s)"
    % (_nb_hits[0]["name"], _nb_hits[0].get("size", -1),
       _nb_hits[0].get("updated_at", "?")))
log("VM_VHD_LINK=%s" % os.environ["VM_VHD_LINK"])
