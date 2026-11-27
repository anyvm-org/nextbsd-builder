# host_waitForLoginTag.py -- gate the boot on sshd answering, not on console
# text.
#
# Two NextBSD facts make the default waitForText(VM_LOGIN_TAG) wait wrong for
# the FIRST boot of a freshly installed disk:
#
#   * /boot/loader.conf.d/nextbsd.conf deliberately sets no `console`, so the
#     kernel talks to vidconsole only and the QEMU serial line stays silent.
#     getty (launchd's com.apple.getty) runs on /dev/console, so its "login:"
#     never reaches the serial log either. hooks/vm_postBuild.sh persists
#     /boot/loader.conf.d/anyvm.conf, after which every later boot DOES print
#     to serial -- but by then this stage is long past.
#   * sshd is already running at that point anyway: launchd starts
#     com.openssh.sshd at boot with RunAtLoad + KeepAlive, so a listening
#     port 22 is a stronger readiness signal than a login banner. Everything
#     downstream (enablessh, postBuild, package install) speaks ssh.
#
# So: wait for an SSH banner on the slirp hostfwd port. Whatever the serial
# line does produce is still streamed into the build log via screenText(), so
# a boot that panics before launchd is not invisible.
#
# This hook also owns the boot RETRY. start_and_wait() short-circuits its own
# force-kill-and-reroll loop as soon as a waitForLoginTag hook exists, and a
# reroll is exactly the right recovery here: upstream documents a
# hwregd/ipconfigd attach race (see the NOTE in com.openssh.sshd.plist) that
# on a losing boot leaves em0 with no address and sshd unreachable. A fresh
# boot usually wins it.

#
# The race is frequent on 27.0.0-195: ipconfigd's first DHCP worker sends
# DISCOVERs that are never answered and falls back to a 169.254 address,
# and it does not recover by itself (ten minutes measured). The same disk
# lost it on 4 of 11 measured boots, the builder's own first boot twice in
# a row. A losing boot is therefore cut short and rerolled: the banner
# normally arrives ~50 s into a KVM boot and ~100-160 s into a TCG one, so
# each attempt gets 180 s (360 s under aarch64 TCG), five attempts in all
# -- worst case 15 / 30 minutes, and five losses in a row at the measured
# rate is about a 1% event. Later boots are covered by the image's own
# anyvm-netcheck job (hooks/vm_postBuild.sh), which is not on the disk yet
# on this first boot.

_nb_osname = env("VM_OS_NAME")
_nb_port = int(read_state(_nb_osname, "sshport") or env("VM_SSH_PORT") or "22")
_nb_max = int(env("VM_LOGIN_MAX_SECONDS")
              or (360 if env("VM_ARCH") == "aarch64" else 180))
_nb_attempts = 5


def _nb_ssh_banner(port):
    """True when something on 127.0.0.1:port answers with an SSH banner."""
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=5)
    except OSError:
        return False
    try:
        # 45 s, not 10: this probe also runs on the first boot of the
        # freshly installed disk, before enablessh.txt sets `UseDNS no`,
        # where sshd's reverse lookup can hold the banner back for 15-30 s.
        # The overall wait stays bounded by _nb_max.
        s.settimeout(45)
        return s.recv(64).startswith(b"SSH-")
    except OSError:
        return False
    finally:
        s.close()


def _nb_wait_ssh(port, seconds):
    _deadline = time.time() + seconds
    while time.time() < _deadline:
        # Print whatever the guest has put on the serial line since the last
        # poll (nothing on boot 1, the whole boot from boot 2 on).
        screenText()
        if _nb_ssh_banner(port):
            return True
        time.sleep(5)
    return False


_nb_up = False
for _nb_try in range(1, _nb_attempts + 1):
    log("waiting up to %d s for sshd on 127.0.0.1:%d (attempt %d/%d)"
        % (_nb_max, _nb_port, _nb_try, _nb_attempts))
    if _nb_wait_ssh(_nb_port, _nb_max):
        _nb_up = True
        break
    if _nb_try < _nb_attempts:
        log("no sshd within %d s; force-killing for a fresh boot "
            "(hwregd/ipconfigd attach race?)" % _nb_max)
        closeConsole()
        destroyVM()
        if startVM() != 0:
            log("FATAL: startVM failed while retrying the boot")
            sys.exit(1)
        time.sleep(2)
        openConsole()

if not _nb_up:
    log("FATAL: %s never brought sshd up on 127.0.0.1:%d"
        % (_nb_osname, _nb_port))
    screenText()
    sys.exit(1)

log("sshd is answering; boot complete")
time.sleep(5)
