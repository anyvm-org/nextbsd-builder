# In-guest configuration, piped into the guest's /bin/sh over ssh right
# after enablessh. build.py reboots the VM once this hook returns, so
# everything written here is in effect for the rest of the build and for
# every anyvm runtime boot of the published image.
#
# NOTE: there is no rc.conf / rc.d / sysrc / service(8) on NextBSD -- launchd
# is PID 1 and services are .plist files. Nothing from freebsd-builder's
# postBuild carries over.

echo '=================== nextbsd postBuild start ===================='

# ---------------------------------------------------------------------------
# 1. Grow the root filesystem into the disk.
#
# createVMFromVHD() does `qemu-img resize +200G`, but NOTHING inside this
# guest ever grows the partition or the filesystem to match: there is no
# rc.d, so no growfs_enable, and upstream's image has no first-boot resize of
# its own. The root therefore stays at the size makefs gave it -- and
# upstream keeps shrinking that: the 20260724 snapshot had a 5.3G root with
# 1.3G free, the 20260726 one has 3.1G with 1.4G free.
#
# That tight filesystem is what broke the first CI build: `pkg install` died
# with "Not enough space in /var/cache/pkg, needed 2008 KiB available -227
# MiB" and sqlite failed the catalogue index with "database or disk is full",
# both while df reported 1.4G free and 460k free inodes (a 300 MB write and
# 2000 file creates both succeeded). pkg's own space accounting goes negative
# on this filesystem; rather than chase that, give it real room.
#
# After this the root is ~197G with 180G free and the whole package stage
# just works. It also fixes the user-visible half of the bug: without it an
# anyvm user gets a 3.1G root no matter how big the disk is.
#
# gpart recover first: growing the backing device leaves the GPT backup
# header stranded at the old end of the disk, and gpart refuses to resize
# until that is repaired. Disk name and partition index are derived, not
# hardcoded, so a VM_DISK change does not silently break this.
# ---------------------------------------------------------------------------
_rootdisk=$(gpart show | awk '$1 == "=>" { d = $4 } /freebsd-ufs/ { print d; exit }')
_rootidx=$(gpart show | awk '/freebsd-ufs/ { print $3; exit }')
if [ -n "$_rootdisk" ] && [ -n "$_rootidx" ]; then
	echo "growing ${_rootdisk}p${_rootidx} (freebsd-ufs) into the whole disk"
	gpart recover "$_rootdisk" || echo "gpart recover failed (already intact?)"
	gpart resize -i "$_rootidx" "$_rootdisk" || echo "gpart resize failed"
	growfs -y /dev/ufs/ROOTFS >/dev/null 2>&1 || echo "growfs failed"
	df -h /
else
	echo "WARNING: could not locate the freebsd-ufs partition; not growing"
	gpart show || true
fi

# ---------------------------------------------------------------------------
# 2. Serial console.
#
# The stock /boot/loader.conf.d/nextbsd.conf deliberately leaves `console`
# unset ("the UEFI/BIOS default consoles work for laptops and VMs"), so the
# kernel and getty only ever talk to the framebuffer and the QEMU serial line
# stays empty -- upstream's own CI works around this by typing the same
# settings at the loader OK prompt on every boot (tests/img-boot-test.sh).
# Persist them instead: build.py's console-build mode reads the serial line as
# the build transcript, and an anyvm user gets `--serial` output for free.
#
# Dual console (not comconsole alone) keeps the framebuffer alive too, so the
# VNC web console still shows the boot and a login prompt. launchd's
# com.apple.getty runs getty on /dev/console, which the kernel multiplexes
# across both when boot_multicons is set, so one getty serves both.
#
# comconsole_speed matches upstream's CI value; QEMU's emulated 16550 ignores
# the divisor either way (getty runs the 3wire.9600 class on /dev/console).
# ---------------------------------------------------------------------------
mkdir -p /boot/loader.conf.d
cat > /boot/loader.conf.d/anyvm.conf <<'ANYVMLOADER'
# Written by anyvm-org/nextbsd-builder (hooks/vm_postBuild.sh).
# Layered on top of nextbsd.conf: the loader reads every
# /boot/loader.conf.d/*.conf, so this clobbers nothing upstream ships.
console="comconsole,vidconsole"
boot_multicons="YES"
boot_serial="YES"
comconsole_speed="115200"
autoboot_delay="0"
ANYVMLOADER
cat /boot/loader.conf.d/anyvm.conf

