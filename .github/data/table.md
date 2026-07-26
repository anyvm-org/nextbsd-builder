

| Release      | x86_64 (amd64) |
|--------------|----------------|
| continuous   |  ✅ (rsync,scp,nfs)  |

NextBSD publishes no versioned releases: upstream refreshes a single rolling
`continuous` tag on every push to main. Each builder release tag freezes one
of those snapshots -- `nextbsd-version` and `/etc/os-release` inside the image
name the exact upstream build it was cut from.

`nfs` works only because `hooks/vm_postBuild.sh` installs `/etc/netconfig`.
The kernel's NFS client, `/sbin/mount_nfs` and `/usr/sbin/rpcbind` are all in
the image, but the curated `/etc` overlay omits the RPC netid table, so every
mount used to fail with `tcp: Netconfig database not found`.

No `sshfs`: the NEXTBSD kernel is built `NO_MODULES` and the image ships no
`kldload` (Darwin's `kextload` replaces it and only loads kext bundles), so
`fusefs` cannot be loaded and there is no `/dev/fuse`.
