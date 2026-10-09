"""Windows Desktop Acrylic with an opaque fallback, using public DWM APIs."""
import os


def apply_frosted_glass(root, background):
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        class Margins(ctypes.Structure):
            _fields_ = [(name, ctypes.c_int) for name in ("left", "right", "top", "bottom")]

        user32, dwm = ctypes.windll.user32, ctypes.windll.dwmapi
        user32.GetParent.argtypes = [wintypes.HWND]
        user32.GetParent.restype = wintypes.HWND
        hwnd = user32.GetParent(root.winfo_id())
        dwm.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        dwm.DwmExtendFrameIntoClientArea.argtypes = [wintypes.HWND, ctypes.POINTER(Margins)]
        light, rounded, acrylic = ctypes.c_int(1), ctypes.c_int(2), ctypes.c_int(3)
        dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(light), 4)
        dwm.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(rounded), 4)
        # DWMWA_SYSTEMBACKDROP_TYPE = 38, DWMSBT_TRANSIENTWINDOW = 3.
        result = dwm.DwmSetWindowAttribute(hwnd, 38, ctypes.byref(acrylic), 4)
        if result != 0:
            return False
        margins = Margins(-1, -1, -1, -1)
        if dwm.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(margins)) != 0:
            return False

        # Legacy GDI pixels have no alpha channel. Composite the two content
        # surfaces separately, preserving readable text above the Acrylic backdrop.
        get_style = user32.GetWindowLongPtrW if ctypes.sizeof(ctypes.c_void_p) == 8 else user32.GetWindowLongW
        set_style = user32.SetWindowLongPtrW if ctypes.sizeof(ctypes.c_void_p) == 8 else user32.SetWindowLongW
        get_style.argtypes = [wintypes.HWND, ctypes.c_int]
        get_style.restype = ctypes.c_ssize_t
        set_style.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        set_style.restype = ctypes.c_ssize_t
        user32.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.DWORD, wintypes.BYTE, wintypes.DWORD]
        layered = []
        for surface in root.winfo_children():
            if surface.winfo_class() != "Frame":
                continue
            ident = surface.winfo_id()
            old_style = get_style(ident, -20)
            set_style(ident, -20, old_style | 0x00080000)
            if not user32.SetLayeredWindowAttributes(ident, 0, 232, 2):
                set_style(ident, -20, old_style)
                for previous, style in layered:
                    set_style(previous, -20, style)
                dwm.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(Margins(0, 0, 0, 0)))
                none = ctypes.c_int(1)
                dwm.DwmSetWindowAttribute(hwnd, 38, ctypes.byref(none), 4)
                return False
            layered.append((ident, old_style))
        root.configure(bg="#000000")
        user32.RedrawWindow(hwnd, None, None, 0x0185)
        return True
    except (AttributeError, OSError):
        return False