# ---------------------------------------------------------------------------
# 3. Bring up networking when the hwregd/ipconfigd attach race loses.
#
# Upstream documents the failure in com.openssh.sshd.plist: "On boots where
# the hwregd/ipconfigd attach race loses, em0 has no inet and sshd is
# unreachable". For an anyvm image that is fatal -- ssh IS the interface --
# so a one-shot launchd job forces the interface up before anything else
# needs it.
#
# Measured on the first snapshot built here (20260724-211803): 3 of 9 boots
# came up with no address at all, so this is not a theoretical race.
#
# It never assigns a static address: the guest address and gateway differ
# between the builder's slirp (192.168.122.254 via .1) and anyvm's
# (192.168.122.10 via .2), so anything hardcoded here would be wrong at
# runtime. It only re-drives DHCP -- see the escalation comment inside.
# ---------------------------------------------------------------------------
cat > /usr/libexec/anyvm-netcheck <<'ANYVMNET'
#!/bin/sh
# Bring up networking when the hwregd/ipconfigd attach race loses.
#
# A plain `ipconfig set <if> DHCP` retry loop recovered 6 of the 9 measured
# boots (every success landed on round 3-4, i.e. 6-8 s in, so the kick does
# work) but never rescued the other 3 within 60 s. The escalation below adds
# the two remedies that do not depend on the racing path at all:
#   rounds 0-4    ifconfig <if> up + ipconfig set <if> DHCP   (normal path)
#   round 5, 20,  ifconfig down/up + ipconfig set NONE then DHCP
#     35, ...     (full IPConfiguration reset on a re-attached link)
#   round 8, 23,  dhclient <if>, backgrounded -- FreeBSD's own DHCP client
#     38, ...     is in this base system (/sbin/dhclient) and does NOT go
#                 through ipconfigd/hwregd, so it works even when that
#                 attach lost
# and it polls for 240 s rather than 60 s: both the builder and anyvm.py wait
# up to 600 s for sshd, so a late recovery is still a win.
#
# dhclient is backgrounded on purpose -- it retries in the foreground when no
# server answers, and this loop must keep polling. One shot at boot, always
# exits 0: it must never wedge launchd.
#
# ipconfig(8) here is Darwin's IPConfiguration CLI (NOT FreeBSD's ifconfig).

_ifs() {
	for _i in $(ifconfig -l 2>/dev/null); do
		case "$_i" in lo*|pflog*|pfsync*) continue ;; esac
		echo "$_i"
	done
}

_try=0
while [ "$_try" -lt 120 ]; do
	for _if in $(_ifs); do
		if ifconfig "$_if" 2>/dev/null | grep -q 'inet '; then
			echo "anyvm-netcheck: $_if has an address after ${_try} rounds"
			exit 0
		fi
	done

	_phase=$((_try % 15))
	for _if in $(_ifs); do
		if [ "$_try" -ge 8 ] && [ "$_phase" = 8 ] && [ -x /sbin/dhclient ]; then
			echo "anyvm-netcheck: round ${_try}: dhclient $_if"
			/sbin/dhclient "$_if" >/dev/null 2>&1 &
		elif [ "$_try" -ge 5 ] && [ "$_phase" = 5 ]; then
			echo "anyvm-netcheck: round ${_try}: re-attaching $_if"
			ifconfig "$_if" down 2>/dev/null
			sleep 1
			ifconfig "$_if" up 2>/dev/null
			ipconfig set "$_if" NONE 2>/dev/null
			ipconfig set "$_if" DHCP 2>/dev/null
		else
			ifconfig "$_if" up 2>/dev/null
			ipconfig set "$_if" DHCP 2>/dev/null
		fi
	done

	_try=$((_try + 1))
	sleep 2
done
echo "anyvm-netcheck: no interface got an address after ${_try} rounds; giving up"
exit 0
ANYVMNET
chmod 0555 /usr/libexec/anyvm-netcheck

cat > /System/Library/LaunchDaemons/org.anyvm.netcheck.plist <<'ANYVMPLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>org.anyvm.netcheck</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/libexec/anyvm-netcheck</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/var/log/anyvm-netcheck.log</string>
    <key>StandardErrorPath</key>
    <string>/var/log/anyvm-netcheck.log</string>
    <key>LimitLoadToSessionType</key>
    <string>System</string>
