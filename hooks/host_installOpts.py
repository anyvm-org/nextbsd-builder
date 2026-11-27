# host_installOpts.py -- install NextBSD from its live ISO, over the serial
# console.
#
# Since 2026-11-25 upstream ships only live ISOs for amd64 and arm64 (see
# hooks/host_beforeBuild.py), so the disk this builder exports is now the
# result of upstream's own installer engine, driven exactly the way
# upstream's CI drives it (nextbsd/nextbsd tests/install-test.sh +
# install-run.exp.inc, harness nextbsd/nextbsd-ci):
#
#   (The ISO was downloaded and checked against the API's sha256 digest by
#   hooks/host_beforeBuild.py before createVM() booted it.)
#   1. Loader. The ISO's loader.conf sets no console, so the kernel would
#      talk to the framebuffer only. Under UEFI the LOADER itself is on the
#      serial line (its efi console is the firmware's ConOut), so the
#      countdown is stopped from here and the serial console is set for
#      this one boot with the variables upstream's loader.exp.inc sets
#      (boot_serial=YES, comconsole_speed, boot_multicons, then on amd64
#      console=comconsole), then `boot`. Each loader command is typed with
#      per-character echo checks -- see _nb_loader_cmd() for why. The arm64
#      EFI loader has no comconsole ("console comconsole is unavailable";
#      its consoles are efi and eficom) and its efi console already is the
#      serial line under QEMU virt, so the console is left alone there.
#   2. Live system. The Server ISO AUTO-LOGS IN as `admin` (zsh) on the
#      console. Upstream's Desktop Preview (nextbsd/nextbsd #684) turns the
#      console autologin off, so a `login:` prompt is answered with `admin`
#      (and an empty password if one is asked). admin is in group admin,
#      which sudoers grants ALL -- `sudo` works without a prompt (pam
#      accepts the empty password), only `sudo -n` refuses.
#   3. Install. probe-disks.sh lists the candidate disks as
#        DISK <dev> <size> <unit> <kind> <is-install-medium 0/1> <bytes>
#      and do-install.sh -d <dev> runs the whole install non-interactively
#      (GPT: freebsd-boot + efi + one freebsd-zfs partition, pool NextBSD,
#      boot environment NextBSD/ROOT/default; UEFI loader plus BIOS
#      gptzfsboot). The engine wants a clean environment and no stdin,
#      hence `env -i PATH=... </dev/null`, as upstream runs it. It needs a
#      disk of at least 30,000,000,000 bytes; build.py's default 200G
#      qcow2 is fine.
#   4. Power off. QEMU must exit on its own (shutdown -p, then halt -p);
#      a VM that will not power off is fatal here, so main() never boots a
#      disk whose pool was not cleanly exported.
#
# Nothing else is configured here: the installed system keeps upstream's
# sshd_config (PermitRootLogin yes) and its pam_permit sshd stack, so the
# credential-free root ssh that hooks/host_enablessh.py relies on works on
# the installed disk exactly as it did on the old prebuilt image (checked on
# 27.0.0-192, Server amd64 + arm64). Everything an anyvm image needs on top
# is done over ssh in enablessh.txt and hooks/vm_postBuild.sh.
#
# Every wait is bounded and fatal. Every match runs on the serial log from a
# saved RAW byte offset onwards, with ANSI escapes stripped after slicing:
# zsh colours its prompt (escapes sit between "admin" and "@"), the
# installer prints enough lines that a marker can leave waitForText()'s
# 50-line window between two polls, and an offset into the stripped text
# would shift when an escape split across two reads is completed.
#
# Measured on 27.0.0-192 (local WSL2): loader prompt ~20 s, live admin
# prompt ~10-20 s KVM / ~75 s TCG, install ~150 s KVM / ~20 min TCG.

_nb_osname = env("VM_OS_NAME")
_nb_log_path = serial_log(_nb_osname)
# Any CSI sequence (OVMF also sends private-parameter ones like ESC[=3h),
# charset selects, and the single-byte ESC = / ESC > / ESC c (the loader
# resets the terminal with ESC c).
_nb_ansi = re.compile(r"\x1b\[[0-9:;<=>?]*[ -/]*[@-~]|\x1b[()][A-Za-z0-9]"
                      r"|\x1b[=>c]")
