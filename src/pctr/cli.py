import argparse
import re
import sys
import time
from typing import Any, NoReturn, cast

for _stream in (sys.stdout, sys.stderr):
    _reconf = getattr(_stream, "reconfigure", None)
    if _reconf is not None:
        try:
            _reconf(encoding="utf-8", errors="replace")
        except Exception:
            pass

from pywinauto import Desktop

DESCRIPTION = "Element-based Windows UI Automation CLI for AI agents."

try:
    from importlib.metadata import version as _pkg_version
    _VERSION = _pkg_version("pctr")
except Exception:
    _VERSION = "unknown"


def _fail(msg, code=2) -> NoReturn:
    """Print a clean one-line error and exit non-zero (no traceback)."""
    print("error: %s" % msg, file=sys.stderr)
    raise SystemExit(code)


def _compile(pattern, what, flags=re.I):
    """Compile a user-supplied regex, failing cleanly on a bad pattern."""
    try:
        return re.compile(pattern, flags)
    except re.error as e:
        _fail("invalid %s regex %r: %s" % (what, pattern, e))


def _iter_windows():
    return Desktop(backend="uia").windows()


def _find_window(title_re, process=None, timeout=5.0, index=0):
    # Locate the HWND with Win32 first (fast, and never touches UIA providers of
    # windows on *other* virtual desktops, which don't answer and can deadlock a
    # full desktop enumeration). Then bind a UIA wrapper to just that one window.
    h = _find_hwnd(title_re, process, timeout)
    return Desktop(backend="uia").window(handle=h)


