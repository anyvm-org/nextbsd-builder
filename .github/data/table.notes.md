<!-- arch-label: x86_64 = x86_64 (amd64) -->
<!-- arch-label: aarch64 = aarch64 (arm64) -->
<!-- desktop-table: continuous-desktop -->
<!-- desktop-table: continuous-desktop-aarch64 -->
<!-- desktop-header: NextBSD Desktop Preview images (the graphical session runs on the VNC / web console): -->
NextBSD publishes no versioned releases: upstream refreshes two rolling
prerelease tags, `continuous-server` (Server Preview, release `continuous`
here) and `continuous-desktop` (Desktop Preview, release
`continuous-desktop`). Each builder release tag freezes one of those
snapshots -- `nextbsd-version` and `/etc/os-release` inside the image name
the exact upstream build it was cut from.

Both are installed from upstream's live ISO by upstream's own installer
engine (`hooks/host_installOpts.py`), so the root is the ZFS pool `NextBSD`
the installer creates, spanning the whole disk.

No `sshfs`: the NEXTBSD kernel is built `NO_MODULES` and the image ships no
`kldload` (Darwin's `kextload` replaces it and only loads kext bundles), so
`fusefs` cannot be loaded and there is no `/dev/fuse`.