_nb_arm = env("VM_ARCH") == "aarch64"

_NB_LOADER_MAX = 300
_NB_LIVE_MAX = int(env("VM_LIVE_MAX_SECONDS") or 1800)
_NB_INSTALL_MAX = int(env("VM_INSTALL_MAX_SECONDS") or 7200)
_NB_PROMPT_RE = r"admin@\S+ \S+ [%#$] *$"
# The loader's prompt is "OK " and it is a prompt only at the very END of
# the output so far: an "OK" line in the middle of boot output is not one,
# and requiring the trailing space means the next command's echo cannot
# start with a late-arriving blank.
_NB_OK_RE = r"\nOK +\Z"


def _nb_raw():
    try:
        with open(_nb_log_path, "rb") as _fh:
            return _fh.read()
    except OSError:
        return b""


def _nb_pos():
    """Current end of the serial log, as a raw byte offset."""
    return len(_nb_raw())


def _nb_text(since=0):
    """Serial log from raw byte offset `since` on, ANSI escapes and CRs
    stripped."""
    _raw = _nb_raw()[since:].decode("utf-8", "replace")
    return _nb_ansi.sub("", _raw).replace("\r", "")


def _nb_show():
    _fresh = _unseen_screen_text("")
    if _fresh.strip():
        log(_fresh.rstrip("\n"))


def _nb_fatal(msg):
    _nb_show()
    log("FATAL: nextbsd installOpts: " + msg)
    destroyVM()
    sys.exit(1)


def _nb_wait(pattern, max_seconds, what, since=0, poll=2):
    """Wait until regex `pattern` matches the serial text after raw offset
    `since`. Fatal on the bound or on the VM going away."""
    _rx = re.compile(pattern, re.M | re.S)
    _t0 = time.time()
    _last_show = 0
    while True:
        # Read first, check the bound after: the last read is taken at (or
        # after) the deadline, so output that arrived during the final sleep
        # still counts.
        _m = _rx.search(_nb_text(since))
        if _m:
            _nb_show()
            log("nextbsd installOpts: %s after %d s" % (what, time.time() - _t0))
            return _m
        if isRunning() != 0:
            _nb_show()
            log("FATAL: nextbsd installOpts: the VM stopped while waiting for "
                "%s" % what)
            sys.exit(1)
        if time.time() - _t0 >= max_seconds:
            break
        if time.time() - _last_show >= 20:
            _nb_show()
            _last_show = time.time()
        time.sleep(max(0, min(poll, max_seconds - (time.time() - _t0))))
    _nb_fatal("no %s within %d s" % (what, max_seconds))


def _nb_type(line):
    _send_console(line + "\r")


def _nb_until(cond, seconds, step=0.1):
    """Poll cond() for up to `seconds`; True as soon as it holds."""
    _t0 = time.time()
    while time.time() - _t0 < seconds:
        if cond():
            return True
        time.sleep(step)
    return False


_NB_LOADER_ERR = re.compile(r"wrong number|not found|unknown|invalid|"
                            r"error|usage|unavailable|no valid console", re.I)


def _nb_kill_line():
    """Empty the loader's input line: ^U is the kill-line key of libsa's
    ngets(), and the loader answers it with a newline."""
    for _k in range(4):
        _mark = _nb_pos()
        _send_console("\x15")
        if _nb_until(lambda: "\n" in _nb_text(_mark), 3):
            return
    _nb_fatal("the loader never answered ^U (kill line)")