def _enum_top_windows():
    """Top-level visible windows via Win32 EnumWindows (no UIA - survives a wedged UIA provider)."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    out = []
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, lp):
        n = user32.GetWindowTextLengthW(hwnd)
        if n and user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            out.append((int(hwnd), buf.value, int(pid.value)))
        return True

    user32.EnumWindows(proc(cb), 0)
    return out


def _find_hwnd(title_re, process=None, timeout=5.0):
    """Find a top-level window handle by title regex (Win32, no UIA)."""
    rx = _compile(title_re, "title") if title_re else None
    deadline = time.time() + timeout
    last = 0
    while True:
        matches = [
            w for w in _enum_top_windows()
            if (rx is None or rx.search(w[1])) and (process is None or w[2] == process)
        ]
        last = len(matches)
        if matches:
            return matches[0][0]
        if time.time() >= deadline:
            break
        time.sleep(0.25)
    raise SystemExit(
        "window not found: title_re=%r process=%r (saw %d match(es))" % (title_re, process, last)
    )


def _matches(win, name=None, control_type=None, auto_id=None):
    rx_name = _compile(name, "name") if name is not None else None
    out = []
    for el in win.descendants():
        try:
            info = el.element_info
        except Exception:
            continue
        if rx_name is not None:
            n = info.name or ""
            if rx_name.search(n) is None:
                continue
        if control_type is not None:
            c = (info.control_type or "")
            if c.lower() != control_type.lower():
                continue
        if auto_id is not None:
            a = (info.automation_id or "")
            if a != auto_id:
                continue
        out.append(el)
    return out


def _describe(el):
    info = el.element_info
    r = info.rectangle
    return "%s | type=%s | id=%s | rect=%d,%d,%d,%d" % (
        info.name,
        info.control_type,
        info.automation_id,
        r.left,
        r.top,
        r.right,
        r.bottom,
    )


def _print_json(obj):
    import json
    print(json.dumps(obj, ensure_ascii=False))


def _is_json(args):
    return bool(getattr(args, "json_out", False))


def _el_dict(el, depth=None):
    info = el.element_info
    r = info.rectangle
    d = {
        "name": info.name,
        "type": info.control_type,
        "id": info.automation_id,
        "rect": [r.left, r.top, r.right, r.bottom],
    }
    if depth is not None:
        d["depth"] = depth
    return d


def _get_element(args):
    win = _find_window(args.title, getattr(args, "process", None), getattr(args, "timeout", 5.0))
    els = _matches(win, args.name, getattr(args, "control_type", None), getattr(args, "auto_id", None))
    onscreen = [e for e in els if e.element_info.rectangle.right > 0 and e.element_info.rectangle.bottom > 0]
    if onscreen:
        els = onscreen
    if not els:
        raise SystemExit("no element matched name=%r type=%r id=%r" % (
            getattr(args, "name", None), getattr(args, "control_type", None), getattr(args, "auto_id", None)))
    idx = getattr(args, "nth", 0)
    if idx >= len(els):
        raise SystemExit("matched %d element(s); nth=%d out of range" % (len(els), idx))
    return win, els[idx], len(els)


def cmd_windows(args):
    rx = _compile(args.filter, "filter") if args.filter else None
    proc = getattr(args, "process", None)
    rows = []
    for hwnd, title, pid in _enum_top_windows():
        if rx and rx.search(title) is None:
            continue
        if proc is not None and pid != proc:
            continue
        rows.append({"hwnd": hwnd, "pid": pid, "title": title})
    if _is_json(args):
        _print_json(rows)
        return
    for r in rows:
        print("pid=%s | %s" % (r["pid"], r["title"]))


def _walk(root, max_depth):
    """Yield (element, depth) for descendants of root, bounded to max_depth."""
    stack = []
    try:
        kids = root.children()
    except Exception:
        kids = []
    for k in reversed(kids):
        stack.append((k, 1))
    while stack:
        node, d = stack.pop()
        yield node, d
        if d >= max_depth:
            continue
        try:
            kids = node.children()
        except Exception:
            kids = []
        for k in reversed(kids):
            stack.append((k, d + 1))


def cmd_tree(args):
    win = _find_window(args.title, args.process, args.timeout)
    depth = getattr(args, "depth", None)
    depth_mode = depth is not None and 0 <= depth < 99
    it = _walk(win, depth) if depth_mode else ((el, 1) for el in win.descendants())
    rows = []
    truncated = False
    for el, d in it:
        try:
            el.element_info
        except Exception:
            continue
        rows.append(_el_dict(el, d if depth_mode else None))
        if len(rows) >= args.limit:
            truncated = True
            break
    if _is_json(args):
        _print_json({
            "window": win.window_text(),
            "pid": win.process_id(),
            "elements": rows,
            "truncated": truncated,
        })
        return
    print("WINDOW: %s (pid=%s)" % (win.window_text(), win.process_id()))
    for r in rows:
        prefix = "  " * (r["depth"] - 1) if depth_mode else ""
        print("  %s%s | type=%s | id=%s | rect=%d,%d,%d,%d" % (
            prefix, r["name"], r["type"], r["id"], *r["rect"]))
    if truncated:
        print("... (limit %d reached)" % args.limit)


def cmd_find(args):
    win = _find_window(args.title, args.process, args.timeout)
    els = _matches(win, args.name, args.control_type, args.auto_id)
    if not els:
        if _is_json(args):
            _print_json({"window": win.window_text(), "matches": [], "count": 0})
            raise SystemExit(1)
        print("no matches")
        raise SystemExit(1)
    nth = getattr(args, "nth", 0) or 0
    if nth > 0:
        if nth >= len(els):
            _fail("matched %d element(s); nth=%d out of range" % (len(els), nth))
        els = [els[nth]]
    limit = getattr(args, "limit", None)
    shown = els[:limit] if limit else els
    if _is_json(args):
        _print_json({
            "window": win.window_text(),
            "count": len(els),
            "matches": [dict(_el_dict(el), index=i) for i, el in enumerate(shown)],
        })
        return
    print("WINDOW: %s" % win.window_text())
    for i, el in enumerate(shown):
        print("[%d] %s" % (i, _describe(el)))
    if limit and len(els) > limit:
        print("... (%d more)" % (len(els) - limit))


def cmd_click(args):
    win, el, n = _get_element(args)
    print("target: %s  (%d match(es))" % (_describe(el), n))
    if args.method == "invoke" or (args.method == "auto" and hasattr(el, "invoke")):
        try:
            el.invoke()
            print("invoked")
            return
        except Exception as e:
            print("invoke failed (%s), falling back to mouse" % e)
    el.click_input(button=args.button)
    if args.dbl:
        el.click_input(button=args.button)
    print("clicked")


def cmd_set(args):
    win, el, n = _get_element(args)
    print("target: %s" % _describe(el))
    try:
        el.set_edit_text(args.text)
        print("set via ValuePattern")
    except Exception:
        try:
            el.click_input()
            el.type_keys("^a{BACKSPACE}")
            el.type_keys(args.text, with_spaces=True, with_newlines=True)
            print("set via type_keys")
        except Exception as e:
            _fail("set failed (no ValuePattern and text has pywinauto key syntax): %s" % e)


def _chunks(text, size):
    return [text[i:i + size] for i in range(0, len(text), size)]


def cmd_type(args):
    delay = args.delay
    chunk = max(1, args.chunk)
    if args.title:
        win, el, n = _get_element(args)
        try:
            for part in _chunks(args.text, chunk):
                el.type_keys(part, with_spaces=True, with_newlines=True)
                time.sleep(delay)
        except Exception as e:
            _fail("type failed (text has pywinauto key syntax like { } + ^ %% ~ ()): %s" % e)
    else:
        import pyautogui
        pyautogui.write(args.text, interval=delay)
    print("typed")


def cmd_keys(args):
    from pywinauto.keyboard import send_keys
    send_keys(args.keys, with_spaces=True)
    print("sent keys: %s" % args.keys)


def cmd_focus(args):
    win = _find_window(args.title, args.process, args.timeout)
    win.set_focus()
    print("focused: %s" % win.window_text())


def cmd_wait(args):
    deadline = time.time() + args.timeout
    while time.time() < deadline:
        try:
            win = _find_window(args.title, args.process, timeout=1.0)
            if _matches(win, args.name, args.control_type, args.auto_id):
                print("found")
                return
        except SystemExit:
            pass
        time.sleep(0.4)
    raise SystemExit("timed out waiting for element")


def cmd_shot(args):
    import pyautogui
    if args.title:
        win = _find_window(args.title, args.process, args.timeout)
        r = win.rectangle()
        img = pyautogui.screenshot(region=(r.left, r.top, r.width(), r.height()))
    else:
        img = pyautogui.screenshot()
    try:
        img.save(args.out)
    except (OSError, ValueError) as e:
        _fail("cannot write screenshot to %r: %s" % (args.out, e))
    print("saved %s (%dx%d)" % (args.out, img.width, img.height))


def _mouse_backend(args):
    if getattr(args, "direct", False):
        import pydirectinput
        pydirectinput.PAUSE = 0.01
        return pydirectinput
    import pyautogui
    pyautogui.FAILSAFE = False
    return pyautogui


def _parse_pair(text):
    try:
        x, y = text.split(",")
        return int(float(x)), int(float(y))
    except (ValueError, AttributeError):
        _fail("bad coordinate pair %r (expected 'x,y')" % text)


def cmd_move(args):
    m = _mouse_backend(args)
    m.moveTo(args.x, args.y, duration=getattr(args, "duration", 0.0))
    print("moved to %d,%d" % (args.x, args.y))


def cmd_down(args):
    m = _mouse_backend(args)
    m.mouseDown(button=args.button)
    print("mouse down (%s)" % args.button)


def cmd_up(args):
    m = _mouse_backend(args)
    m.mouseUp(button=args.button)
    print("mouse up (%s)" % args.button)


def cmd_hold(args):
    m = _mouse_backend(args)
    if args.x is not None and args.y is not None:
        m.moveTo(args.x, args.y, duration=0.0)
    m.mouseDown(button=args.button)
    print("holding %s for %dms..." % (args.button, args.ms))
    time.sleep(args.ms / 1000.0)
    m.mouseUp(button=args.button)
    print("released")


def cmd_keydown(args):
    m = _mouse_backend(args)
    m.keyDown(args.key)
    print("key down: %s" % args.key)


def cmd_keyup(args):
    m = _mouse_backend(args)
    m.keyUp(args.key)
    print("key up: %s" % args.key)


def cmd_keyhold(args):
    m = _mouse_backend(args)
    m.keyDown(args.key)
    print("holding key %s for %dms..." % (args.key, args.ms))
    time.sleep(args.ms / 1000.0)
    m.keyUp(args.key)
    print("released")


def cmd_hotkey(args):
    m = _mouse_backend(args)
    keys = [k.strip() for k in args.keys.split("+") if k.strip()]
    for k in keys:
        m.keyDown(k)
    for k in reversed(keys):
        m.keyUp(k)
    print("hotkey: %s" % "+".join(keys))


def cmd_size(args):
    import pyautogui
    w, h = pyautogui.size()
    print("%dx%d" % (w, h))


def _vdm() -> Any:
    from ctypes import POINTER
    from ctypes.wintypes import BOOL, HWND
    import comtypes
    from comtypes import COMMETHOD, GUID, HRESULT, IUnknown

    class IVirtualDesktopManager(IUnknown):
        _iid_ = GUID("{A5CD92FF-29BE-454C-8D04-D82879FB3F1B}")
        _methods_ = [
            COMMETHOD([], HRESULT, "IsWindowOnCurrentVirtualDesktop",
                      (["in"], HWND, "w"), (["out"], POINTER(BOOL), "on")),
            COMMETHOD([], HRESULT, "GetWindowDesktopId",
                      (["in"], HWND, "w"), (["out"], POINTER(GUID), "did")),
            COMMETHOD([], HRESULT, "MoveWindowToDesktop",
                      (["in"], HWND, "w"), (["in"], POINTER(GUID), "did")),
        ]

    return cast(Any, comtypes.CoCreateInstance(
        GUID("{AA509086-5CA9-4C25-8F95-589D3C07B48A}"),
        interface=IVirtualDesktopManager,
    ))


def _guid_norm(g):
    return str(g).strip("{}").lower()


def _vdesk_guids():
    """Ordered desktop GUIDs from the shell's registry (no undocumented COM)."""
    import uuid
    import winreg
    k = winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Explorer\VirtualDesktops",
    )
    try:
        raw = winreg.QueryValueEx(k, "VirtualDesktopIDs")[0]
    finally:
        winreg.CloseKey(k)
    return [_guid_norm(uuid.UUID(bytes_le=raw[i:i + 16])) for i in range(0, len(raw) - 15, 16)]


