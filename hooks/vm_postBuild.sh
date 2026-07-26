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
# 1. Serial console.
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
# 2. Bring up networking when the hwregd/ipconfigd attach race loses.
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
# 3. /etc/netconfig -- the RPC netconfig database.
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
# 4. Record what this snapshot actually is. Upstream stamps every build with
#    one UTC timestamp shared by the image name, /etc/os-release and
#    nextbsd-version, so this line identifies the exact snapshot the release
#    asset was cut from.
# ---------------------------------------------------------------------------
uname -a
cat /etc/os-release 2>/dev/null || true
/bin/nextbsd-version 2>/dev/null || true
launchctl list 2>/dev/null || true
df -h || true

echo '=================== nextbsd postBuild done ====================='