def _nb_loader_cmd(cmd, expect_ok=True, tries=4):
    """Type one loader command a character at a time and submit it only when
    the loader echoed back exactly that line; then check its answer.

    Once `console=comconsole` is set the amd64 loader reads the UART
    directly while OVMF's own serial terminal driver keeps polling the same
    UART, so received bytes are split between the two at random:
    27.0.0-192 once turned `set boot_serial=YES` into `set boot_ser=YES`
    (and another run of the same build typed every command cleanly). A byte
    the loader never got is never echoed, so it is sent again; a byte that
    stays lost, or an echo that comes late and makes a resend a duplicate,
    leaves an echoed line that differs from `cmd` -- that line is killed
    (^U) and the whole command typed again. Only an exact echo is submitted.

    Enter is checked the same way (the loader answers it with a newline at
    once). With expect_ok, the answer up to the next OK prompt must carry
    no error text, else the command is retyped (`set` is idempotent). For
    `boot` (expect_ok=False) success needs positive evidence: no new OK
    prompt within 20 s AND kernel output after the Enter."""
    for _try in range(1, tries + 1):
        _start = _nb_pos()
        _lost = False
        _resent = False
        for _ch in cmd:
            for _resend in range(6):
                _mark = _nb_pos()
                _send_console(_ch)
                if _nb_until(lambda: _ch in _nb_text(_mark), 2):
                    break
                _resent = True
                log("nextbsd installOpts: no echo for %r in %r; resending"
                    % (_ch, cmd))
            else:
                _lost = True
                break
        if _resent:
            # A resend is a duplicate if the first copy's echo was only
            # late. Give such an echo time to land before the line is
            # compared, or the duplicate would be submitted unseen.
            time.sleep(4)
        _echo = _nb_text(_start).replace("\x00", "")
        if _lost or _echo != cmd:
            log("nextbsd installOpts: echo %r is not %r (try %d/%d); killing "
                "the line" % (_echo, cmd, _try, tries))
            _nb_kill_line()
            continue
        _mark = _nb_pos()
        for _enter in range(3):
            _send_console("\r")
            if _nb_until(lambda: "\n" in _nb_text(_mark), 5, 0.2):
                break
            log("nextbsd installOpts: no newline after Enter for %r; "
                "resending Enter" % cmd)
        else:
            _nb_fatal("the loader never took Enter after %r" % cmd)
        if expect_ok:
            _m = _nb_wait(_NB_OK_RE, 30, "OK after %r" % cmd, since=_mark)
            _answer = _nb_text(_mark)[:_m.start()]
            if not _NB_LOADER_ERR.search(_answer):
                return
        else:
            # Success = boot output (40+ non-blank characters) with no OK
            # prompt at its end; a returned OK prompt = the loader refused,
            # retype. Neither within 120 s is fatal -- never type into a
            # kernel that might be booting slowly.
            def _nb_boot_state():
                _after = _nb_text(_mark)
                if re.search(_NB_OK_RE, _after):
                    return "ok-prompt"
                if len("".join(_after.split())) >= 40:
                    # Give a refusal time to show its OK prompt.
                    time.sleep(3)
                    if not re.search(_NB_OK_RE, _nb_text(_mark)):
                        return "booting"
                    return "ok-prompt"
                return None
            _state = [None]

            def _nb_boot_seen():
                _state[0] = _nb_boot_state()
                return _state[0] is not None
            if not _nb_until(_nb_boot_seen, 120, 1):
                _nb_fatal("neither boot output nor an OK prompt within 120 s "
                          "after %r" % cmd)
            if _state[0] == "booting":
                return
        _nb_show()
        log("nextbsd installOpts: loader did not take %r (try %d/%d); "
            "retyping" % (cmd, _try, tries))
    _nb_fatal("the loader did not take %r after %d tries" % (cmd, tries))


# --- 1. loader ---------------------------------------------------------------
# Space stops the countdown; any key would, but space is harmless if it lands
# in the firmware first. The spaces are echoed after the OK prompt, so an
# empty line is entered to get a clean one before the first `set`.
log("nextbsd installOpts: stopping the loader countdown on the serial line")
_nb_t0 = time.time()
while "Type '?' for a list of commands" not in _nb_text():
    if time.time() - _nb_t0 > _NB_LOADER_MAX:
        _nb_fatal("the loader never offered its OK prompt on the serial line "
                  "within %d s (is the firmware UEFI? amd64 needs "
                  "VM_BIOS=OVMF)" % _NB_LOADER_MAX)
    if isRunning() != 0:
        log("FATAL: nextbsd installOpts: the VM stopped before the loader")
        sys.exit(1)
    _send_console(" ")
    time.sleep(1)
time.sleep(1)
_nb_mark = _nb_pos()
_nb_type("")
_nb_wait(_NB_OK_RE, 30, "loader OK prompt", since=_nb_mark)
# console=comconsole goes LAST: until it is set the loader reads the
# firmware's efi console, which takes input reliably, so only `boot` has to
# cross the lossy direct-UART path (see _nb_loader_cmd).
_nb_cmds = ["set boot_serial=YES", "set comconsole_speed=115200",
            "set boot_multicons=YES"]