def _list_windows_with_desktop():
    import ctypes
    from ctypes import wintypes
    vdm: Any = _vdm()
    user32 = ctypes.windll.user32
    out = []
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, lp):
        n = user32.GetWindowTextLengthW(hwnd)
        if n and user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            try:
                d = _guid_norm(vdm.GetWindowDesktopId(wintypes.HWND(hwnd)))
            except Exception:
                d = ""
            out.append((hwnd, buf.value, d))
        return True

    user32.EnumWindows(proc(cb), 0)
    return out


def _desktop_list(args):
    import ctypes
    from ctypes import wintypes
    guids = _vdesk_guids()
    vdm: Any = _vdm()
    fg = ctypes.windll.user32.GetForegroundWindow()
    try:
        cur = _guid_norm(vdm.GetWindowDesktopId(wintypes.HWND(fg)))
    except Exception:
        cur = ""
    counts = {}
    for _hwnd, _title, d in _list_windows_with_desktop():
        counts[d] = counts.get(d, 0) + 1
    if _is_json(args):
        _print_json([
            {"index": i, "guid": d, "current": d == cur, "windows": counts.get(d, 0)}
            for i, d in enumerate(guids)
        ])
        return
    for i, d in enumerate(guids):
        print("#%d %s%s  (%d windows)" % (i, d, "  [current]" if d == cur else "", counts.get(d, 0)))


def _desktop_windows(args):
    guids = _vdesk_guids()
    want = getattr(args, "desktop", None)
    rows = []
    for hwnd, title, d in _list_windows_with_desktop():
        i = guids.index(d) if d in guids else -1
        if want is not None and i != want:
            continue
        rows.append({"desktop": i, "hwnd": hwnd, "title": title})
    if _is_json(args):
        _print_json(rows)
        return
    for r in rows:
        print("desktop=#%s | hwnd=%s | %s" % (r["desktop"], r["hwnd"], r["title"]))


def _desktop_where(args, bool_only):
    if not getattr(args, "title", None) and getattr(args, "process", None) is None:
        _fail("%s needs --title (or --process)" % args.action)
    import ctypes
    from ctypes import wintypes
    vdm: Any = _vdm()
    h = _find_hwnd(args.title, getattr(args, "process", None), getattr(args, "timeout", 5.0))
    hwnd = wintypes.HWND(int(h))
    if bool_only:
        on = vdm.IsWindowOnCurrentVirtualDesktop(hwnd)
        if _is_json(args):
            _print_json({"on_current": bool(on)})
            return
        print("on_current=%s" % ("True" if on else "False"))
        return
    guids = _vdesk_guids()
    d = _guid_norm(vdm.GetWindowDesktopId(hwnd))
    i = guids.index(d) if d in guids else -1
    if _is_json(args):
        _print_json({"desktop": i, "guid": d})
        return
    print("desktop=#%d %s" % (i, d))


def _desktop_move(args):
    import ctypes
    from ctypes import wintypes
    import comtypes
    if not getattr(args, "title", None):
        _fail("move-window needs --title")
    if getattr(args, "to", None) is None:
        _fail("move-window needs --to N")
    guids = _vdesk_guids()
    n = args.to
    if n < 0 or n >= len(guids):
        _fail("desktop #%d out of range (0-%d)" % (n, len(guids) - 1))
    vdm: Any = _vdm()
    h = _find_hwnd(args.title, getattr(args, "process", None), getattr(args, "timeout", 5.0))
    hwnd = wintypes.HWND(int(h))
    try:
        vdm.MoveWindowToDesktop(hwnd, comtypes.GUID("{" + guids[n] + "}"))
    except Exception as e:
        msg = str(e).lower()
        if "denied" in msg or "0x80070005" in msg or "access" in msg:
            _fail(
                "Windows 11 blocks third-party MoveWindowToDesktop (Access denied). "
                "read (list/where/windows) and UIA actions still work cross-desktop; "
                "moving windows between desktops works on Windows 10."
            )
        _fail("move-window failed: %s" % e)
    print("moved %r to desktop #%d" % (args.title, n))


