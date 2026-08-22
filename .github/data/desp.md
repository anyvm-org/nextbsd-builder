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
