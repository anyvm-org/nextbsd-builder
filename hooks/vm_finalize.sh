# Last in-guest stage before the VM is shut down and exported. Runs after
# VM_PRE_INSTALL_PKGS / VM_EXTRA_SCRIPT, so it covers whatever they fetched.
#
# The UFS root is only makefs'd with ~1.5 GB of headroom over the content, so
# the fetched package archives are a large fraction of the free space -- drop
# them. No zero-fill pass: there is not enough free space for one to be worth
# it, and exportOVA's `qemu-img convert -S 4k` already re-sparsifies whatever
# the guest never wrote.

echo "=== finalize: image cleanup ==="

pkg clean -ay || true

# Boot-time noise from the build itself; the published image should start
# with empty logs.
rm -f /var/log/sshd.stderr /var/log/anyvm-netcheck.log 2>/dev/null || true

df -h || true
echo "=== finalize: image cleanup done ==="

: > ~/.sh_history