def cmd_desktop(args):
    action = args.action
    if action == "list":
        return _desktop_list(args)
    if action == "windows":
        return _desktop_windows(args)
    if action == "on-current":
        return _desktop_where(args, bool_only=True)
    if action == "where":
        return _desktop_where(args, bool_only=False)
    if action == "move-window":
        return _desktop_move(args)
    combos = {
        "next": ("win", "ctrl", "right"),
        "prev": ("win", "ctrl", "left"),
        "new": ("win", "ctrl", "d"),
        "close": ("win", "ctrl", "f4"),
    }
    combo = combos[action]
    if getattr(args, "direct", False):
        import pydirectinput
        pydirectinput.PAUSE = 0.01
        for k in combo:
            pydirectinput.keyDown(k)
        for k in reversed(combo):
            pydirectinput.keyUp(k)
    else:
        import pyautogui
        pyautogui.FAILSAFE = False
        pyautogui.hotkey(*combo)
    print("desktop: %s" % action)


def cmd_drag(args):
    m = _mouse_backend(args)
    x1, y1 = _parse_pair(args.start)
    x2, y2 = _parse_pair(args.end)
    m.moveTo(x1, y1, duration=0.0)
    m.mouseDown(button=args.button)
    m.moveTo(x2, y2, duration=getattr(args, "duration", 0.3))
    m.mouseUp(button=args.button)
    print("dragged %d,%d -> %d,%d" % (x1, y1, x2, y2))


_OCR_PS = r'''
param([string]$Path)
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, $type) {
  $m = $asTaskGeneric.MakeGenericMethod($type)
  $t = $m.Invoke($null, @($op))
  $t.Wait(-1) | Out-Null
  $t.Result
}
$null = [Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder,Windows.Foundation,ContentType=WindowsRuntime]
$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($Path)) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$engine = $null
$lang = $env:PCTR_OCR_LANG
if ($lang) {
  $null = [Windows.Globalization.Language,Windows.Foundation,ContentType=WindowsRuntime]
  $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage((New-Object Windows.Globalization.Language $lang))
  if (-not $engine) { Write-Error ("OCR language not available on this system: " + $lang); exit 2 }
} else {
  $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
  if (-not $engine) {
    $null = [Windows.Globalization.Language,Windows.Foundation,ContentType=WindowsRuntime]
    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage((New-Object Windows.Globalization.Language 'en-US'))
  }
  if (-not $engine) { Write-Error 'no OCR engine available'; exit 2 }
}
$res = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
$out = foreach ($line in $res.Lines) { foreach ($w in $line.Words) { [pscustomobject]@{ text = $w.Text; x = [int]$w.BoundingRect.X; y = [int]$w.BoundingRect.Y; w = [int]$w.BoundingRect.Width; h = [int]$w.BoundingRect.Height } } }
ConvertTo-Json -Compress -InputObject @($out)
'''


def _pctr_tempdir():
    import os
    import tempfile
    d = os.path.join(tempfile.gettempdir(), "pctr")
    os.makedirs(d, exist_ok=True)
    return d


def _windows_ocr(image_path, lang=None):
    import base64
    import json
    import os
    import subprocess
    body = _OCR_PS.strip()
    if body.startswith("param"):  # drop the param() line; path comes via env
        body = body.split("\n", 1)[1]
    ps = "$Path = $env:PCTR_OCR_PATH\n" + body
    enc = base64.b64encode(ps.encode("utf-16-le")).decode("ascii")
    env = dict(os.environ)
    env["PCTR_OCR_PATH"] = image_path
    if lang:
        env["PCTR_OCR_LANG"] = lang
    else:
        env.pop("PCTR_OCR_LANG", None)
    out = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", enc],
        capture_output=True, text=True, env=env,
    )
    txt = (out.stdout or "").strip()
    if not txt:
        raise SystemExit("OCR failed: " + (out.stderr or "").strip()[:400])
    data = json.loads(txt, strict=False)
    if isinstance(data, dict):
        data = [data]
    return [w for w in data if w and w.get("text")]


def _ocr_capture(args):
    import os
    import pyautogui
    path = os.path.join(_pctr_tempdir(), "ocr_%d.png" % os.getpid())
    ox = oy = 0
    title = getattr(args, "title", None)
    if title:
        win = _find_window(title, getattr(args, "process", None), getattr(args, "timeout", 5.0))
        r = win.rectangle()
        img = pyautogui.screenshot(region=(r.left, r.top, r.width(), r.height()))
        ox, oy = r.left, r.top
    else:
        img = pyautogui.screenshot()
    try:
        img.save(path)
        words = _windows_ocr(path, getattr(args, "lang", None))
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    return words, ox, oy


def cmd_ocr(args):
    words, ox, oy = _ocr_capture(args)
    if _is_json(args):
        _print_json([
            {"text": w["text"], "x": ox + w["x"], "y": oy + w["y"], "w": w["w"], "h": w["h"]}
            for w in words
        ])
        return
    for w in words:
        print("%s | %d,%d %dx%d" % (w["text"], ox + w["x"], oy + w["y"], w["w"], w["h"]))
    print("(%d words)" % len(words))


