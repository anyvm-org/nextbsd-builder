# Last in-guest stage before the VM is shut down and exported. Runs after
# VM_PRE_INSTALL_PKGS / VM_EXTRA_SCRIPT, so it covers whatever they fetched.
#
# The root is the ZFS pool upstream's installer creates (pool NextBSD, see
# hooks/host_installOpts.py). Drop the fetched package archives, then TRIM
# the pool: build.py attaches the disk with discard=unmap, so the blocks ZFS
# freed (the package cache, the install's scratch data) become holes in the
# qcow2 and exportOVA's `qemu-img convert -S 4k` leaves them out of the
# published image. No zero-fill pass: on ZFS with lz4 a file of zeros is
# compressed away and never reaches the disk, so it would free nothing.

echo "=== finalize: image cleanup ==="

pkg clean -ay || true

# Boot-time noise from the build itself; the published image should start
# with empty logs.
rm -f /var/log/sshd.stderr /var/log/anyvm-netcheck.log 2>/dev/null || true

sync
# Bounded: build.py runs this hook over ssh with no timeout of its own, so
# an unbounded `zpool trim -w` that stopped progressing would hang the
# build. Start the trim, poll its status (in progress = "% trimmed,
# started"), and cancel it after 20 minutes -- a partial trim only leaves a
# bigger image. On this pool (~1 GB used) it takes seconds.
if zpool trim NextBSD; then
	_trim_s=0
	while zpool status -t NextBSD | grep -q '% trimmed, started'; do
		if [ "$_trim_s" -ge 1200 ]; then
			echo "zpool trim still running after ${_trim_s} s; cancelling (not fatal)"
			zpool trim -c NextBSD || true
			break
		fi
		sleep 5
		_trim_s=$((_trim_s + 5))
	done
	echo "zpool trim: done after ~${_trim_s} s"
	zpool status -t NextBSD | grep -i trim || true
else
	echo "zpool trim failed to start (not fatal)"
fi
zpool list NextBSD || true
df -h || true
echo "=== finalize: image cleanup done ==="

: > ~/.sh_history
