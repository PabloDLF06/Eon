"""Interruptor de emergencia inmutable de EON (spec 4.4).

Este módulo es la última línea de defensa de Pablo y por eso cumple reglas que
el resto del proyecto no cumple:

1. **Sólo la librería estándar.** Ni PyQt, ni numpy, ni ``requests``: si un
   entorno de ejecución está roto, el kill switch tiene que seguir funcionando.
2. **Verificación de integridad.** El SHA-256 de este archivo se comprueba al
   arrancar. Si no coincide, EON se niega a iniciar. La línea base vive en dos
   sitios (``config.KILLSWITCH_SHA256`` y ``assets/integrity/killswitch.sha256``)
   y sólo la actualiza una persona, ejecutando a mano
   ``python tools/attest_killswitch.py``.
3. **Prohibición de auto-modificación.** :func:`assert_writable_path` bloquea
   cualquier escritura sobre ``safety/``; el motor de auto-programación la llama
   antes de tocar disco y no puede desactivarla (no puede reescribir este
   archivo, precisamente por el punto 2).

Disparadores: ``Ctrl+Shift+Space`` global (gancho de teclado de Win32 o
``pynput``) y sacudida violenta del ratón (>1500 px en <=300 ms). Al saltar,
EON suelta todos los botones y teclas que haya dejado pulsados, congela los
hilos de automatización y devuelve el control absoluto del hardware.
"""

from __future__ import annotations

import hashlib
import os
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "SHA256_CHUNK",
    "EonPaused",
    "IntegrityReport",
    "KillSwitch",
    "assert_writable_path",
    "compute_file_sha256",
    "killswitch_path",
    "protected_prefixes",
    "verify_integrity",
]

SHA256_CHUNK = 128 * 1024

#: Cualquier ruta que empiece por uno de estos prefijos es intocable.
PROTECTED_PREFIXES: tuple[str, ...] = (
    "safety" + os.sep,
    "safety/",
    ".git" + os.sep,
    ".git/",
    "config.py",
    "install.bat",
    "start.bat",
    "requirements.txt",
    "requirements-core.txt",
)

_ROOT = Path(__file__).resolve().parents[1]


def killswitch_path() -> Path:
    """Ruta absoluta de este archivo (la que se ha de hashear)."""
    return Path(__file__).resolve()


def protected_prefixes() -> tuple[str, ...]:
    return PROTECTED_PREFIXES


