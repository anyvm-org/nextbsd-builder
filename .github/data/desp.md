How the images are built:

Each image is built automatically in the
[anyvm-org/nextbsd-builder](https://github.com/anyvm-org/nextbsd-builder)
repo's GitHub Actions: it downloads the NextBSD live ISO, boots it in
QEMU, runs upstream's own installer engine onto a blank disk over the
serial console, boots the installed system, enables ssh, pre-installs the
packages listed in the conf, and exports the disk as a compressed qcow2
image.

Upstream media: the `continuous-server` and `continuous-desktop`
prerelease ISOs from https://github.com/nextbsd/nextbsd/releases (project
site: https://nextbsd.org).
