"""
plonk2daw -- Soundminer -> Pro Tools spotting watcher (event-driven, zero idle PT traffic).

It TAILS Soundminer's log for `Generated FilePath:<path>` lines (pure filesystem,
no Pro Tools polling), waits for the file to finish writing, then does a single
connect -> spot -> disconnect burst via PTSL -- the same short-lived pattern as
the one-shot spotter. Because nothing polls Pro Tools while idle, PT menus and
selection stay responsive.

  * Follows session/Tpath automatically (it spots whatever path SM logged, as
    long as it's inside an "Audio Files" folder -- batch transfers elsewhere are
    ignored).
  * Default = AUTO: poly, unless you've selected >=2 tracks -> spread.
  * Alt+Shift+S = sticky toggle to FORCE spread (flip again -> AUTO).
  * Alt+Shift+A = sticky toggle to FORCE ambisonics on new-track creation
    (a bare 4ch B-Format file -> 1stOrderAmbisonics instead of PT's Quad guess).

Note: trim-to-range (fill) does NOT work through this watcher under SM's Spot /
Bring-Into-DAW -- SM clears the PT selection via PTSL at the start of SpotToDAW(),
before the transfer, so no range survives to trim against. For trim-to-range use
the spotter CLI (`spotter.py --file X` with a range selected) or S2D's native trim.

Run from this folder:
    python watch.py                 (auto-finds %APPDATA%\\Soundminer\\SoundminerLog.txt)
    python watch.py --log "C:\\path\\to\\SoundminerLog.txt"
Quit with Ctrl+C.

Keep "Use Pro Tools Scripting SDK" ON in Soundminer (needed for it to follow the
session/Tpath). SM's own spot will fail with a harmless -2; we do the real placement.
"""
__author__ = "Stan van den Baar"
__license__ = "MIT"

import os, sys, time, argparse, threading, ctypes
from ctypes import wintypes
from spotter import connect, spot_file, PtslError

MARK = "Generated FilePath:"
POLL = 0.5            # filesystem tail cadence (no PT calls)
STABLE_TIMEOUT = 180  # give up waiting for a logged file to appear/finish
# hotkeys (Win32): Alt+Shift+S = force spread, Alt+Shift+A = force ambi
MOD_ALT, MOD_SHIFT, WM_HOTKEY = 0x0001, 0x0004, 0x0312
VK_S, VK_A = 0x53, 0x41
HK_SPREAD, HK_AMBI = 1, 2            # RegisterHotKey ids (== WM_HOTKEY wParam)
SPREAD_LABEL, AMBI_LABEL = "Alt+Shift+S", "Alt+Shift+A"

force_spread = [False]
force_ambi = [False]


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def foreground_is_soundminer():
    """True if the foreground window belongs to a Soundminer process."""
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return False
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return "soundminer" in buf.value.lower()
        return False
    finally:
        kernel32.CloseHandle(h)


def hotkey_thread():
    user32 = ctypes.windll.user32
    if user32.RegisterHotKey(None, HK_SPREAD, MOD_ALT | MOD_SHIFT, VK_S):
        log("hotkey ready: %s = toggle FORCE-SPREAD (only while Soundminer is focused)" % SPREAD_LABEL)
    else:
        log("WARN: couldn't register %s (in use?). Force-spread disabled; select 2+ tracks to spread." % SPREAD_LABEL)
    if user32.RegisterHotKey(None, HK_AMBI, MOD_ALT | MOD_SHIFT, VK_A):
        log("hotkey ready: %s = toggle FORCE-AMBI (only while Soundminer is focused)" % AMBI_LABEL)
    else:
        log("WARN: couldn't register %s (in use?). Force-ambi disabled; use spotter --ambi for ambisonic new tracks." % AMBI_LABEL)
    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
        if msg.message != WM_HOTKEY:
            continue
        if not foreground_is_soundminer():
            continue                          # only toggle while Soundminer is the foreground app
        if msg.wParam == HK_SPREAD:
            force_spread[0] = not force_spread[0]
            log("MODE -> %s" % ("FORCE-SPREAD" if force_spread[0]
                                 else "AUTO (poly unless >=2 tracks selected)"))
        elif msg.wParam == HK_AMBI:
            force_ambi[0] = not force_ambi[0]
            log("AMBI -> %s" % ("ON (new tracks = ambisonics by channel count)" if force_ambi[0]
                                 else "OFF (PT's Quad/surround guess for new tracks)"))


def is_audio_files(p):
    return os.path.basename(os.path.dirname(p)).lower() == "audio files"


def do_spot(path):
    mode = "spread" if force_spread[0] else "auto"
    log("SPOT: %s   [%s%s]" % (os.path.basename(path), mode, ", AMBI" if force_ambi[0] else ""))
    c = None
    try:
        c = connect(verbose=False)
        spot_file(c, path, mode=mode, ambi=force_ambi[0])
    except PtslError as e:
        log("spot FAILED: %s" % e)
    finally:
        if c is not None:
            try: c.channel.close()
            except Exception: pass


def main():
    ap = argparse.ArgumentParser()
    default_log = os.path.join(os.environ.get("APPDATA", ""), "Soundminer", "SoundminerLog.txt")
    ap.add_argument("--log", default=default_log, help="path to SoundminerLog.txt")
    args = ap.parse_args()
    print(__doc__)
    log("starting; tailing SM log: %s" % args.log)
    threading.Thread(target=hotkey_thread, daemon=True).start()

    f = None
    pos = 0
    pending = {}   # path -> {first_seen, last_size}

    while True:
        try:
            # (re)open the log; handle "not yet created" and SM-relaunch truncation
            if f is None:
                if not os.path.exists(args.log):
                    time.sleep(POLL); continue
                f = open(args.log, "r", encoding="utf-8", errors="replace")
                f.seek(0, os.SEEK_END)          # start at the end -> ignore old transfers
                pos = f.tell()
                log("connected to log (watching for new transfers)")
            if os.path.getsize(args.log) < pos:  # log was truncated (SM relaunched)
                f.close(); f = None; pos = 0
                continue

            # read complete new lines only
            f.seek(pos)
            while True:
                line_start = f.tell()
                line = f.readline()
                if not line:
                    pos = line_start; break
                if not line.endswith("\n"):       # partial line still being written
                    f.seek(line_start); pos = line_start; break
                pos = f.tell()
                if MARK in line:
                    p = line.split(MARK, 1)[1].strip()
                    if p.lower().endswith((".wav", ".aif", ".aiff")) and is_audio_files(p):
                        if p not in pending:
                            pending[p] = {"t": time.time(), "sz": -1}
                            log("queued: %s" % os.path.basename(p))

            # check queued files for write-completion (filesystem only, no PT)
            for p in list(pending):
                info = pending[p]
                if time.time() - info["t"] > STABLE_TIMEOUT:
                    log("dropped (never finished): %s" % os.path.basename(p)); del pending[p]; continue
                try:
                    sz = os.path.getsize(p)
                except OSError:
                    continue                       # not on disk yet
                if sz > 0 and sz == info["sz"]:    # size stable across two polls -> done
                    del pending[p]
                    do_spot(p)
                else:
                    info["sz"] = sz
        except Exception as e:
            log("error: %s" % e); time.sleep(1)
        time.sleep(POLL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nwatcher stopped.")