def compute_file_sha256(path: Path) -> str:
    """SHA-256 en streaming: no carga el archivo entero en memoria."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(SHA256_CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def _baseline_candidates(root: Path | None = None) -> list[str]:
    """Líneas base aceptadas: la constante de ``config`` y el archivo de attest.

    Con ``root`` se busca el archivo de attest bajo esa carpeta (usado por los
    tests de integridad); la constante de ``config`` siempre cuenta.
    """
    out: list[str] = []
    try:  # config puede no ser importable en un arranque roto: nunca lanzar
        import config

        value = str(getattr(config, "KILLSWITCH_SHA256", "") or "").strip().lower()
        if value:
            out.append(value)
    except Exception:
        pass
    if root is not None:
        baseline = Path(root) / "assets" / "integrity" / "killswitch.sha256"
    else:
        try:
            import config

            baseline = Path(getattr(config, "KILLSWITCH_BASELINE_FILE", _ROOT / "assets" / "integrity" / "killswitch.sha256"))
        except Exception:
            baseline = _ROOT / "assets" / "integrity" / "killswitch.sha256"
    try:
        if baseline.exists():
            text = baseline.read_text(encoding="utf-8").strip().lower()
            # formato: <hash>  safety/killswitch.py  (como sha256sum)
            token = text.split()[0] if text else ""
            if token:
                out.append(token)
    except OSError:
        pass
    return out


@dataclass(frozen=True)
class IntegrityReport:
    """Resultado de la verificación del kill switch."""

    ok: bool
    actual: str
    expected: Sequence[str] = ()
    reason: str = ""
    skipped: bool = False

    def summary(self) -> str:
        if self.skipped:
            return f"integridad no verificada ({self.reason})"
        if self.ok:
            return f"kill switch verificado ({self.actual[:12]}…)"
        return f"INTEGRIDAD ROTA: {self.reason}"


def verify_integrity(root: Path | None = None, enforce: bool | None = None) -> IntegrityReport:
    """Compara el hash de ``safety/killswitch.py`` con la línea base.

    Reglas:
    * Sin línea base registrada -> ``ok=True, skipped=True``: primer arranque o
      checkout sin ``assets/integrity``; se avisa pero no se bloquea.
    * Con línea base y hash distinto -> ``ok=False``. El llamador debe negarse
      a arrancar salvo que la variable de entorno de desarrollo lo exima.
    """
    try:
        import config

        root = root or _ROOT
        enforce = bool(getattr(config, "ENFORCE_KILLSWITCH_INTEGRITY", True)) if enforce is None else enforce
        override = os.environ.get(getattr(config, "ALLOW_KILLSWITCH_OVERRIDE", "EON_DEV_SKIP_KILLSWITCH_HASH"), "")
    except Exception:  # pragma: no cover
        enforce = True if enforce is None else enforce
        override = os.environ.get("EON_DEV_SKIP_KILLSWITCH_HASH", "")

    path = (root / "safety" / "killswitch.py") if root else killswitch_path()
    try:
        actual = compute_file_sha256(Path(path))
    except OSError as exc:
        return IntegrityReport(ok=False, actual="", reason=f"no se puede leer {path}: {exc}")

    expected = _baseline_candidates(Path(root) if root else None)
    if not expected:
        return IntegrityReport(ok=True, actual=actual, expected=(), reason="sin línea base", skipped=True)
    if actual in expected:
        return IntegrityReport(ok=True, actual=actual, expected=expected, reason="coincide")
    if override and not enforce:
        return IntegrityReport(ok=True, actual=actual, expected=expected, reason="hash distinto (eximido por EON_DEV_…)", skipped=True)
    if override:
        return IntegrityReport(
            ok=True,
            actual=actual,
            expected=expected,
            reason="hash distinto; eximido explícitamente con EON_DEV_SKIP_KILLSWITCH_HASH",
            skipped=True,
        )
    return IntegrityReport(
        ok=False,
        actual=actual,
        expected=expected,
        reason="el archivo safety/killswitch.py ha cambiado respecto a la línea base",
    )


def assert_writable_path(target: Path | str, root: Path | None = None) -> Path:
    """Comprueba que ``target`` se puede escribir; lanza si está protegido.

    Se llama antes de cualquier escritura del motor de auto-programación o de la
    fábrica de apps. Compara rutas **resueltas** para que ``safety/../safety`` o
    un enlace simbólico no burlen la lista.
    """
    base = Path(root or _ROOT).resolve()
    path = Path(target)
    if not path.is_absolute():
        path = (base / path)
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(base)
    except ValueError:
        # fuera del repo: se permite, pero nunca sobre el propio kill switch
        if resolved.name == "killswitch.py" and resolved.parent.name == "safety":
            raise EonPaused(f"ruta prohibida: {resolved}") from None
        return path
    parts = relative.parts
    lowered = tuple(p.lower() for p in parts)
    if lowered and lowered[0] in ("safety", ".git"):
        raise EonPaused(f"EON tiene prohibido escribir en {lowered[0]}/: {relative}")
    name = relative.as_posix().lower()
    for prefix in PROTECTED_PREFIXES:
        pref = prefix.lower()
        if name == pref.rstrip("/\\") or name.startswith(pref):
            raise EonPaused(f"ruta protegida: {relative}")
    if "killswitch" in lowered and "safety" in lowered:
        raise EonPaused("el interruptor de emergencia es inmutable")
    return path


class EonPaused(RuntimeError):
    """Se lanza cuando una acción se rechaza porque EON está en pausa/bloqueado.

    Cubre dos casos: el kill switch activado y los intentos de escritura sobre
    rutas protegidas. Así el llamador puede distinguir "no ahora" de "imposible".
    """

    def __init__(self, message: str = "EON en pausa por el interruptor de emergencia") -> None:
        super().__init__(message)
        self.message = message


@dataclass
class _Hook:
    name: str
    callback: Callable[[str], None]
    order: int = 0
    critical: bool = False


class KillSwitch:
    """Coordina el pánico: escucha los disparadores y corta todo lo registrado.

    Ciclo de vida típico en ``main.py``::

        switch = KillSwitch(logger=log)
        switch.register("voice", voice.stop, critical=True)
        switch.register("vision", vision.pause, critical=True)
        switch.start()                # gancho de teclado + watchdog del ratón
        ...
        if switch.should_stop():      # dentro de los bucles de trabajo
            break
    """

    def __init__(
        self,
        combo: Sequence[str] | None = None,
        shake_distance: float | None = None,
        shake_window_ms: float | None = None,
        sample_hz: int | None = None,
        logger=None,
        enable_mouse_watchdog: bool = True,
        enable_keyboard_hook: bool = True,
    ) -> None:
        try:
            import config

            self.combo = tuple(c.lower() for c in (combo or config.KILLSWITCH_COMBO))
            self.shake_distance = float(shake_distance if shake_distance is not None else config.SHAKE_DISTANCE_PX)
            self.shake_window = float(shake_window_ms if shake_window_ms is not None else config.SHAKE_WINDOW_MS) / 1000.0
            self.sample_hz = int(sample_hz or config.SHAKE_SAMPLE_HZ)
        except Exception:  # pragma: no cover - config siempre está
            self.combo = tuple(c.lower() for c in (combo or ("ctrl", "shift", "space")))
            self.shake_distance = float(shake_distance or 1500.0)
            self.shake_window = (shake_window_ms or 300.0) / 1000.0
            self.sample_hz = int(sample_hz or 120)

        self.log = logger or _SilentLogger()
        self.enable_mouse_watchdog = enable_mouse_watchdog
        self.enable_keyboard_hook = enable_keyboard_hook

        self._event = threading.Event()
        self._stop = threading.Event()
        self._hooks: list[_Hook] = []
        self._lock = threading.RLock()
        self._held_keys: set[int] = set()
        self._mouse_buttons: set[str] = set()
        self._samples: deque[tuple[float, float, float]] = deque(maxlen=max(8, self.sample_hz * 2))
        self._engaged_at: float | None = None
        self._reason: str = ""
        self._threads: list[threading.Thread] = []
        self._hook_thread: threading.Thread | None = None
        self._watchdog_thread: threading.Thread | None = None
        self._hook_handle: int = 0
        self._hook_proc = None  # referencia viva: Win32 la necesita
        self._engagements = 0
        self._actuator_active = False

    # ------------------------------------------------------------------ alta --
    def register(self, name: str, callback: Callable[[str], None], order: int = 0, critical: bool = False) -> None:
        """Registra un cortacircuitos. ``critical=True`` no puede fallar en silencio."""
        with self._lock:
            self._hooks = [hook for hook in self._hooks if hook.name != name]
            self._hooks.append(_Hook(name=name, callback=callback, order=order, critical=critical))
            self._hooks.sort(key=lambda hook: hook.order)

    def unregister(self, name: str) -> None:
        with self._lock:
            self._hooks = [hook for hook in self._hooks if hook.name != name]

    def start(self) -> bool:
        """Arranca el gancho de teclado global y el watchdog del ratón.

        Devuelve ``True`` si al menos un disparador quedó activo: sin ninguno,
        EON sigue funcionando pero el registro lo advierte como riesgo.
        """
        if self.enable_keyboard_hook:
            self._hook_thread = threading.Thread(target=self._keyboard_loop, name="eon-killswitch-key", daemon=True)
            self._hook_thread.start()
        if self.enable_mouse_watchdog:
            self._watchdog_thread = threading.Thread(target=self._mouse_loop, name="eon-killswitch-mouse", daemon=True)
            self._watchdog_thread.start()
        return bool(self._hook_thread or self._watchdog_thread)

    def stop(self) -> None:
        """Desmonta los hilos (al cerrar la aplicación)."""
        self._stop.set()
        if self._watchdog_thread and self._watchdog_thread.is_alive():
            self._watchdog_thread.join(timeout=1.5)
        if self._hook_thread and self._hook_thread.is_alive():
            self._hook_thread.join(timeout=1.5)

    # ------------------------------------------------------------------ estado --
    @property
    def engaged(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str:
        return self._reason

    @property
    def engagements(self) -> int:
        return self._engagements

    def should_stop(self) -> bool:
        """Los hilos de trabajo preguntan esto en cada iteración."""
        return self._event.is_set() or self._stop.is_set()

    def guard(self) -> None:
        """Lanza :class:`EonPaused` si el sistema está en pausa."""
        if self._event.is_set():
            raise EonPaused(f"EON en pausa ({self._reason or 'kill switch'})")

    def wait_resume(self, timeout: float | None = None) -> bool:
        """Espera a que el usuario libere el kill switch.

        ``threading.Event.wait`` espera a que el evento se *ponga*, pero aquí
        estar en pausa es justo tenerlo puesto: hay que vigilar el vaciado a
        mano. Devuelve ``True`` si se puede reanudar, ``False`` por timeout.
        """
        if not self._event.is_set():
            return True
        deadline = time.monotonic() + (float(timeout) if timeout else float("inf"))
        while self._event.is_set():
            if time.monotonic() > deadline:
                return False
            time.sleep(0.05)
        return True

    def note_actuator_active(self, active: bool) -> None:
        """Marca que el motor de visión está tomando el control del ratón.

        Mientras esté activo se ignora el sacudón (el propio movimiento
        sintético no debe disparar el pánico en bucle).
        """
        self._actuator_active = bool(active)

    # -------------------------------------------------------------- entrada --
    def note_key_down(self, vk: int) -> None:
        with self._lock:
            self._held_keys.add(int(vk))

    def note_key_up(self, vk: int) -> None:
        with self._lock:
            self._held_keys.discard(int(vk))

    def note_button(self, button: str, down: bool) -> None:
        with self._lock:
            (self._mouse_buttons.add if down else self._mouse_buttons.discard)(str(button))

    def held_inputs(self) -> tuple[set[int], set[str]]:
        with self._lock:
            return set(self._held_keys), set(self._mouse_buttons)

    # ---------------------------------------------------------------- pánico --
    def engage(self, reason: str = "manual") -> int:
        """Ejecuta la secuencia de parada. Idempotente mientras esté activado."""
        with self._lock:
            if self._event.is_set():
                return 0
            self._event.set()
            self._engaged_at = time.monotonic()
            self._reason = reason
            self._engagements += 1
            hooks = list(self._hooks)
        self.log.warning("KILL SWITCH activado (%s)", reason)
        failures = 0
        for hook in hooks:
            try:
                hook.callback(reason)
            except Exception as exc:  # un cortocircuitos roto no impide el resto
                failures += 1
                self.log.error("hook %s falló: %s", hook.name, exc)
                if hook.critical:
                    self._hard_release_inputs()
        self._hard_release_inputs()
        return failures

    def release(self, reason: str = "recuperado") -> bool:
        """Devuelve el control. Lo llama el usuario (o el propio hook de UI)."""
        with self._lock:
            if not self._event.is_set():
                return False
            self._event.clear()
            duration = (time.monotonic() - (self._engaged_at or time.monotonic()))
            self._reason = ""
        self.log.warning("KILL SWITCH liberado tras %.1f s (%s)", duration, reason)
        return True

    def toggle(self, reason: str = "combo") -> bool:
        """Alterna el estado. Devuelve ``True`` si al final queda activado."""
        if self._event.is_set():
            self.release(f"{reason} (liberado)")
            return False
        self.engage(reason)
        return True

    # ------------------------------------------------------------- hard inputs --
    def _hard_release_inputs(self) -> None:
        """Suelta teclas y botones que EON haya dejado pulsados (Win32 SendInput)."""
        with self._lock:
            keys = sorted(self._held_keys)
            buttons = sorted(self._mouse_buttons)
            self._held_keys.clear()
            self._mouse_buttons.clear()
        if not keys and not buttons:
            return
        if sys.platform != "win32":
            self._release_with_pyautogui(keys, buttons)
            return
        try:  # pragma: no cover - sólo Windows
            self._release_with_sendinput(keys, buttons)
        except Exception as exc:
            self.log.debug("SendInput no disponible (%s); probando pyautogui", exc)
            self._release_with_pyautogui(keys, buttons)

    def _release_with_sendinput(self, keys: Iterable[int], buttons: Iterable[str]) -> None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
        KEYEVENTF_KEYUP = 0x0002
        # banderas "soltar botón" de la SDK de Windows
        flags = {"left": 0x0004, "right": 0x0010, "middle": 0x0040}

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [
                ("dx", wintypes.LONG),
                ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
            ]

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [
                ("wVk", wintypes.WORD),
                ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
            ]

        class INPUT(ctypes.Structure):
            class _I(ctypes.Union):
                _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]

            _anonymous_ = ("i",)
            _fields_ = [("type", wintypes.DWORD), ("i", _I)]

        sent = 0
        for button in buttons:
            flag = flags.get(str(button).lower())
            if not flag:
                continue
            payload = INPUT(type=INPUT_MOUSE)
            payload.mi = MOUSEINPUT(0, 0, 0, flag, 0, None)
            sent += int(user32.SendInput(1, ctypes.byref(payload), ctypes.sizeof(INPUT)))
        for vk in keys:
            payload = INPUT(type=INPUT_KEYBOARD)
            payload.ki = KEYBDINPUT(wintypes.WORD(vk), 0, KEYEVENTF_KEYUP, 0, None)
            payload.ki.dwFlags = KEYEVENTF_KEYUP
            sent += int(user32.SendInput(1, ctypes.byref(payload), ctypes.sizeof(INPUT)))
        if sent:
            self.log.debug("SendInput liberó %d dispositivos", sent)

    def _release_with_pyautogui(self, keys: Iterable[int], buttons: Iterable[str]) -> None:
        try:  # pragma: no cover - depende de tener pyautogui instalado
            import pyautogui

            for button in buttons:
                if str(button).lower() in ("left", "right", "middle"):
                    pyautogui.mouseUp(button=str(button).lower())
            virtual = {
                0x11: "ctrl",
                0x10: "shift",
                0x12: "alt",
                0x5B: "win",
                0x5C: "win",
            }
            for vk in keys:
                name = virtual.get(int(vk))
                if name:
                    pyautogui.keyUp(name)
        except Exception as exc:
            self.log.debug("no se pudieron liberar las entradas: %s", exc)

    # ------------------------------------------------------- disparadores ------
    def _mouse_loop(self) -> None:
        """Watchdog: distancia euclídea acumulada dentro de la ventana temporal."""
        interval = 1.0 / max(10, self.sample_hz)
        while not self._stop.wait(interval):
            point = self._cursor_position()
            if point is None:
                continue
            now = time.monotonic()
            self._samples.append((now, point[0], point[1]))
            self._trim(now)
            if self._actuator_active or self._event.is_set():
                continue
            if self._shake_detected():
                self.engage("sacudida violenta del ratón")

    def _trim(self, now: float) -> None:
        cutoff = now - max(0.15, self.shake_window * 2.0)
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()

    def _shake_detected(self) -> bool:
        """Devuelve ``True`` si se han recorrido >umbral px en <=ventana ms."""
        window = self.shake_window
        samples = list(self._samples)
        if len(samples) < 3:
            return False
        best = 0.0
        start_index = 0
        for index in range(1, len(samples)):
            while samples[index][0] - samples[start_index][0] > window:
                start_index += 1
            path = 0.0
            for i in range(start_index + 1, index + 1):
                path += ((samples[i][1] - samples[i - 1][1]) ** 2 + (samples[i][2] - samples[i - 1][2]) ** 2) ** 0.5
            best = max(best, path)
        return best >= self.shake_distance

    def _cursor_position(self) -> tuple[float, float] | None:
        if sys.platform == "win32":
            try:  # pragma: no cover - sólo Windows
                import ctypes
                from ctypes import wintypes

                point = wintypes.POINT()
                if ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
                    return float(point.x), float(point.y)
                return None
            except Exception:
                return None
        try:  # pragma: no cover - entorno no Windows
            import pyautogui

            pos = pyautogui.position()
            return float(pos.x), float(pos.y)
        except Exception:
            return None

    def _keyboard_loop(self) -> None:
        """Hilo de escucha del combo global: Win32 de bajo nivel, si no, pynput."""
        if sys.platform == "win32" and self._run_win32_hook():  # pragma: no cover
            return
        self._run_pynput_hook()

    def _combo_pressed(self, modifiers: set[str], key: str) -> bool:
        """True si las teclas pulsadas contienen exactamente el combo."""
        wanted = set(self.combo)
        key = (key or "").lower()
        pressed = set(modifiers) | {key}
        return wanted.issubset(pressed)

    def _run_win32_hook(self) -> bool:  # pragma: no cover - Windows only
        """Gancho WH_KEYBOARD_LL con su propia bomba de mensajes."""
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32

            class KBDLLHOOKSTRUCT(ctypes.Structure):
                _fields_ = [
                    ("vkCode", wintypes.DWORD),
                    ("scanCode", wintypes.DWORD),
                    ("flags", wintypes.DWORD),
                    ("time", wintypes.DWORD),
                    ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
                ]

            WH_KEYBOARD_LL = 13
            WM_KEYDOWN, WM_SYSKEYDOWN, WM_KEYUP, WM_SYSKEYUP = 0x0100, 0x0104, 0x0101, 0x0105
            MODIFIER_VKS = {0x10: "shift", 0x11: "ctrl", 0x12: "alt", 0x5B: "win", 0x5C: "win"}
            NAMED = {0x20: "space", 0x09: "tab", 0x0D: "enter", 0x1B: "esc"}

            held: set[str] = set()
            hook_ref = {}

            LRESULT = ctypes.c_long
            HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

            def callback(nCode, wParam, lParam):
                try:
                    if nCode == 0:
                        info = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                        vk = int(info.vkCode)
                        name = MODIFIER_VKS.get(vk) or NAMED.get(vk) or chr(vk).lower() if vk < 256 else ""
                        if wParam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                            with self._lock:
                                self._held_keys.add(vk)
                            if name:
                                held.add(name)
                            if self._combo_pressed({m for m in held if m != name}, name) or self._combo_pressed(set(held), name):
                                self.engage(f"combo {'+'.join(self.combo)}")
                        elif wParam in (WM_KEYUP, WM_SYSKEYUP):
                            with self._lock:
                                self._held_keys.discard(vk)
                            held.discard(name)
                            if self._event.is_set() and self._combo_pressed(set(held), name):
                                self.release("combo liberado")
                except Exception as exc:
                    self.log.debug("hook de teclado: %s", exc)
                return user32.CallNextHookEx(hook_ref.get("handle", 0), nCode, wParam, lParam)

            proc = HOOKPROC(callback)
            hook_id = user32.SetWindowsHookExW(WH_KEYBOARD_LL, proc, kernel32.GetModuleHandleW(None), 0)
            if not hook_id:
                return False
            hook_ref["handle"] = hook_id
            self._hook_proc = proc  # evitar que el GC libere el callback
            self._hook_handle = int(hook_id)
            msg = wintypes.MSG()
            while not self._stop.is_set():
                got = user32.PeekMessageW(ctypes.byref(msg), 0, 0, 0, 1)  # PM_REMOVE
                if not got:
                    time.sleep(0.008)
                    continue
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            user32.UnhookWindowsHookEx(hook_id)
            return True
        except Exception as exc:
            self.log.debug("gancho Win32 no disponible: %s", exc)
            return False

    def _run_pynput_hook(self) -> None:  # pragma: no cover - depende de pynput
        try:
            from pynput import keyboard
        except Exception:
            self.log.warning(
                "sin gancho de teclado global (falta pynput). Ctrl+Shift+Space seguirá "
                "funcionando dentro de las ventanas de EON, y el sacudón de ratón está activo."
            )
            return

        wanted = set(self.combo)
        held: set[str] = set()

        def normalize(key) -> str:
            name = getattr(key, "name", "") or ""
            if name.startswith("ctrl"):
                return "ctrl"
            if name.startswith("alt"):
                return "alt"
            if name.startswith("cmd"):
                return "win"
            return name or str(getattr(key, "char", "") or "").lower()

        def on_press(key):
            held.add(normalize(key))
            if wanted.issubset(held):
                self.engage(f"combo {'+'.join(self.combo)}")

        def on_release(key):
            name = normalize(key)
            held.discard(name)
            if self._event.is_set() and not wanted.issubset(held):
                self.release("combo liberado")

        listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        listener.daemon = True
        listener.start()
        while not self._stop.wait(0.25):
            if not listener.running:
                break
        try:
            listener.stop()
        except Exception:
            pass


class _SilentLogger:
    """Logger mínimo para poder usar el módulo sin configurar nada."""

    def warning(self, message: str, *args) -> None:
        self._emit("WARNING", message, args)

    def error(self, message: str, *args) -> None:
        self._emit("ERROR", message, args)

    def debug(self, message: str, *args) -> None:
        return None

    def info(self, message: str, *args) -> None:
        return None

    @staticmethod
    def _emit(level: str, message: str, args: tuple) -> None:
        text = message % args if args else message
        sys.stderr.write(f"[eon.safety] {level}: {text}\n")


def build_default_killswitch(logger=None) -> KillSwitch:
    """Fábrica con la configuración de ``config.py`` ya aplicada."""
    return KillSwitch(logger=logger)
