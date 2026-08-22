

[![Build](https://github.com/anyvm-org/nextbsd-builder/actions/workflows/build.yml/badge.svg)](https://github.com/anyvm-org/nextbsd-builder/actions/workflows/build.yml)

Latest: v2.0.1


The image builder for `nextbsd`


All the supported releases are here:



| Release | x86_64 (amd64) | aarch64 (arm64) |
|---------|---------|---------|
| continuous | ✅ (rsync,scp,nfs,tar) | ✅ (rsync,scp,nfs,tar) |

<!-- arch-label: x86_64 = x86_64 (amd64) -->
<!-- arch-label: aarch64 = aarch64 (arm64) -->
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

How the images are built:

Each image is built automatically in the
[anyvm-org/nextbsd-builder](https://github.com/anyvm-org/nextbsd-builder)
repo's GitHub Actions: it downloads the prebuilt NextBSD disk image
published by the NextBSD (redux) project, boots it in QEMU, enables
ssh, pre-installs the packages listed in the conf, and exports the disk
as a compressed qcow2 image.

Upstream media: the `continuous` prerelease images from
https://github.com/nextbsd-redux/nextbsd/releases (project site:
https://nextbsd.org).




How to build:

1. Use the [manual.yml](.github/workflows/manual.yml) to build manually.
   
    Run the workflow manually, you will get a view-only webconsole from the output of the workflow, just open the link in your web browser.
   
    You will also get an interactive VNC connection port from the output, you can connect to the vm by any vnc client.

2. Run the builder locally on your Ubuntu machine.

    Just clone the repo. and run:
    ```bash
    python3 build.py conf/nextbsd-continuous.conf
    ```
   
