# host_enablessh.py -- install the host pubkey over ssh, not over the console.
#
# Neither of build.py's two built-in enable-ssh paths fits NextBSD:
#
#   * _enable_ssh_console_branch() types `root` at a login prompt and then
#     runs inputFile(), which in console-build mode makes the guest fetch the
#     script with `nc 192.168.122.1 64342 | sh`. Nothing reaches the serial
#     line on the first boot (the image's loader.conf sets no console -- see
#     hooks/host_waitForLoginTag.py), and nc(1) is not in the curated base.
#   * _enable_ssh_root_branch() drives sshpass with VM_ROOT_PASSWORD, but
#     this guest wants no credential at all.
#
# The shipped image's /etc/pam.d/sshd is a pam_permit stack and its
# sshd_config has `PermitRootLogin yes` + `KbdInteractiveAuthentication yes`,
# so `ssh root@127.0.0.1` succeeds with no key, no password and -- important
# here -- no prompt. That makes it safe to hand the whole of enablessh.local
# to the remote /bin/sh on stdin: nothing else is competing for it.
#
# Auth is proved FIRST with stdin closed. If NextBSD ever tightens that PAM
# stack, the probe fails cleanly instead of feeding the enable-ssh script
# into a password prompt one line at a time.

_nb_port = str(read_state(env("VM_OS_NAME"), "sshport")
               or env("VM_SSH_PORT") or "22")
_nb_ssh = ["ssh", "-p", _nb_port,
           "-o", "StrictHostKeyChecking=no",
           "-o", "UserKnownHostsFile=/dev/null",
           "-o", "PubkeyAuthentication=no",
           "-o", "ConnectTimeout=30",
           "root@127.0.0.1"]

log("probing credential-free root ssh on 127.0.0.1:%s" % _nb_port)
try:
    _nb_probe = subprocess.run(_nb_ssh + ["echo", "anyvm-ssh-ok"],
                               stdin=DEVNULL, capture_output=True, timeout=180)
    _nb_out, _nb_err = _nb_probe.stdout, _nb_probe.stderr
except subprocess.TimeoutExpired as _nb_to:
    # A hung ssh means the guest answered the TCP handshake but never
    # finished authenticating -- report it as the auth failure it is
    # rather than letting the exception unwind out of the exec()'d hook.
    _nb_out, _nb_err = _nb_to.stdout or b"", (_nb_to.stderr or b"") + b"\n[ssh timed out after 180 s]"
if b"anyvm-ssh-ok" not in _nb_out:
    log("FATAL: root ssh into the guest did not work without a credential")
    log("stdout: %s" % _nb_out.decode("utf-8", "replace"))
    log("stderr: %s" % _nb_err.decode("utf-8", "replace"))
    sys.exit(1)
log("credential-free root ssh works; feeding enablessh.local")

with open("enablessh.local", "rb") as _nb_inp:
    _nb_rc = subprocess.run(_nb_ssh + ["sh"], stdin=_nb_inp).returncode
if _nb_rc != 0:
    log("FATAL: enablessh.local failed in the guest (exit %d)" % _nb_rc)
    sys.exit(1)

# sshd was HUP'd at the end of enablessh.txt; give the re-exec a moment
# before main() starts using the key it just installed.
time.sleep(10)
log("check ssh access with the installed key:")
subprocess.call(
    ["ssh", "-p", _nb_port, "-o", "StrictHostKeyChecking=no",
     "-o", "UserKnownHostsFile=/dev/null", "-o", "PasswordAuthentication=no",
     "-o", "KbdInteractiveAuthentication=no",
     "root@127.0.0.1", "pwd; id"])
log("ssh OK")
