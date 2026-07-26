# host_waitForLoginTag.py -- gate the boot on sshd answering, not on console
# text.
#
# Two NextBSD facts make the default waitForText(VM_LOGIN_TAG) wait wrong for
# the FIRST boot of a freshly downloaded image:
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

_nb_osname = env("VM_OS_NAME")
_nb_port = int(read_state(_nb_osname, "sshport") or env("VM_SSH_PORT") or "22")
_nb_max = int(env("VM_LOGIN_MAX_SECONDS") or 600)
_nb_attempts = 2


def _nb_ssh_banner(port):
    """True when something on 127.0.0.1:port answers with an SSH banner."""
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=5)
    except OSError:
        return False
    try:
        s.settimeout(10)
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
