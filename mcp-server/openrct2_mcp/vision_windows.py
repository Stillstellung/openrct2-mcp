"""Windows screenshots of the OpenRCT2 window via Win32 PrintWindow (stdlib only).

PrintWindow with PW_RENDERFULLCONTENT asks the window to render itself into our
bitmap, so the capture works even when the game is covered by other windows.
"""

from __future__ import annotations

import struct
import time
import zlib
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace

from openrct2_mcp.vision import VisionCaptureError

_GAME_EXE_NAMES = ("openrct2.exe", "openrct2.com")
_WINDOW_TITLE = "OpenRCT2"

_PW_CLIENTONLY = 0x1
_PW_RENDERFULLCONTENT = 0x2
_SW_SHOWNOACTIVATE = 4
_SW_SHOWMINNOACTIVE = 7
_HALFTONE = 4
_SRCCOPY = 0x00CC0020
_DIB_RGB_COLORS = 0
_BI_RGB = 0
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4


def encode_png_rgb(width: int, height: int, rgb: bytes) -> bytes:
    """Encode 8-bit RGB pixels (row-major, top-down) as a PNG."""
    stride = width * 3
    if len(rgb) != stride * height:
        raise ValueError(f"expected {stride * height} bytes of RGB data, got {len(rgb)}")
    raw = b"".join(b"\x00" + rgb[y * stride : (y + 1) * stride] for y in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 6))
        + chunk(b"IEND", b"")
    )


def bgra_to_rgb(bgra: bytes) -> bytes:
    rgb = bytearray(len(bgra) // 4 * 3)
    rgb[0::3] = bgra[2::4]
    rgb[1::3] = bgra[1::4]
    rgb[2::3] = bgra[0::4]
    return bytes(rgb)


@lru_cache(maxsize=1)
def _win32() -> SimpleNamespace:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    def fn(dll, name, restype, *argtypes):
        func = getattr(dll, name)
        func.restype = restype
        func.argtypes = argtypes
        return func

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD),
            ("biWidth", wintypes.LONG),
            ("biHeight", wintypes.LONG),
            ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", wintypes.LONG),
            ("biYPelsPerMeter", wintypes.LONG),
            ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    api = SimpleNamespace(
        ctypes=ctypes,
        wintypes=wintypes,
        BITMAPINFOHEADER=BITMAPINFOHEADER,
        EnumWindowsProc=enum_proc,
        EnumWindows=fn(user32, "EnumWindows", wintypes.BOOL, enum_proc, wintypes.LPARAM),
        IsWindowVisible=fn(user32, "IsWindowVisible", wintypes.BOOL, wintypes.HWND),
        IsIconic=fn(user32, "IsIconic", wintypes.BOOL, wintypes.HWND),
        ShowWindow=fn(user32, "ShowWindow", wintypes.BOOL, wintypes.HWND, ctypes.c_int),
        GetWindowTextW=fn(user32, "GetWindowTextW", ctypes.c_int, wintypes.HWND, wintypes.LPWSTR, ctypes.c_int),
        GetWindowThreadProcessId=fn(
            user32, "GetWindowThreadProcessId", wintypes.DWORD, wintypes.HWND, ctypes.POINTER(wintypes.DWORD)
        ),
        GetClientRect=fn(user32, "GetClientRect", wintypes.BOOL, wintypes.HWND, ctypes.POINTER(wintypes.RECT)),
        GetDC=fn(user32, "GetDC", wintypes.HDC, wintypes.HWND),
        ReleaseDC=fn(user32, "ReleaseDC", ctypes.c_int, wintypes.HWND, wintypes.HDC),
        PrintWindow=fn(user32, "PrintWindow", wintypes.BOOL, wintypes.HWND, wintypes.HDC, wintypes.UINT),
        CreateCompatibleDC=fn(gdi32, "CreateCompatibleDC", wintypes.HDC, wintypes.HDC),
        CreateCompatibleBitmap=fn(
            gdi32, "CreateCompatibleBitmap", wintypes.HBITMAP, wintypes.HDC, ctypes.c_int, ctypes.c_int
        ),
        SelectObject=fn(gdi32, "SelectObject", wintypes.HGDIOBJ, wintypes.HDC, wintypes.HGDIOBJ),
        DeleteObject=fn(gdi32, "DeleteObject", wintypes.BOOL, wintypes.HGDIOBJ),
        DeleteDC=fn(gdi32, "DeleteDC", wintypes.BOOL, wintypes.HDC),
        BitBlt=fn(
            gdi32, "BitBlt", wintypes.BOOL,
            wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD,
        ),
        StretchBlt=fn(
            gdi32, "StretchBlt", wintypes.BOOL,
            wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.DWORD,
        ),
        SetStretchBltMode=fn(gdi32, "SetStretchBltMode", ctypes.c_int, wintypes.HDC, ctypes.c_int),
        SetBrushOrgEx=fn(gdi32, "SetBrushOrgEx", wintypes.BOOL, wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.LPVOID),
        GetDIBits=fn(
            gdi32, "GetDIBits", ctypes.c_int,
            wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
            wintypes.LPVOID, ctypes.POINTER(BITMAPINFOHEADER), wintypes.UINT,
        ),
        OpenProcess=fn(kernel32, "OpenProcess", wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD),
        CloseHandle=fn(kernel32, "CloseHandle", wintypes.BOOL, wintypes.HANDLE),
        QueryFullProcessImageNameW=fn(
            kernel32, "QueryFullProcessImageNameW", wintypes.BOOL,
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
        ),
        SetThreadDpiAwarenessContext=None,
    )
    try:
        api.SetThreadDpiAwarenessContext = fn(
            user32, "SetThreadDpiAwarenessContext", ctypes.c_void_p, ctypes.c_void_p
        )
    except AttributeError:  # Windows older than 10 1607
        pass
    return api