if not _nb_arm:
    _nb_cmds.append("set console=comconsole")
for _nb_cmd in _nb_cmds:
    _nb_loader_cmd(_nb_cmd)
_nb_mark = _nb_pos()
_nb_loader_cmd("boot", expect_ok=False)

# --- 2. live system ----------------------------------------------------------
# Server: straight to the admin prompt. Desktop: login: -> admin, and an
# empty password if one is asked. The admin prompt is checked FIRST, and a
# login:/Password: prompt counts only as a whole word at the very end of
# the output -- so neither an "...autologin:" line nor a login prompt that
# has already been answered makes this type `admin` again. At most three
# login rounds, inside the one live-system bound.
_nb_prompt_rx = re.compile(_NB_PROMPT_RE, re.M)
_nb_login_rx = re.compile(r"(?<![\w-])login: *\Z")
_nb_pass_rx = re.compile(r"(?<![\w-])Password: *\Z")
_nb_t0 = time.time()
_nb_logins = 0
while True:
    _nb_wait(r"(%s)|(?<![\w-])login: *\Z|(?<![\w-])Password: *\Z"
             % _NB_PROMPT_RE,
             max(1, _NB_LIVE_MAX - (time.time() - _nb_t0)),
             "live system console", since=_nb_mark, poll=5)
    _nb_live = _nb_text(_nb_mark)
    _nb_pm = _nb_prompt_rx.search(_nb_live)
    if _nb_pm:
        # Steps 3 and 4 recognise the prompt by this exact host name.
        _nb_host = re.match(r"admin@(\S+)", _nb_pm.group(0)).group(1)
        break
    _nb_logins += 1
    if _nb_logins > 3:
        _nb_fatal("still not logged in after 3 login rounds")
    _nb_mark = _nb_pos()
    if _nb_login_rx.search(_nb_live):
        log("nextbsd installOpts: live system asks for a login; admin")
        _nb_type("admin")
    elif _nb_pass_rx.search(_nb_live):
        log("nextbsd installOpts: live system asks for a password; empty")
        _nb_type("")
time.sleep(3)

# --- 3. install --------------------------------------------------------------
# One line, POSIX sh inside sudo so the admin shell (zsh) does not matter.
# The disk is the first DISK line whose install-medium flag (second-to-last
# field) is 0 -- fields are tab-separated, but a whitespace split keeps the
# last two fields right either way.
#
# The console ECHOES the typed line, so a marker pattern must never match
# the command text itself: both markers are printed from shell variables
# ($d, $r), which puts `$d` / `$r` -- never a device name or a digit --
# right after the marker in the echo. (A literal `@@nb-install-rc=90` in
# the no-disk branch once matched its own echo and failed a good install.)
# The rc pattern also wants the end of the line, so a status split across
# two reads of the log is never taken for a shorter one.
_nb_mark = _nb_pos()
# _nb_ran=1 marks THIS zsh: the status fallback below reads it back, so a
# fresh autologin shell (whose $? is 0) can never vouch for an install it
# did not run.
_nb_type("_nb_ran=1; "
         "sudo env -i PATH=/sbin:/usr/sbin:/bin:/usr/bin sh -c '"
         "d=$(/usr/libexec/nextbsd-installer/probe-disks.sh </dev/null"
         " | awk \"\\$1 == \\\"DISK\\\" && \\$(NF-1) == \\\"0\\\""
         " { print \\$2; exit }\"); "
         "echo \"@@nb-disk=[$d]\"; "
         "if [ -n \"$d\" ]; then "
         "/usr/libexec/nextbsd-installer/do-install.sh -d \"$d\" </dev/null 2>&1; "
         "r=$?; else r=90; fi; "
         "echo \"@@nb-install-rc=$r\"; exit $r'")
_nb_m = _nb_wait(r"@@nb-disk=\[(\w*)\]\n", 300, "target disk",
                 since=_nb_mark)