</dict>
</plist>
ANYVMPLIST

# No KeepAlive on purpose: this is a one-shot boot fixup, not a daemon.

# ---------------------------------------------------------------------------
# 4. /etc/netconfig -- the RPC netconfig database.
#
# Without it EVERY NFS mount fails at the first step with
#     tcp: Netconfig database not found
# because libc's TI-RPC code cannot map the "tcp" netid to a transport. The
# NFS client itself is present and fine (the kernel exports vfs.nfs.*), and
# /sbin/mount_nfs + /usr/sbin/rpcbind are both in the image -- the curated
# /etc overlay just does not carry this one file, so --sync nfs was dead on
# arrival. Verified: adding it turns a 5-attempt mount failure into
# `192.168.122.2:/ on /mnt/host (nfs)`.
#
# Content is copied VERBATIM from FreeBSD releng/15.0
# lib/libc/rpc/netconfig (the branch this userland is built from); it is a
# fixed table, not something to write from memory. Harmless if upstream ever
# starts shipping it -- the content is identical.
# ---------------------------------------------------------------------------
if [ ! -f /etc/netconfig ]; then
	cat > /etc/netconfig <<'ANYVMNETCONFIG'
#
# The network configuration file. This file is currently only used in
# conjunction with the (TI-) RPC code in the C library, unlike its
# use in SVR4.
#
# Entries consist of:
#
#       <network_id> <semantics> <flags> <protofamily> <protoname> \
#               <device> <nametoaddr_libs>
#
# The <device> and <nametoaddr_libs> fields are always empty in FreeBSD.
#
udp6       tpi_clts      v     inet6    udp     -       -
tcp6       tpi_cots_ord  v     inet6    tcp     -       -
udp        tpi_clts      v     inet     udp     -       -
tcp        tpi_cots_ord  v     inet     tcp     -       -
rawip      tpi_raw       -     inet      -      -       -
local      tpi_cots_ord  -     loopback  -      -       -
ANYVMNETCONFIG
	chmod 0644 /etc/netconfig
	echo "installed /etc/netconfig (RPC netid table; NFS needs it)"
else
	echo "/etc/netconfig already present; leaving it alone"
fi