def _process_image_name(api: SimpleNamespace, pid: int) -> str:
    handle = api.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = api.wintypes.DWORD(1024)
        buf = api.ctypes.create_unicode_buffer(size.value)
        if api.QueryFullProcessImageNameW(handle, 0, buf, api.ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        api.CloseHandle(handle)


def _client_size(api: SimpleNamespace, hwnd: int) -> tuple[int, int]:
    rect = api.wintypes.RECT()
    if not api.GetClientRect(hwnd, api.ctypes.byref(rect)):
        return 0, 0
    return rect.right - rect.left, rect.bottom - rect.top


def _find_game_window(api: SimpleNamespace) -> tuple[int, str] | None:
    """Return (hwnd, exe path) for the main OpenRCT2 window, if one is visible."""
    candidates: list[tuple[int, int, int, str]] = []

    def visit(hwnd, _lparam):
        if not api.IsWindowVisible(hwnd):
            return True
        title_buf = api.ctypes.create_unicode_buffer(256)
        api.GetWindowTextW(hwnd, title_buf, 256)
        pid = api.wintypes.DWORD()
        api.GetWindowThreadProcessId(hwnd, api.ctypes.byref(pid))
        exe = _process_image_name(api, pid.value)
        width, height = _client_size(api, hwnd)  # 0x0 while minimized
        if Path(exe).name.lower() in _GAME_EXE_NAMES:
            candidates.append((0, -(width * height), hwnd, exe))
        elif title_buf.value == _WINDOW_TITLE:
            candidates.append((1, -(width * height), hwnd, exe))
        return True

    api.EnumWindows(api.EnumWindowsProc(visit), 0)
    if not candidates:
        return None
    _, _, hwnd, exe = min(candidates)
    return hwnd, exe


def capture_window_png(
    path: Path, *, max_width: int, bring_to_front: bool, crop: float = 1.0
) -> tuple[Path, dict]:
    """Capture the game window without taking focus from the user's terminal.

    bring_to_front only matters when the game is minimized (fullscreen OpenRCT2
    minimizes itself on focus loss by default): the window is shown without
    being activated, captured, then minimized again.
    """
    api = _win32()
    previous_dpi = None
    if api.SetThreadDpiAwarenessContext is not None:
        # Measure and capture in physical pixels on scaled (e.g. 150%) displays.
        previous_dpi = api.SetThreadDpiAwarenessContext(_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
    try:
        found = _find_game_window(api)
        if found is None:
            raise VisionCaptureError("OpenRCT2 window not found. Is the game running (not headless)?")
        hwnd, exe = found
        if not api.IsIconic(hwnd):
            return _capture(api, hwnd, exe, path, max_width=max_width, crop=crop)
        if not bring_to_front:
            raise VisionCaptureError(
                "OpenRCT2 is minimized; retry with bring_to_front=true. (Fullscreen OpenRCT2 minimizes "
                "when it loses focus unless 'Minimise fullscreen on focus loss' is off in Options > Display.)"
            )
        api.ShowWindow(hwnd, _SW_SHOWNOACTIVATE)
        try:
            time.sleep(0.5)  # let the game repaint at full size
            image_path, meta = _capture(api, hwnd, exe, path, max_width=max_width, crop=crop)
        finally:
            api.ShowWindow(hwnd, _SW_SHOWMINNOACTIVE)
        meta["restored_from_minimized"] = True
        return image_path, meta
    finally:
        if previous_dpi:
            api.SetThreadDpiAwarenessContext(previous_dpi)


def crop_rect(width: int, height: int, crop: float) -> tuple[int, int, int, int]:
    """Centred (x, y, w, h) covering ``crop`` of each window dimension (0 < crop <= 1)."""
    crop = min(1.0, max(0.05, crop))
    cw, ch = max(1, round(width * crop)), max(1, round(height * crop))
    return (width - cw) // 2, (height - ch) // 2, cw, ch


def _capture(
    api: SimpleNamespace, hwnd: int, exe: str, path: Path, *, max_width: int, crop: float = 1.0
) -> tuple[Path, dict]:
    width, height = _client_size(api, hwnd)
    if width <= 0 or height <= 0:
        raise VisionCaptureError("OpenRCT2 window has no drawable area.")
    # A centre crop keeps full-resolution detail on large (e.g. 4K) windows.
    cx, cy, cw, ch = crop_rect(width, height, crop)
    if max_width > 0 and cw > max_width:
        out_w, out_h = max_width, max(1, round(ch * max_width / cw))
    else:
        out_w, out_h = cw, ch

    window_dc = api.GetDC(hwnd)
    if not window_dc:
        raise VisionCaptureError("Could not get the OpenRCT2 window device context.")
    src_dc = dst_dc = src_bmp = dst_bmp = None
    try:
        src_dc = api.CreateCompatibleDC(window_dc)
        src_bmp = api.CreateCompatibleBitmap(window_dc, width, height)
        if not src_dc or not src_bmp:
            raise VisionCaptureError("Could not allocate a capture bitmap.")
        src_old = api.SelectObject(src_dc, src_bmp)
        method = "printwindow"
        if not api.PrintWindow(hwnd, src_dc, _PW_CLIENTONLY | _PW_RENDERFULLCONTENT):
            method = "bitblt"  # only correct if the window is unobscured
            api.BitBlt(src_dc, 0, 0, width, height, window_dc, 0, 0, _SRCCOPY)

        if (out_w, out_h) == (width, height) and (cx, cy) == (0, 0):
            api.SelectObject(src_dc, src_old)
            read_dc, read_bmp = src_dc, src_bmp
        else:
            dst_dc = api.CreateCompatibleDC(window_dc)
            dst_bmp = api.CreateCompatibleBitmap(window_dc, out_w, out_h)
            if not dst_dc or not dst_bmp:
                raise VisionCaptureError("Could not allocate a resize bitmap.")
            dst_old = api.SelectObject(dst_dc, dst_bmp)
            api.SetStretchBltMode(dst_dc, _HALFTONE)
            api.SetBrushOrgEx(dst_dc, 0, 0, None)
            api.StretchBlt(dst_dc, 0, 0, out_w, out_h, src_dc, cx, cy, cw, ch, _SRCCOPY)
            api.SelectObject(dst_dc, dst_old)
            api.SelectObject(src_dc, src_old)
            read_dc, read_bmp = dst_dc, dst_bmp

        header = api.BITMAPINFOHEADER()
        header.biSize = api.ctypes.sizeof(api.BITMAPINFOHEADER)
        header.biWidth = out_w
        header.biHeight = -out_h  # negative: top-down rows
        header.biPlanes = 1
        header.biBitCount = 32
        header.biCompression = _BI_RGB
        pixels = api.ctypes.create_string_buffer(out_w * out_h * 4)
        rows = api.GetDIBits(read_dc, read_bmp, 0, out_h, pixels, api.ctypes.byref(header), _DIB_RGB_COLORS)
        if rows != out_h:
            raise VisionCaptureError("Could not read pixels from the capture bitmap.")
    finally:
        # Delete DCs first so no bitmap is still selected when it is deleted.
        for dc in (src_dc, dst_dc):
            if dc:
                api.DeleteDC(dc)
        for bmp in (src_bmp, dst_bmp):
            if bmp:
                api.DeleteObject(bmp)
        api.ReleaseDC(hwnd, window_dc)

    rgb = bgra_to_rgb(pixels.raw)
    path.write_bytes(encode_png_rgb(out_w, out_h, rgb))
    meta = {
        "method": method,
        "window": {"hwnd": hwnd, "exe": exe, "width": width, "height": height},
        "image": {"width": out_w, "height": out_h},
        "crop": round(crop, 3),
        "path": str(path),
        "bytes": path.stat().st_size,
    }
    if not rgb.strip(b"\x00"):
        meta["note"] = "Captured image is entirely black; the window may still be loading."
    return path, meta