log("nextbsd installOpts: installing onto %r" % _nb_m.group(1))
# The rc marker line can go missing: on 27.0.0-195 Desktop amd64 the
# installer printed DONE and the admin prompt came back, but the
# `@@nb-install-rc=` line never reached the serial log, and waiting for it
# alone burnt the whole 7200 s bound. So the end of the sudo line is ALSO
# recognised by the admin prompt coming back AFTER the disk marker (the
# shell is busy from that marker until the line ends), and the status is
# then read from zsh's $? -- the sh exits with $r, and sudo and env pass it
# through. Both are searched for after the disk marker in the text since
# the line was typed, never after a later offset: a fast exit (no disk:
# r=90 at once) puts the marker, the rc line and the prompt into one read.
# The rc line wins when both are there. The prompt is not anchored to the
# end of a line here, so console output that lands right after it does not
# hide it; it must carry the host name step 2 saw, so installer output that
# merely looks like `admin@x y % ` does not count either.
# The rc line and the status answer must each be a whole line of their own,
# so installer output that merely contains the marker text is never taken.
_nb_prompt_any = (r"admin@%s \S+ [%%#$](?: |$)"
                  % re.escape(globals().get("_nb_host", "nextbsd")))
_nb_m = _nb_wait(r"@@nb-disk=\[\w*\]\n(?:.*?^@@nb-install-rc=(\d+)\n"
                 r"|.*?%s)" % _nb_prompt_any, _NB_INSTALL_MAX,
                 "installer exit", since=_nb_mark, poll=10)
if _nb_m.group(1) is not None:
    _nb_rc = _nb_m.group(1)
else:
    log("nextbsd installOpts: the shell is back but the rc marker line is "
        "missing; asking zsh for the sudo line's exit status")
    _nb_mark = _nb_pos()
    _nb_type("echo \"@@nb-install-status=[${_nb_ran-}:$?]\"")
    _nb_m = _nb_wait(r"^@@nb-install-status=\[(\d*):(\d+)\]\n", 60,
                     "installer exit status", since=_nb_mark)
    if _nb_m.group(1) != "1":
        _nb_fatal("the shell that answered is not the one that ran the "
                  "install (the console session was restarted?)")
    _nb_rc = _nb_m.group(2)
if _nb_rc != "0":
    _nb_fatal("do-install.sh exited %s (90 = no target disk found by "
              "probe-disks.sh)" % _nb_rc)
log("nextbsd installOpts: install done")

# --- 4. power off ------------------------------------------------------------
# shutdown -p first (what upstream's harness sends); halt -p if launchd does
# not take QEMU down within two minutes; fatal if even that does not, rather
# than letting main()'s _wait_vm_down() kill QEMU under a pool that was
# never exported and then boot that disk.
# The prompt that follows the rc line (or the status answer), searched from
# the line typed last and not anchored to the end of a line, so console
# output right after the prompt cannot hide it. (The echo of a typed line
# carries `$r` / `${_nb_ran-}`, never a digit, after the marker.)
_nb_wait(r"^@@nb-install-(?:rc=\d+|status=\[\d*:\d+\])\n.*?%s"
         % _nb_prompt_any, 60, "prompt after the install", since=_nb_mark)
_nb_type("sudo shutdown -p now")
if not _nb_until(lambda: isRunning() != 0, 120, 5):
    log("nextbsd installOpts: still up 120 s after shutdown -p; halt -p")
    _nb_type("sudo /sbin/halt -p")
    if not _nb_until(lambda: isRunning() != 0, 120, 5):
        _nb_fatal("the live system did not power off after shutdown -p "
                  "and halt -p (240 s)")
log("nextbsd installOpts: the live system powered off")

# UEFI was for the live ISO only (see the conf's VM_BIOS comment). Every boot
# from here on -- and the profile.json / .qemu that exportOVA() publishes --
# must use QEMU's default SeaBIOS, which boots the installed disk through
# gptzfsboot: left set, VM_BIOS would publish "firmware_kind": "bios" and a
# host-specific `-bios /usr/share/ovmf/OVMF.fd` to every anyvm user, and the
# post-install boots would never exercise the firmware anyvm actually uses.
if os.environ.pop("VM_BIOS", None):
    log("nextbsd installOpts: VM_BIOS unset; the installed disk boots under "
        "SeaBIOS from here on")