def cmd_ocrfind(args):
    import re
    words, ox, oy = _ocr_capture(args)
    rx = re.compile(args.text, re.I)
    hits = [w for w in words if rx.search(w["text"])]
    if _is_json(args):
        _print_json([
            {"text": w["text"], "x": ox + w["x"], "y": oy + w["y"], "w": w["w"], "h": w["h"],
             "cx": ox + w["x"] + w["w"] // 2, "cy": oy + w["y"] + w["h"] // 2}
            for w in hits
        ])
        return
    for i, w in enumerate(hits):
        print("[%d] %s | center=%d,%d" % (i, w["text"], ox + w["x"] + w["w"] // 2, oy + w["y"] + w["h"] // 2))
    if not hits:
        print("no OCR match for %r" % args.text)


def cmd_ocrclick(args):
    import re
    import pyautogui
    words, ox, oy = _ocr_capture(args)
    rx = re.compile(args.text, re.I)
    hits = [w for w in words if rx.search(w["text"])]
    if not hits:
        raise SystemExit("no OCR match for %r" % args.text)
    idx = getattr(args, "nth", 0)
    if idx >= len(hits):
        raise SystemExit("matched %d word(s); nth=%d out of range" % (len(hits), idx))
    w = hits[idx]
    cx = ox + w["x"] + w["w"] // 2
    cy = oy + w["y"] + w["h"] // 2
    pyautogui.FAILSAFE = False
    pyautogui.moveTo(cx, cy, duration=0.0)
    pyautogui.click()
    print("ocr-clicked %r at %d,%d" % (w["text"], cx, cy))


_MODEL_CACHE = {}


def _look_detect(args):
    import contextlib
    import os
    import pyautogui
    try:
        from ultralytics.models import YOLOWorld
    except Exception as e:
        raise SystemExit("YOLO-World needs ultralytics: pip install ultralytics (%s)" % e)
    path = os.path.join(_pctr_tempdir(), "look_%d.png" % os.getpid())
    ox = oy = 0
    title = getattr(args, "title", None)
    if title:
        win = _find_window(title, getattr(args, "process", None), getattr(args, "timeout", 5.0))
        r = win.rectangle()
        img = pyautogui.screenshot(region=(r.left, r.top, r.width(), r.height()))
        ox, oy = r.left, r.top
    else:
        img = pyautogui.screenshot()
    weights = getattr(args, "model", None) or os.environ.get("PCTR_YOLO_MODEL") or "yolov8s-worldv2.pt"
    conf = args.conf if getattr(args, "conf", None) is not None else 0.35
    try:
        img.save(path)
        # keep stdout machine-readable: route ultralytics/tqdm chatter to stderr
        with contextlib.redirect_stdout(sys.stderr):
            model = _MODEL_CACHE.get(weights)
            if model is None:
                model = YOLOWorld(weights)
                _MODEL_CACHE[weights] = model
            model.set_classes([args.for_])
            results = model.predict(path, conf=conf, agnostic_nms=True, verbose=False)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    r0 = results[0]
    names = r0.names
    boxes = r0.boxes
    dets = []
    if boxes is None:
        return dets
    for i in range(len(boxes)):
        x1, y1, x2, y2 = [float(v) for v in boxes.xyxy[i].cpu().numpy()]
        dets.append({
            "label": names.get(int(boxes.cls[i]), str(int(boxes.cls[i]))) if isinstance(names, dict) else str(int(boxes.cls[i])),
            "conf": float(boxes.conf[i].cpu().numpy()),
            "x": int(ox + (x1 + x2) / 2),
            "y": int(oy + (y1 + y2) / 2),
            "box": (int(ox + x1), int(oy + y1), int(ox + x2), int(oy + y2)),
        })
    dets.sort(key=lambda d: -d["conf"])
    return dets


def cmd_look(args):
    dets = _look_detect(args)
    if _is_json(args):
        _print_json([
            {"label": d["label"], "conf": round(d["conf"], 4), "x": d["x"], "y": d["y"],
             "box": list(d["box"]), "index": i}
            for i, d in enumerate(dets)
        ])
        return
    for i, d in enumerate(dets):
        print("[%d] %s %.2f | center=%d,%d | box=%d,%d,%d,%d" % (i, d["label"], d["conf"], d["x"], d["y"], *d["box"]))
    if not dets:
        print("nothing found for %r" % args.for_)


def cmd_lookclick(args):
    import pyautogui
    dets = _look_detect(args)
    if not dets:
        raise SystemExit("nothing found for %r" % args.for_)
    idx = getattr(args, "nth", 0)
    if idx >= len(dets):
        raise SystemExit("found %d; nth=%d out of range" % (len(dets), idx))
    d = dets[idx]
    pyautogui.FAILSAFE = False
    pyautogui.moveTo(d["x"], d["y"], duration=0.0)
    pyautogui.click()
    print("look-clicked %r (%.2f) at %d,%d" % (d["label"], d["conf"], d["x"], d["y"]))


SETUP_TARGETS = {
    "opencode": (".config/opencode/skills/pctr", "SKILL.md"),
    "claude": (".claude/skills/pctr", "SKILL.md"),
    "agents": (".agents/skills/pctr", "SKILL.md"),
}


def _data_text(name):
    from importlib import resources
    return resources.files("pctr").joinpath("data", name).read_text(encoding="utf-8")


def cmd_skill(args):
    if getattr(args, "install", False):
        return cmd_setup(args)
    print(_data_text("SKILL.md"))


def cmd_setup(args):
    import os
    from pathlib import Path
    skill = _data_text("SKILL.md")
    frag = _data_text("AGENTS.md")
    home = Path.home()
    targets = list(SETUP_TARGETS) if getattr(args, "target", "all") in (None, "all") else [args.target]
    for t in targets:
        rel, fname = SETUP_TARGETS[t]
        d = home / rel
        d.mkdir(parents=True, exist_ok=True)
        (d / fname).write_text(skill, encoding="utf-8")
        print("installed skill: %s" % (d / fname))
    proj = Path(getattr(args, "project", None) or os.getcwd())
    ag = proj / "AGENTS.md"
    existing = ag.read_text(encoding="utf-8", errors="replace") if ag.exists() else ""
    marker = "<!-- pctr -->"
    if marker in existing:
        print("AGENTS.md already has a pctr section: %s" % ag)
    else:
        with open(ag, "a", encoding="utf-8") as f:
            if existing and not existing.endswith("\n"):
                f.write("\n")
            f.write("\n" + frag + "\n")
        print("appended pctr section to: %s" % ag)
    print("done. restart your agent so it loads the skill.")


MCP_DEFAULT_PORT = 8765


def _mcp_statefile():
    import os
    return os.path.join(_pctr_tempdir(), "mcp.json")


def _pid_alive(pid):
    import ctypes
    k = ctypes.windll.kernel32
    h = k.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    k.CloseHandle(h)
    return True


def _mcp_state():
    import json
    import os
    p = _mcp_statefile()
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _mcp_running():
    st = _mcp_state()
    if st and st.get("pid") and _pid_alive(st["pid"]):
        return st
    return None


def _mcp_port_open(host, port, timeout=0.5):
    import socket
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((host, int(port)))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _mcp_sdk_version():
    try:
        from importlib.metadata import version
        return version("mcp")
    except Exception:
        return None


def _mcp_help():
    print("pctr mcp - manage the pctr MCP server\n")
    print("usage: pctr mcp [start|stop|restart|status|help] [options]\n")
    print("  start    start the server (stdio foreground, or http/sse in the background)")
    print("  stop     stop a background server started with `start`")
    print("  restart  stop (if running) then start")
    print("  status   show SDK + server state")
    print("  help     this text\n")
    print("options:")
    print("  --transport stdio|sse|streamable-http   (default: stdio)")
    print("  --host HOST                             (default: 127.0.0.1)")
    print("  --port PORT                             (default: %d)" % MCP_DEFAULT_PORT)
    print("  --foreground                            run http/sse in the foreground")
    print()
    print("register with an MCP client (stdio):")
    print('  { "command": "pctr-mcp" }')
    print("or (http):")
    print('  { "url": "http://127.0.0.1:%d/mcp" }' % MCP_DEFAULT_PORT)
    print()
    print('needs the SDK:  pip install "pctr[mcp]"')


def _mcp_status(args):
    ver = _mcp_sdk_version()
    print("MCP SDK: %s" % (("mcp " + ver) if ver else 'NOT installed (pip install "pctr[mcp]")'))
    st = _mcp_running()
    if not st:
        print("server:  not running")
        return
    host = st.get("host", "127.0.0.1")
    port = st.get("port")
    reach = _mcp_port_open(host, port) if port else False
    print("server:  running  pid=%s transport=%s %s:%s reachable=%s" % (
        st.get("pid"), st.get("transport"), host, port, "yes" if reach else "no"))


def _mcp_start(args):
    import os
    import subprocess
    running = _mcp_running()
    if running:
        _fail("pctr MCP already running (pid=%s); use `pctr mcp restart`" % running["pid"])
    transport = args.transport
    if transport == "stdio":
        from pctr import mcp_server
        mcp_server.server.run(transport="stdio")
        return
    log = os.path.join(_pctr_tempdir(), "mcp.log")
    cmd = [sys.executable, "-m", "pctr.mcp_server", "--transport", transport,
           "--host", args.host, "--port", str(args.port)]
    flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    with open(log, "ab") as lf:
        p = subprocess.Popen(cmd, creationflags=flags, close_fds=True, stdin=subprocess.DEVNULL,
                             stdout=lf, stderr=subprocess.STDOUT)
    import json
    with open(_mcp_statefile(), "w", encoding="utf-8") as f:
        json.dump({"pid": p.pid, "transport": transport, "host": args.host, "port": args.port}, f)
    for _ in range(24):
        time.sleep(0.25)
        if _mcp_port_open(args.host, args.port):
            break
    reach = _mcp_port_open(args.host, args.port)
    path = "/sse" if transport == "sse" else "/mcp"
    print("pctr MCP started: pid=%s transport=%s  http://%s:%s%s  reachable=%s" % (
        p.pid, transport, args.host, args.port, path, "yes" if reach else "no"))
    print("logs: %s" % log)


def _mcp_stop(args):
    import os
    import subprocess
    st = _mcp_running()
    if not st:
        try:
            os.remove(_mcp_statefile())
        except OSError:
            pass
        print("pctr MCP not running")
        return
    pid = st["pid"]
    subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
    try:
        os.remove(_mcp_statefile())
    except OSError:
        pass
    print("pctr MCP stopped (pid=%s)" % pid)


def cmd_mcp(args):
    action = getattr(args, "action", None) or "help"
    if action in ("help", "info"):
        return _mcp_help()
    if action == "status":
        return _mcp_status(args)
    if action == "start":
        return _mcp_start(args)
    if action == "stop":
        return _mcp_stop(args)
    if action == "restart":
        _mcp_stop(args)
        return _mcp_start(args)


def _run_file(path, json_default=False):
    import shlex
    ap = build_parser()
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError as e:
        _fail("cannot read --file %r: %s" % (path, e))
    ran = 0
    for i, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            argv = shlex.split(line)
        except ValueError as e:
            _fail("--file line %d: %s" % (i, e))
        try:
            a = ap.parse_args(argv)
        except SystemExit:
            _fail("--file line %d: bad command: %s" % (i, line))
        if not getattr(a, "cmd", None):
            _fail("--file line %d: no subcommand: %s" % (i, line))
        if json_default and not getattr(a, "json_out", False):
            a.json_out = True
        print("pctr> %s" % line, file=sys.stderr)
        _validate(a)
        a.func(a)
        ran += 1
    print("ran %d command(s) from %s" % (ran, path), file=sys.stderr)


def add_target(p, require_selector=False, require_window=False):
    p.add_argument("--title", default=None, help="Window title regex")
    p.add_argument("--process", type=int, default=None, help="Filter by process id")
    p.add_argument("--timeout", type=float, default=5.0)
    p.add_argument("--name", default=None, help="Element name regex")
    p.add_argument("--control-type", default=None, help="Exact UIA control type (Button, Edit, ...)")
    p.add_argument("--auto-id", default=None)
    p.add_argument("--nth", type=int, default=0)
    if require_window:
        p.set_defaults(_need_window=True)
    if require_selector:
        p.set_defaults(_need_selector=True)


def _validate(args):
    if getattr(args, "_need_window", False) and not (getattr(args, "title", None) or getattr(args, "process", None) is not None):
        _fail("provide --title (or --process) to choose a window")
    if getattr(args, "_need_selector", False):
        have = any([
            getattr(args, "title", None),
            getattr(args, "process", None) is not None,
            getattr(args, "name", None),
            getattr(args, "control_type", None),
            getattr(args, "auto_id", None),
        ])
        if not have:
            _fail("provide at least one selector: --title / --process / --name / --control-type / --auto-id")


def _add_json(p):
    p.add_argument("--json", action="store_true", dest="json_out", default=argparse.SUPPRESS,
                   help="Emit machine-readable JSON")


def _add_lang(p):
    p.add_argument("--lang", default=None, help="OCR language BCP-47 tag, e.g. en-US or de-DE")


def build_parser():
    ap = argparse.ArgumentParser(prog="pctr", description=DESCRIPTION, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version="pctr %s" % _VERSION)
    ap.add_argument("--json", action="store_true", dest="json_out",
                    help="Emit machine-readable JSON (windows/tree/find/ocr/ocrfind/look/desktop)")
    ap.add_argument("--file", default=None,
                    help="Run pctr commands from a file, one per line (# comments allowed)")
    sub = ap.add_subparsers(dest="cmd", required=False)

    w = sub.add_parser("windows", help="List top-level windows")
    w.add_argument("--filter", default=None)
    w.add_argument("--process", type=int, default=None, help="Only this process id")
    _add_json(w)
    w.set_defaults(func=cmd_windows)

    t = sub.add_parser("tree", help="Dump UIA tree")
    add_target(t, require_window=True)
    t.add_argument("--depth", type=int, default=99)
    t.add_argument("--limit", type=int, default=400)
    _add_json(t)
    t.set_defaults(func=cmd_tree)

    f = sub.add_parser("find", help="Find elements")
    add_target(f, require_window=True)
    f.add_argument("--limit", type=int, default=None, help="Show at most N matches")
    _add_json(f)
    f.set_defaults(func=cmd_find)

    c = sub.add_parser("click", help="Click an element")
    add_target(c, require_selector=True)
    c.add_argument("--method", choices=["auto", "invoke", "mouse"], default="auto")
    c.add_argument("--button", default="left")
    c.add_argument("--dbl", action="store_true")
    c.set_defaults(func=cmd_click)

    s = sub.add_parser("set", help="Set text on an element")
    add_target(s, require_selector=True)
    s.add_argument("--text", required=True)
    s.set_defaults(func=cmd_set)

    ty = sub.add_parser("type", help="Send keystrokes")
    ty.add_argument("--title", default=None)
    ty.add_argument("--name", default=None)
    ty.add_argument("--control-type", default=None)
    ty.add_argument("--auto-id", default=None)
    ty.add_argument("--nth", type=int, default=0)
    ty.add_argument("--process", type=int, default=None)
    ty.add_argument("--timeout", type=float, default=5.0)
    ty.add_argument("--delay", type=float, default=0.03, help="Seconds between chars/chunks")
    ty.add_argument("--chunk", type=int, default=1, help="Chunk size when typing into an element")
    ty.add_argument("--text", required=True)
    ty.set_defaults(func=cmd_type)

    k = sub.add_parser("keys", help="Send a key combo globally")
    k.add_argument("--keys", required=True, help="pywinauto send_keys syntax, e.g. {ENTER} or ^s")
    k.set_defaults(func=cmd_keys)

    fo = sub.add_parser("focus", help="Focus a window")
    fo.add_argument("--title", required=True)
    fo.add_argument("--process", type=int, default=None)
    fo.add_argument("--timeout", type=float, default=5.0)
    fo.set_defaults(func=cmd_focus)

    wa = sub.add_parser("wait", help="Wait for an element")
    add_target(wa, require_selector=True)
    wa.set_defaults(func=cmd_wait)

    sh = sub.add_parser("shot", help="Screenshot")
    sh.add_argument("--out", required=True)
    sh.add_argument("--title", default=None)
    sh.add_argument("--process", type=int, default=None)
    sh.add_argument("--timeout", type=float, default=5.0)
    sh.set_defaults(func=cmd_shot)

    mv = sub.add_parser("move", help="Move the cursor")
    mv.add_argument("--x", type=int, required=True)
    mv.add_argument("--y", type=int, required=True)
    mv.add_argument("--duration", type=float, default=0.0)
    mv.add_argument("--direct", action="store_true", help="Use pydirectinput (games)")
    mv.set_defaults(func=cmd_move)

    dn = sub.add_parser("down", help="Press and hold a mouse button")
    dn.add_argument("--button", default="left")
    dn.add_argument("--direct", action="store_true")
    dn.set_defaults(func=cmd_down)

    up = sub.add_parser("up", help="Release a mouse button")
    up.add_argument("--button", default="left")
    up.add_argument("--direct", action="store_true")
    up.set_defaults(func=cmd_up)

    ho = sub.add_parser("hold", help="Press, hold for --ms, then release")
    ho.add_argument("--ms", type=int, default=1000)
    ho.add_argument("--button", default="left")
    ho.add_argument("--x", type=int, default=None)
    ho.add_argument("--y", type=int, default=None)
    ho.add_argument("--direct", action="store_true")
    ho.set_defaults(func=cmd_hold)

    kd = sub.add_parser("keydown", help="Press and hold a keyboard key")
    kd.add_argument("--key", required=True)
    kd.add_argument("--direct", action="store_true")
    kd.set_defaults(func=cmd_keydown)

    ku = sub.add_parser("keyup", help="Release a keyboard key")
    ku.add_argument("--key", required=True)
    ku.add_argument("--direct", action="store_true")
    ku.set_defaults(func=cmd_keyup)

    kh = sub.add_parser("keyhold", help="Press, hold for --ms, then release a key")
    kh.add_argument("--key", required=True)
    kh.add_argument("--ms", type=int, default=1000)
    kh.add_argument("--direct", action="store_true")
    kh.set_defaults(func=cmd_keyhold)

    hk = sub.add_parser("hotkey", help="Send a key combo, e.g. --keys win+shift+s")
    hk.add_argument("--keys", required=True, help="Combo joined by +, e.g. ctrl+shift+t")
    hk.add_argument("--direct", action="store_true")
    hk.set_defaults(func=cmd_hotkey)

    sz = sub.add_parser("size", help="Print primary screen size as WxH")
    sz.set_defaults(func=cmd_size)

    dp = sub.add_parser("desktop", help="Virtual desktops: list / windows / where / on-current / next / prev / new / close / move-window")
    dp.add_argument("action", choices=["list", "windows", "where", "on-current", "next", "prev", "new", "close", "move-window"])
    dp.add_argument("--direct", action="store_true", help="Use pydirectinput for the switch hotkey")
    dp.add_argument("--title", default=None, help="Window target (where / on-current / move-window)")
    dp.add_argument("--process", type=int, default=None)
    dp.add_argument("--timeout", type=float, default=5.0)
    dp.add_argument("--desktop", type=int, default=None, help="Filter `windows` to this desktop index")
    dp.add_argument("--to", type=int, default=None, help="Target desktop index for move-window")
    _add_json(dp)
    dp.set_defaults(func=cmd_desktop)

    dr = sub.add_parser("drag", help="Drag from --start to --end (x,y pairs)")
    dr.add_argument("--start", required=True, help="x,y")
    dr.add_argument("--end", required=True, help="x,y")
    dr.add_argument("--button", default="left")
    dr.add_argument("--duration", type=float, default=0.3)
    dr.add_argument("--direct", action="store_true")
    dr.set_defaults(func=cmd_drag)

    oc = sub.add_parser("ocr", help="OCR the screen (or a window) and list words with boxes")
    oc.add_argument("--title", default=None)
    oc.add_argument("--process", type=int, default=None)
    oc.add_argument("--timeout", type=float, default=5.0)
    _add_json(oc)
    _add_lang(oc)
    oc.set_defaults(func=cmd_ocr)

    ocf = sub.add_parser("ocrfind", help="OCR then list words matching --text (with click centers)")
    ocf.add_argument("--title", default=None)
    ocf.add_argument("--process", type=int, default=None)
    ocf.add_argument("--timeout", type=float, default=5.0)
    ocf.add_argument("--text", required=True)
    _add_json(ocf)
    _add_lang(ocf)
    ocf.set_defaults(func=cmd_ocrfind)

    occ = sub.add_parser("ocrclick", help="OCR then click the word matching --text")
    occ.add_argument("--title", default=None)
    occ.add_argument("--process", type=int, default=None)
    occ.add_argument("--timeout", type=float, default=5.0)
    occ.add_argument("--text", required=True)
    occ.add_argument("--nth", type=int, default=0)
    _add_lang(occ)
    occ.set_defaults(func=cmd_ocrclick)

    lk = sub.add_parser("look", help="Open-vocab detect objects by text prompt (YOLO-World)")
    lk.add_argument("--for", dest="for_", required=True, help="object to look for, e.g. 'save button'")
    lk.add_argument("--title", default=None)
    lk.add_argument("--process", type=int, default=None)
    lk.add_argument("--timeout", type=float, default=5.0)
    lk.add_argument("--conf", type=float, default=None)
    lk.add_argument("--model", default=None)
    _add_json(lk)
    lk.set_defaults(func=cmd_look)

    lkc = sub.add_parser("lookclick", help="Open-vocab detect then click the top match")
    lkc.add_argument("--for", dest="for_", required=True, help="object to look for")
    lkc.add_argument("--title", default=None)
    lkc.add_argument("--process", type=int, default=None)
    lkc.add_argument("--timeout", type=float, default=5.0)
    lkc.add_argument("--conf", type=float, default=None)
    lkc.add_argument("--model", default=None)
    lkc.add_argument("--nth", type=int, default=0)
    lkc.set_defaults(func=cmd_lookclick)

    sk = sub.add_parser("skill", help="Print the agent skill (SKILL.md), or --install it")
    sk.add_argument("--install", action="store_true")
    sk.add_argument("--target", default="all", choices=["all", "opencode", "claude", "agents"])
    sk.add_argument("--project", default=None)
    sk.set_defaults(func=cmd_skill)

    st = sub.add_parser("setup", help="Install the pctr skill for your agents + append to AGENTS.md")
    st.add_argument("--target", default="all", choices=["all", "opencode", "claude", "agents"])
    st.add_argument("--project", default=None)
    st.set_defaults(func=cmd_setup)

    mc = sub.add_parser("mcp", help="Manage the pctr MCP server: start / stop / restart / status / help")
    mc.add_argument("action", nargs="?", default="help",
                    choices=["start", "stop", "restart", "status", "help", "info"])
    mc.add_argument("--transport", choices=["stdio", "sse", "streamable-http"], default="stdio")
    mc.add_argument("--host", default="127.0.0.1")
    mc.add_argument("--port", type=int, default=MCP_DEFAULT_PORT)
    mc.add_argument("--foreground", action="store_true", help="Run http/sse in the foreground")
    mc.set_defaults(func=cmd_mcp)

    return ap


def main():
    ap = build_parser()
    args = ap.parse_args()
    if getattr(args, "file", None):
        try:
            return _run_file(args.file, json_default=bool(getattr(args, "json_out", False)))
        except SystemExit:
            raise
        except KeyboardInterrupt:
            raise SystemExit(130)
        except Exception as e:
            _fail("%s: %s" % (type(e).__name__, e))
    if not getattr(args, "cmd", None):
        ap.print_help()
        return
    _validate(args)
    try:
        args.func(args)
    except SystemExit:
        raise
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as e:
        _fail("%s: %s" % (type(e).__name__, e))


if __name__ == "__main__":
    main()
