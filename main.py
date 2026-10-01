#!/usr/bin/env python3
"""EON — punto de entrada. Arranca núcleo, notch, voz y manos; si algo falta, degrada.

Este archivo es el único que conoce todas las piezas. Su trabajo es:

1. levantar ``config`` (rutas, logs, ajustes del usuario en ``settings.json``);
2. verificar la integridad del interruptor de seguridad antes que nada;
3. construir los subsistemas con tolerancia a fallos (uno roto no tumba nada);
4. coserlos con reglas de negocio en español (qué frase dispara qué);
5. mostrar la GUI si hay PyQt6; si falta o revienta, seguir vivo en consola.

Todo el código Qt vive dentro de :func:`run_gui` con import perezoso, para que
el núcleo funcione en máquinas sin interfaz (pruebas, ``--diagnose``, SSH).

Comandos útiles: ``python main.py --diagnose`` imprime capacidades en JSON;
``--text`` abre un REPL; ``--send "orden"`` habla con la instancia en marcha.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import socket
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:  # doble clic en Windows: el paquete no está instalado
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402  (necesita ROOT en sys.path)

log = logging.getLogger("eon.main")

INSTANCE_PORT = 47905  # control local: --send y pruebas de integración

# --------------------------------------------------------------------------- #
# Intenciones: español primero, modelo después
# --------------------------------------------------------------------------- #

INTENTS: list[tuple[str, re.Pattern[str]]] = [
    ("silence", re.compile(r"\b(c[áa]llate|silencio|para de hablar|no hables)\b", re.I)),
    ("stop_all", re.compile(r"^\s*(para|basta|alto|stop|det[ée]nlo todo)\s*[.!]*\s*$", re.I)),
    ("music_stop", re.compile(r"\b(para|pausa|apaga|silencia)\b[^.]{0,20}\b(m[úu]sica|canci[óo]n|spotify|pista)\b", re.I)),
    ("music", re.compile(r"\b(pon|m[eé]teles?|ponme|reproduce|suelta)\b[^.]{0,25}\b(m[úu]sica|canci[óo]n|loser|tame impala|spotify)\b", re.I)),
    ("look", re.compile(r"\bqu[ée] (hay|ves|pasa)\b|\bdime qu[ée] veo\b|\bdescribe (la |mi )?pantalla\b", re.I)),
    ("self_upgrade", re.compile(r"\b(mej[oaáó]ra(?:te)?|mej[oaáó]rate|a[nñ]ade\b[^.]{0,30}\b(funcionalidad|capacidad|m[óo]dulo)|actualiza tu c[óo]digo|auto-?mejora)\b", re.I)),
    ("app_build", re.compile(r"\b(crea|cr[ée]ame|hazme|haz|construye|genera|programa)\b[^.]{0,60}\b(app|aplicaci[óo]n|programa|software|p[áa]gina web|web|herramienta)\b", re.I)),
    ("automation", re.compile(r"\b(abre|abrir|haz clic|pincha|pulsa|escribe|rellena|busca en|navega|ve a|cierra la|mueve el rat[óo]n|haz scroll|ordena (las |mis )?ventanas)\b", re.I)),
    ("thanks", re.compile(r"\b(gracias|mil gracias|bien hecho|buen[íi]simo)\b", re.I)),
    ("sleep", re.compile(r"\b(duerme|descansa|ap[áa]gate|vete a dormir)\b", re.I)),
    ("greeting", re.compile(r"^\s*(hola|buenos d[íi]as|buenas tardes|buenas noches|qu[ée] tal|c[óo]mo est[áa]s)\b", re.I)),
]


def classify(text: str) -> str:
    """Nombre de la intención detectada, o ``chat`` si hay que pensar con el modelo."""
    cleaned = (text or "").strip()
    if not cleaned:
        return "ignore"
    for name, pattern in INTENTS:
        if pattern.search(cleaned):
            return name
    return "chat"


def chat_system() -> str:
    return (
        "Eres EON, el compañero y mayordomo local de Pablo. Hablas español natural, tuteándole "
        "con respeto (un «señor» de vez en cuando, como un mayordomo). Respondes en una a tres "
        "frases: sin listas, sin markdown, sin emojis. No tienes internet. Si te pide hacer algo "
        "en el ordenador, reconoce la orden; no inventes resultados."
    )


# --------------------------------------------------------------------------- #
# Orquestador — independiente de Qt: la GUI es una oyente más
# --------------------------------------------------------------------------- #


class EonOrchestrator:
    """Monta los sistemas y los cose con reglas. Sus métodos nunca lanzan al exterior."""

    def __init__(self, *, allow_voice: bool = True, headless: bool = False) -> None:
        config.ensure_runtime_dirs()
        overrides = config.load_settings_file()
        if overrides:
            applied = config.apply_overrides(overrides)
            log.info("settings.json aplicado (%d claves: %s)", len(applied), ", ".join(applied[:6]))
        self.headless = headless
        self._start_ts = time.time()
        self._activity = time.time()
        self.media: dict[str, Any] = {}
        self._listeners: list[Callable[[str, dict], None]] = []
        self._workers: list[threading.Thread] = []
        self._lock = threading.RLock()

        # -- seguridad: integridad del kill switch antes de tocar nada --------
        from safety.killswitch import build_default_killswitch, verify_integrity

        self.integrity = verify_integrity(root=ROOT)
        if self.integrity.ok:
            log.info("integridad: %s", self.integrity.summary())
        else:
            log.error("integridad: %s", self.integrity.summary())
        self.killswitch = build_default_killswitch(logger=logging.getLogger("eon.kill"))

        # -- núcleo -------------------------------------------------------------
        from core.event_bus import EventBus

        self.bus = EventBus(logger=log)
        from core.model_router import ModelRouter

        self.router = ModelRouter(bus=self.bus, logger=log)

        self.voice = None
        if allow_voice:
            try:
                from core.voice_engine import VoiceEngine

                self.voice = VoiceEngine(
                    bus=self.bus,
                    killswitch=self.killswitch,
                    on_heard=self.handle_text,
                    on_state=self._on_voice_state,
                    logger=log,
                )
            except Exception as exc:
                log.warning("motor de voz no disponible: %s", exc)

        self.ear = None
        try:
            from core.acoustic_detector import AcousticEar

            self.ear = AcousticEar(mic=self.voice.mic if self.voice else None, on_trigger=self._on_clap, logger=log)
        except Exception as exc:
            log.warning("detector acústico no disponible: %s", exc)

        from core.vision_actuator import VisionActuator

        self.vision = VisionActuator(router=self.router, bus=self.bus, killswitch=self.killswitch, on_state=self._on_vision_state, logger=log)
        from core.self_programmer import SelfProgrammer

        self.programmer = SelfProgrammer(router=self.router, notify=self.say, on_state=self._on_programmer_state, logger=log)
        from core.app_builder import AppBuilder

        self.builder = AppBuilder(router=self.router, notify=self.say, on_state=self._on_builder_state, logger=log)

        # -- kill switch: enganchar todo lo que hay que cortar -----------------
        self.killswitch.register("voice", self._kill_voice, critical=True)
        self.killswitch.register("notch", lambda reason: self._emit("killed", {"reason": reason}), critical=True)
        self.killswitch.register("glow", lambda reason: self._emit("glow-off", {}), order=5)
        self.killswitch.register("media", lambda reason: self._stop_media(), order=8)
        self.killswitch.start()

    # ------------------------------------------------------------------- alta --
    def on_event(self, callback: Callable[[str, dict], None]) -> None:
        """La GUI (o el REPL) se registra como oyente de estados."""
        self._listeners.append(callback)

    def start(self) -> None:
        if self.voice is not None:
            try:
                self.voice.start()
            except Exception as exc:
                log.warning("el motor de voz no arrancó: %s", exc)
                self.voice = None
        if self.ear is not None:
            try:
                self.ear.start()
            except Exception as exc:
                log.info("oído acústico inactivo: %s", exc)
        self._serve_control_socket()
        self._emit("booted", {"seconds": round(time.time() - self._start_ts, 1)})
        log.info(
            "EON en marcha (voz=%s, palmada=%s, ratón=%s)",
            bool(self.voice),
            bool(self.ear),
            self.vision.capabilities()["input_available"],
        )

    def shutdown(self) -> None:
        for closer in (
            self.ear.stop if self.ear else None,
            self.voice.stop if self.voice else None,
            self.builder.stop_servers,
            self.killswitch.stop,
            self.router.shutdown,
        ):
            try:
                if callable(closer):
                    closer()
            except Exception as exc:
                log.debug("cierre parcial: %s", exc)
        log.info("EON se apaga")

    # -------------------------------------------------------------- escucha --
    def handle_text(self, text: str) -> None:
        """Toda orden (micrófono, notch, socket o REPL) entra por aquí. No bloquea."""
        text = (text or "").strip()
        if not text:
            return
        self._activity = time.time()
        intent = classify(text)
        log.info("orden [%s]: %s", intent, text[:120])
        if intent == "ignore":
            return

        # corrección en vuelo: si estoy actuando, cualquier frase reorienta
        if self.vision.busy and intent in ("chat", "automation") and self.vision.request_correction(text):
            self.say("Reoriento la automatización.")
            return

        if intent == "silence":
            if self.voice is not None:
                self.voice.interrupt()
            self._emit("idle-now", {})
            return
        if intent == "stop_all":
            self._emit("state", {"notch": "idle", "status": "todo detenido"})
            self.killswitch.engage("orden de voz")  # corta voz, ratón y glow de golpe
            return
        if intent == "sleep":
            self._emit("state", {"notch": "sleeping"})
            self.say("Descanso. Diga «Eon» y despierto.")
            return
        if intent == "music":
            self._worker(self.play_music)
            return
        if intent == "music_stop":
            self._worker(self._stop_music)
            return
        if intent == "thanks":
            self.say("A su servicio, señor.")
            return
        if intent == "greeting":
            self._worker(self._greet)
            return
        if intent == "look":
            self._worker(self._do_look)
            return
        if intent == "self_upgrade":
            self._worker(lambda: self._do_programmer(text))
            return
        if intent == "app_build":
            self._worker(lambda: self._do_builder(text))
            return
        if intent == "automation":
            self._worker(lambda: self._do_automation(text))
            return
        self._worker(lambda: self._do_chat(text))

    def listen_now(self) -> None:
        """El click del notch pide escucha."""
        if self.voice is not None:
            self.voice.begin_listening(source="notch")

    # -------------------------------------------------------------- acciones --
    def play_music(self) -> None:
        """Pon "Loser": la cascada Spotify -> Comet --app -> navegador (spec 4.4)."""
        if self.ear is None:
            self.say("No tengo lanzador de música en este equipo.")
            return
        result = self.ear.launcher.play()
        media = dict(getattr(config, "MUSIC_CONFIG", {}))
        title = media.get("title", "Loser")
        artist = media.get("artist", "Tame Impala")
        if result.ok:
            self._emit("media", {"title": title, "artist": artist, "source": result.source, "playing": True})
            self.say(f"{title}, de {artist}, por {result.source}.")
        else:
            log.warning("música no disponible: %s", result.detail)
            self.say("No he podido abrir la música; revise que Spotify o Comet estén instalados.")

    def _stop_music(self) -> None:
        if self.ear is None:
            return
        result = self.ear.launcher.stop()
        if result.ok:
            self._stop_media()
            self.say("Música en pausa.")
        else:
            self.say("No hay ninguna canción que yo haya puesto.")

    def _stop_media(self) -> None:
        self.media = {}
        self._emit("media-stop", {})

    def _greet(self) -> None:
        from core.acoustic_detector import greeting_for

        self.say(greeting_for(name=str(getattr(config, "USER_NAME", "Pablo"))))

    def _do_look(self) -> None:
        self._emit("state", {"notch": "thinking", "status": "mirando"})
        try:
            description = self.vision.look()
        except Exception as exc:
            log.warning("mirada fallida: %s", exc)
            description = ""
        if description:
            self._emit("state", {"notch": "idle", "status": _clip(description, 38)})
            self.say(description)
        else:
            self._emit("state", {"notch": "error", "status": "sin visión"})
            self.say("No he podido mirar la pantalla; el modelo de visión no responde.")

    def _do_automation(self, text: str) -> None:
        if self.vision.busy:
            self.say("Ya estoy con otra automatización; hébrela cuando termine.")
            return
        self._emit("state", {"notch": "action", "status": "planificando", "detail": _clip(text, 38)})
        try:
            report = self.vision.run(text)
        except Exception as exc:
            log.exception("automatización abortada")
            self._emit("state", {"notch": "error", "status": "interrumpido", "detail": _clip(str(exc), 38)})
            self.say("He tenido que parar por seguridad.")
            return
        if report.ok:
            self._emit("state", {"notch": "idle", "status": report.summary()})
            self.say(f"Listo: {report.summary()}.")
        else:
            self._emit("state", {"notch": "error", "status": _clip(report.aborted or "fallo", 24), "detail": _clip(report.error, 38)})
            self.say(f"No pude terminar. {report.summary()}.")

    def _do_programmer(self, text: str) -> None:
        if self.programmer.busy:
            self.say("Ya estoy compilando otra mejora; termínela y me la apunto.")
            return
        self._emit("state", {"notch": "thinking", "status": "reprogramándome", "detail": _clip(text, 38)})
        result = self.programmer.implement(text)
        if result.hot_reloaded:
            # el proceso nuevo ya está lanzado: le cedo el sitio
            self.say(result.message())
            threading.Timer(1.5, self._exit_for_restart).start()
            return
        self._emit("state", {"notch": "idle" if result.ok else "error", "status": _clip(result.summary or result.detail, 38)})
        self.say(result.message())

    def _do_builder(self, text: str) -> None:
        if self.builder.busy:
            self.say("Estoy terminando otra aplicación; espídame un momento.")
            return
        self._emit("state", {"notch": "thinking", "status": "fábrica de apps", "detail": _clip(text, 38)})
        result = self.builder.create(text)
        if result.ok:
            self._emit("state", {"notch": "idle", "status": _clip(f"{result.slug}: {len(result.files)} archivos", 38)})
            # la frase de la spec, literal, sólo si de verdad la abrí
            if result.launched:
                self.say("Señor, su aplicación está terminada y abierta en su pantalla.")
            else:
                self.say("Su aplicación está terminada, pero no he podido abrirla sola.")
        else:
            self._emit("state", {"notch": "error", "status": "fábrica detenida", "detail": _clip(result.detail, 38)})
            self.say(f"No pude construir la aplicación. {result.detail or 'Revisa el registro.'}")

    def _do_chat(self, text: str) -> None:
        health = None
        try:
            health = self.router.health(check_models=False)
        except Exception as exc:
            log.debug("health falló: %s", exc)
        if health is not None and not health.reachable:
            self.say("El motor local no responde. Abra Ollama y vuelva en un momento.")
            return
        self._emit("state", {"notch": "thinking", "status": "pensando"})
        buffer: list[str] = []
        last_paint = [0.0]

        def on_token(piece: str) -> None:
            buffer.append(piece)
            now = time.monotonic()
            if now - last_paint[0] > 0.12:
                last_paint[0] = now
                self._emit("state", {"notch": "thinking", "status": "pensando", "detail": _clip("".join(buffer), 38)})

        try:
            result = self.router.stream_chat(
                [{"role": "user", "content": text}],
                system=chat_system(),
                on_token=on_token,
                options={"temperature": 0.55, "num_predict": 340},
            )
        except Exception as exc:
            log.warning("chat abortado: %s", exc)
            self._emit("state", {"notch": "error", "status": "sin modelo", "detail": _clip(str(exc), 38)})
            self.say("Algo me interrumpió al pensar. Repítamelo.")
            return
        answer = (result.text or "").strip()
        if not answer:
            self._emit("idle-now", {})
            return
        self.say(answer)  # speak() bloquea: así el notch muestra "hablando" mientras suena

    # --------------------------------------------------------------- oyentes --
    def _on_voice_state(self, name: str, payload: dict) -> None:
        if name == "wake":
            self._emit("wake", {})
            self._emit("state", {"notch": "listening", "status": "le escucho"})
        elif name == "listening":
            self._emit("state", {"notch": "listening", "status": ""})
        elif name == "processing":
            self._emit("state", {"notch": "thinking", "status": "entendiendo"})
        elif name == "transcript":
            self._emit("state", {"notch": "thinking", "status": "entendido", "detail": _clip(str(payload.get("text", "")), 38)})
        elif name == "amplitude":
            self._emit("energy", {"level": float(payload.get("level", 0.0))})
        elif name in ("silent", "interrupted"):
            self._emit("idle-now", {})

    def _on_vision_state(self, name: str, payload: dict) -> None:
        if name in ("planning", "replanning"):
            detail = payload.get("instruction") or payload.get("correction") or ""
            self._emit("state", {"notch": "action", "status": "mirando la pantalla", "detail": _clip(str(detail), 38)})
        elif name == "planned":
            self._emit("state", {"notch": "action", "status": f"{payload.get('steps', '?')} paso(s)", "detail": _clip(str(payload.get("first", "")), 38)})
        elif name == "action":
            self._emit("state", {"notch": "action", "status": "ejecutando", "detail": _clip(str(payload.get("label", "")), 38)})
        elif name in ("vision-error", "capture-error"):
            self._emit("state", {"notch": "error", "status": "visión", "detail": _clip(str(payload.get("detail", "")), 38)})

    def _on_programmer_state(self, name: str, payload: dict) -> None:
        status = {
            "generating": "escribiendo el código",
            "writing": "tocando disco",
            "testing": "corriendo pruebas",
            "rolling_back": "revirtiendo",
            "done": "listo",
        }.get(name, name)
        self._emit("state", {"notch": "thinking", "status": status, "detail": _clip(str(payload.get("instruction", "")), 34)})

    def _on_builder_state(self, name: str, payload: dict) -> None:
        status = {"thinking": "diseñando la app", "built": "abriendo la app"}.get(name, name)
        detail = payload.get("idea") or payload.get("path") or ""
        self._emit("state", {"notch": "thinking", "status": status, "detail": _clip(str(detail), 34)})

    def _on_clap(self, greeting: str, result: Any) -> None:
        """Doble palmada: saludo según la hora + música + media card (spec 4.4)."""
        self._activity = time.time()
        self._emit("wake", {})  # el personaje abre los ojos de golpe
        if greeting:
            self.say(greeting)
        if result is not None and getattr(result, "ok", False):
            media = dict(getattr(config, "MUSIC_CONFIG", {}))
            self._emit("media", {
                "title": media.get("title", "Loser"),
                "artist": media.get("artist", "Tame Impala"),
                "source": getattr(result, "source", ""),
                "playing": True,
            })

    def _kill_voice(self, reason: str) -> None:
        if self.voice is not None:
            self.voice.interrupt()

    def _emit(self, name: str, payload: dict[str, Any]) -> None:
        if name == "media":
            with self._lock:
                self.media = dict(payload)
        for listener in list(self._listeners):
            try:
                listener(name, payload)
            except Exception as exc:
                log.debug("oyente de eventos falló: %s", exc)

    # ----------------------------------------------------------------- varios --
    def say(self, text: str) -> None:
        """Di algo en voz alta (o por consola si no hay TTS)."""
        text = (text or "").strip()
        if not text:
            return
        if self.voice is not None:
            try:
                self.voice.say(text)
            except Exception as exc:
                log.warning("el TTS falló: %s", exc)
                print(f"[EON dice] {text}", flush=True)
        else:
            print(f"[EON dice] {text}", flush=True)

    def _worker(self, target: Callable[[], None]) -> None:
        def guard() -> None:
            try:
                target()
            except Exception:
                log.exception("tarea en segundo plano falló")

        thread = threading.Thread(target=guard, name="eon-worker", daemon=True)
        self._workers.append(thread)
        thread.start()

    def _serve_control_socket(self) -> None:
        """127.0.0.1:47905 acepta líneas de comando (``--send`` y pruebas)."""
        try:
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(("127.0.0.1", INSTANCE_PORT))
            server.listen(8)
        except OSError:
            return  # otra instancia o puerto reservado: no es fatal, seguimos sin control remoto

        def loop() -> None:
            while True:
                try:
                    client, _addr = server.accept()
                except OSError:
                    return
                with client:
                    client.settimeout(10.0)
                    try:
                        data = client.recv(4096).decode("utf-8", "replace").strip()
                    except OSError:
                        continue
                    if not data:
                        continue
                    low = data.lower()
                    if low == "ping":
                        client.sendall(b"pong\n")
                    elif low == "state":
                        client.sendall((json.dumps(self.status(), ensure_ascii=False) + "\n").encode("utf-8"))
                    elif low == "kill":
                        self.killswitch.toggle("control local")
                        client.sendall(b"toggle\n")
                    else:
                        self.handle_text(data)
                        client.sendall(b"ok\n")

        threading.Thread(target=loop, name="eon-control", daemon=True).start()

    def status(self) -> dict[str, Any]:
        """Diagnóstico completo y serializable (``--diagnose`` y ``--send state``)."""
        out: dict[str, Any] = {
            "uptime_s": round(time.time() - self._start_ts, 1),
            "headless": self.headless,
            "integrity": self.integrity.summary(),
            "killswitch": {"engaged": self.killswitch.engaged, "reason": str(self.killswitch.reason), "engagements": self.killswitch.engagements},
        }
        for label, getter in (
            ("voice", lambda: self.voice.capabilities() if self.voice else {"available": False}),
            ("ear", lambda: {"claps_seen": self.ear.claps_seen, "triggers": self.ear.triggers} if self.ear else {"available": False}),
            ("vision", self.vision.capabilities),
            ("builder", lambda: {"projects": [item["name"] for item in self.builder.list_projects()]}),
        ):
            try:
                out[label] = getter()
            except Exception as exc:
                out[label] = f"indisponible: {exc}"
        try:
            health = self.router.health()
            out["ollama"] = {"reachable": health.reachable, "missing": list(health.missing), "detail": health.human()}
        except Exception as exc:
            out["ollama"] = f"indisponible: {exc}"
        return out

    def _exit_for_restart(self) -> None:
        """Cierre limpio tras un hot-reload: el nuevo proceso ya viene en camino."""
        log.info("cedo el testigo al proceso renovado")
        try:
            self.shutdown()
        finally:
            import os

            os._exit(0)


# --------------------------------------------------------------------------- #
# Utilidades del arranque
# --------------------------------------------------------------------------- #


def _clip(text: str, width: int) -> str:
    text = (text or "").replace("\n", " ").strip()
    return text if len(text) <= width else text[: max(1, width - 1)] + "…"


def instance_is_up(timeout: float = 0.6) -> bool:
    """¿Hay un EON escuchando el puerto de control?"""
    try:
        with socket.create_connection(("127.0.0.1", INSTANCE_PORT), timeout=timeout):
            return True
    except OSError:
        return False


def send_command(text: str, timeout: float = 15.0) -> str:
    try:
        with socket.create_connection(("127.0.0.1", INSTANCE_PORT), timeout=timeout) as client:
            client.sendall(text.encode("utf-8"))
            return client.recv(65536).decode("utf-8", "replace").strip()
    except OSError as exc:
        return f"EON no responde ({exc})"


# --------------------------------------------------------------------------- #
# GUI (PyQt6) — el import de Qt es perezoso y vive aquí dentro
# --------------------------------------------------------------------------- #


def _load_ui_sounds() -> dict[str, Any]:
    """Carga los WAV cortos de ``assets/sounds``; silencio absoluto si no hay multimedia."""
    out: dict[str, Any] = {}
    try:
        from PyQt6.QtCore import QUrl
        from PyQt6.QtMultimedia import QSoundEffect
    except Exception as exc:
        log.debug("sin QSoundEffect (%s): la app será muda y elegante", exc)
        return out
    for name in ("boot", "ack", "alarm"):
        path = ROOT / "assets" / "sounds" / f"{name}.wav"
        if not path.exists():
            continue
        try:
            clip = QSoundEffect()
            clip.setSource(QUrl.fromLocalFile(str(path)))
            clip.setVolume(0.35 if name != "alarm" else 0.6)
            out[name] = clip
        except Exception as exc:
            log.debug("sonido %s no cargó: %s", name, exc)
    return out


def run_gui(orch: EonOrchestrator) -> int:
    """Crea la QApplication, el notch y el glow de pantalla; devuelve el código de salida."""
    try:
        from PyQt6.QtCore import QObject, QTimer, pyqtSignal
        from PyQt6.QtWidgets import QApplication
    except Exception as exc:
        print(f"[EON] Sin PyQt6 no hay interfaz ({exc}). Pruebe: python main.py --text", file=sys.stderr)
        return run_headless(orch)
    try:
        from gui.notch_layout import MediaInfo, NotchState
        from gui.notch_window import NotchWindow
        from gui.screen_glow import ScreenGlow
    except Exception as exc:
        log.exception("los módulos GUI no cargaron")
        print(f"[EON] La interfaz no pudo cargarse ({exc}); modo consola.", file=sys.stderr)
        return run_headless(orch)

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("EON")
    app.setQuitOnLastWindowClosed(False)  # el notch no es una ventana que cerrar

    try:  # icono: el mismo retrato vectorial, congelado (tools/build_assets.py)
        from PyQt6.QtGui import QIcon

        icon_path = ROOT / "assets" / "char" / "eon-idle.png"
        if icon_path.exists():
            app.setWindowIcon(QIcon(str(icon_path)))
    except Exception as exc:
        log.debug("sin icono de aplicación: %s", exc)

    sounds = _load_ui_sounds()

    def _chime(name: str) -> None:
        """Un sonido corto; si no hay multimedia, QApplication.beep sólo para la alarma."""
        clip = sounds.get(name)
        if clip is not None:
            try:
                clip.play()
                return
            except Exception:
                pass
        if name == "alarm":
            try:
                QApplication.beep()
            except Exception:
                pass

    notch = NotchWindow()
    glow = ScreenGlow()

    # Puente hilo-seguro: los workers del núcleo emiten aquí y Qt pinta en su hilo.
    bridge_class = type("EonBridge", (QObject,), {"event": pyqtSignal(object)})
    bridge = bridge_class()

    def _apply(packet: tuple[str, dict]) -> None:
        name, payload = packet
        if name == "state":
            notch.request(
                str(payload.get("notch", "idle")),
                status=str(payload.get("status", "")),
                detail=str(payload.get("detail", "")),
            )
        elif name == "energy":
            notch.set_energy(float(payload.get("level", 0.0)))
        elif name == "wake":
            notch.wake()
            _chime("ack")
        elif name == "media":
            with orch._lock:
                orch.media = dict(payload)
            notch.set_media(
                MediaInfo(
                    title=str(payload.get("title", "")),
                    artist=str(payload.get("artist", "")),
                    source=str(payload.get("source", "")),
                    playing=bool(payload.get("playing", True)),
                )
            )
            notch.request(NotchState.MEDIA)
        elif name == "media-stop":
            with orch._lock:
                orch.media = {}
            notch.clear_media()
        elif name == "idle-now":
            with orch._lock:
                playing = bool(orch.media.get("playing"))
            if not playing:
                notch.request(NotchState.IDLE)
        elif name == "glow":
            if payload.get("off"):
                glow.end()
            else:
                glow.pulse(str(payload.get("mode", "capture")), int(payload.get("duration_ms", 900)))
        elif name == "glow-off":
            glow.end()
        elif name == "killed":
            notch.rest()
            glow.end()
            _chime("alarm")
        elif name == "booted":
            notch.set_status("", "")

    bridge.event.connect(_apply)
    orch.on_event(lambda name, payload: bridge.event.emit((name, dict(payload))))

    # Visor de la isla y órdenes tecleadas
    notch.activated.connect(orch.listen_now)
    notch.commandSubmitted.connect(orch.handle_text)

    class _GlowShim:
        """Adaptador thread-safe: los workers de visión nunca tocan Qt directamente."""

        def pulse(self, mode: str = "capture", duration_ms: int = 900, intensity: float = 1.0) -> None:
            bridge.event.emit(("glow", {"mode": mode, "duration_ms": duration_ms}))

        def begin(self, mode: str = "capture", intensity: float = 1.0, scan: bool | None = None) -> None:
            self.pulse(mode)

        def end(self) -> None:
            bridge.event.emit(("glow", {"off": True}))

        def is_lit(self) -> bool:
            return False

    orch.vision.glow = _GlowShim()  # el borde cian acompaña cada captura y clic

    def _hello() -> None:
        _chime("boot")
        try:
            from core.acoustic_detector import greeting_for

            orch.say(greeting_for(name=str(getattr(config, "USER_NAME", "Pablo"))))
        except Exception as exc:
            log.debug("saludo de bienvenida omitido: %s", exc)

    orch.start()
    notch.show()
    notch.request(NotchState.IDLE, animate=False)
    QTimer.singleShot(900, _hello)

    # el kill switch también se puede soltar desde el notch con su tecla (doble clic)
    try:
        app.aboutToQuit.connect(orch.shutdown)
    except Exception:
        pass
    return int(app.exec())


def run_headless(orch: EonOrchestrator) -> int:
    """Modo consola: REPL en español para probar el núcleo sin GUI."""
    orch.start()
    print("EON en modo consola. Escriba una orden ('estado' para el diagnóstico, 'salir' para terminar).")
    try:
        while True:
            try:
                line = input("usted> ").strip()
            except EOFError:
                break
            if not line:
                continue
            if line.lower() in {"salir", "exit", "quit"}:
                break
            if line.lower() == "estado":
                print(json.dumps(orch.status(), ensure_ascii=False, indent=2))
                continue
            orch.handle_text(line)
    except KeyboardInterrupt:
        print()
    finally:
        orch.shutdown()
    return 0


# --------------------------------------------------------------------------- #
# argparse + main
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="main.py", description="EON: compañero y agente local de escritorio (100%% privado).")
    parser.add_argument("--no-gui", action="store_true", help="modo consola (REPL) sin PyQt6")
    parser.add_argument("--text", action="store_true", help="alias de --no-gui")
    parser.add_argument("--no-voice", action="store_true", help="no abrir micrófono ni TTS")
    parser.add_argument("--diagnose", action="store_true", help="imprimir capacidades en JSON y salir")
    parser.add_argument("--send", metavar="TEXTO", help="enviar una orden a la instancia en marcha")
    parser.add_argument("--force", action="store_true", help="arrancar aunque parezca haber otra instancia")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    log = config.init_logging()
    log.info("arrancando EON (python %s, %s)", sys.version.split()[0], sys.platform)

    if args.send:
        print(send_command(args.send))
        return 0

    if not args.diagnose and not args.force and instance_is_up():
        print(f"EON ya está en marcha (puerto {INSTANCE_PORT}). Use --send \"orden\" o --force para otro ejemplar.")
        return 0

    try:
        orch = EonOrchestrator(allow_voice=not args.no_voice, headless=bool(args.no_gui or args.text))
    except Exception as exc:
        log.exception("no se pudo construir EON")
        print(f"EON no pudo arrancar: {exc}", file=sys.stderr)
        print(f"Detalle en el registro: {config.LOG_FILE}", file=sys.stderr)
        return 1

    # la spec es tajante: con el hash del kill switch roto, no se arranca
    if not orch.integrity.ok:
        log.error("arranque bloqueado por integridad: %s", orch.integrity.summary())
        print(orch.integrity.summary(), file=sys.stderr)
        print("Si acaba de editar safety/killswitch.py a mano, regénerelo con: python tools/attest_killswitch.py", file=sys.stderr)
        return 2

    if args.diagnose:
        print(json.dumps(orch.status(), ensure_ascii=False, indent=2))
        orch.shutdown()
        return 0

    if args.no_gui or args.text:
        return run_headless(orch)
    try:
        return run_gui(orch)
    except Exception as exc:  # pythonw no tiene consola: el log es la única voz, y la consola la segunda
        log.exception("la GUI cayó; EON sigue en modo consola")
        print(f"[EON] La interfaz no arrancó ({exc}); sigo en modo consola.", file=sys.stderr)
        return run_headless(orch)


if __name__ == "__main__":
    sys.exit(main())