# ---------------------------------------------------------------------------
# 5. FreeBSD ports packages for a plain `pkg install`.
#
# Since 2026-10-31 / 11-01 (nextbsd-redux/nextbsd 37a4295d + f535efef)
# upstream ships ONLY its own repository, /private/etc/pkg/NextBSD.conf: the
# NextBSD-* meta packages and pkg itself -- no rsync, no tree, no socat. And
# NextBSD's pkg now computes the ABI NextBSD:27:<arch>, so it refuses FreeBSD
# packages even from a configured FreeBSD repository ("wrong architecture:
# FreeBSD:15:amd64 instead of NextBSD:27:amd64"). That broke this builder's
# package stage and every `pkg install -y socat` in a nextbsd-vm prepare.
#
# NextBSD's pkg keeps that door open on purpose. nextbsd-contrib
# src/pkg/patches/0005-NextBSD-ABI.patch: "An explicit ABI=FreeBSD:15:<arch>
# (with OSVERSION, or IGNORE_OSVERSION=yes) never goes through the detection
# and works exactly as before, so a user can still select FreeBSD packages."
# Upstream's wiki page Installing-Packages
# (https://github.com/nextbsd-redux/nextbsd/wiki/Installing-Packages) gives
# the recipe, and the files below follow it to the byte where they can: the
# signing key under /usr/local/etc/pkg/fingerprints/FreeBSD (the image has
# no /usr/share/keys/pkg; the key is verbatim FreeBSD releng/15.0
# share/keys/pkg/trusted/pkg.freebsd.org.2013102301), a repository named
# FreeBSD in /usr/local/etc/pkg/repos/FreeBSD.conf, ABI FreeBSD:15:<arch>
# with OSVERSION 1501000 (releng/15.1's __FreeBSD_version, which patch 0005
# names too) and IGNORE_OSVERSION.
#
# It departs from that page in two places, DELIBERATELY, because the wiki
# makes FreeBSD ports opt-in per command (`ABI=... pkg install -r FreeBSD
# <pkg>`) and an anyvm image has to keep a plain `pkg install -y <pkg>`
# working -- the build's VM_INSTALL_CMD and every nextbsd-vm prepare script
# depend on it, and it is what the image shipped until upstream's change:
#
#   * The ABI is pinned in /private/etc/pkg.conf (where NextBSD's pkg reads
#     its config, patch 0002). The wiki warns against exactly this: it
#     "makes pkg treat the whole system as FreeBSD", and `pkg upgrade` stops
#     updating the OS. That is the intended trade-off here -- an anyvm guest
#     is a snapshot pinned by this builder's release tag, never upgraded in
#     place.
#   * So the FreeBSD repository is enabled and the NextBSD one DISABLED (not
#     removed): with the ABI pinned, NextBSD's NextBSD:27 catalogue is
#     rejected, and one repository failing to update fails every pkg command
#     ("Error updating repositories!", exit 3). The override sits in
#     /usr/local/etc/pkg/repos/, which pkg reads after /private/etc/pkg/, so
#     upstream's own NextBSD.conf stays byte-identical.
#
# To get NextBSD's own setup back: delete the ABI and OSVERSION lines from
# /private/etc/pkg.conf and the `NextBSD: { enabled: no }` line here, and
# set the FreeBSD repository to `enabled: no` as the wiki has it.
#
# NextBSD's other pkg rules still hold and need nothing from us: pkg itself
# only ever comes from the repository named NextBSD (patch 0001), and a
# foreign package may only install under /usr/local/ and /var/ (patch 0004),
# which is where every FreeBSD port goes. Verified on 27.0.0 (165): rsync,
# tree and socat install from it and run, `pkg check -d -a` is clean.
# ---------------------------------------------------------------------------
_fbsd_abi="FreeBSD:15:$(uname -p)"
mkdir -p /usr/local/etc/pkg/repos /usr/local/etc/pkg/fingerprints/FreeBSD/trusted
cat > /usr/local/etc/pkg/fingerprints/FreeBSD/trusted/pkg.freebsd.org.2013102301 <<'ANYVMPKGKEY'
function: "sha256"
fingerprint: "b0170035af3acc5f3f3ae1859dc717101b4e6c1d0a794ad554928ca0cbb2f438"
ANYVMPKGKEY
cat > /usr/local/etc/pkg/repos/FreeBSD.conf <<ANYVMPKGREPO
# Written by anyvm-org/nextbsd-builder (hooks/vm_postBuild.sh), after
# upstream's wiki page Installing-Packages -- except that this repository is
# enabled and NextBSD's is disabled, because the ABI is pinned in
# /private/etc/pkg.conf. See the comment in that hook for why and how to undo.
FreeBSD: {
  url: "pkg+https://pkg.FreeBSD.org/${_fbsd_abi}/latest",
  mirror_type: "srv",
  signature_type: "fingerprints",
  fingerprints: "/usr/local/etc/pkg/fingerprints/FreeBSD",
  enabled: yes
}
NextBSD: { enabled: no }
ANYVMPKGREPO
if ! grep -q '^ABI' /private/etc/pkg.conf 2>/dev/null; then
	cat >> /private/etc/pkg.conf <<ANYVMPKGCONF
# Added by anyvm-org/nextbsd-builder (hooks/vm_postBuild.sh): FreeBSD ports
# are this image's package source. OSVERSION only stops pkg printing
# "Setting ABI requires setting OSVERSION" on every run.
ABI = "${_fbsd_abi}";
OSVERSION = 1501000;
ANYVMPKGCONF
fi
grep -q '^IGNORE_OSVERSION' /private/etc/pkg.conf ||
	echo "IGNORE_OSVERSION = yes;" >> /private/etc/pkg.conf
cat /private/etc/pkg.conf
pkg config ABI

# ---------------------------------------------------------------------------
# 6. Record what this snapshot actually is. Upstream stamps every build with
#    one <version> (<build>) pair, from SystemVersion.plist, shared by the
#    image name, /etc/os-release and nextbsd-version, so this line identifies
#    the exact snapshot the release asset was cut from.
# ---------------------------------------------------------------------------
uname -a
cat /etc/os-release 2>/dev/null || true
/bin/nextbsd-version 2>/dev/null || true
launchctl list 2>/dev/null || true
df -h || true

echo '=================== nextbsd postBuild done ====================='
