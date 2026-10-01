"""Bus de eventos mínimo y seguro para subprocesos.

Todos los motores (voz, visión, oído, GUI) hablan por aquí en lugar de
conocerse entre sí. Ventajas prácticas para un proyecto de este tamaño:

* Un fallo en un oyente nunca derriba al emisor (se registra y se continúa).
* La GUI puede suscribirse sin que ``core/`` importe ``PyQt6`` jamás.
* Los tests se pueden suscribir y afirmar sobre el orden de los eventos.

Convención de temas (puntos separados, jerárquicos)::

    eon.state            payload: {"state": "listening", "detail": ...}
    eon.voice.transcript payload: {"text": ..., "source": "mic"}
    eon.tts.amplitude    payload: {"level": 0.42}
    eon.model.swap       payload: {"from": ..., "to": ...}
    eon.action           payload: {"action": "click", "x":..., "y":...}
    eon.kill             payload: {"reason": ...}
    eon.music            payload: {"title":..., "source":...}
"""

from __future__ import annotations

import fnmatch
import logging
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

Handler = Callable[[str, dict], None]


@dataclass(frozen=True)
class Event:
    """Evento inmutable ya publicado."""

    topic: str
    payload: dict[str, Any]
    timestamp: float

    def age(self) -> float:
        return time.time() - self.timestamp


@dataclass
class EventBus:
    """Publicación/suscripción con suscripciones por patrón glob.

    ``history`` guarda los últimos eventos para que un componente que se
    suscribe tarde (la GUI, por ejemplo) pueda reconstruir el estado actual.
    """

    logger: logging.Logger | None = None
    history_size: int = 64
    _handlers: dict[str, list[tuple[int, Handler]]] = field(default_factory=dict, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    _history: deque[Event] = field(default_factory=deque, repr=False)
    _errors: int = field(default=0, repr=False)

    def __post_init__(self) -> None:
        self.log = self.logger or logging.getLogger("eon.bus")
        self._history = deque(maxlen=max(1, self.history_size))

    # ------------------------------------------------------------- suscripción --
    def subscribe(self, topic_glob: str, handler: Handler, priority: int = 0) -> Callable[[], None]:
        """Registra ``handler`` para los temas que casen con ``topic_glob``.

        Devuelve una función de desuscripción (útil en pruebas y al recargar).
        """
        with self._lock:
            bucket = self._handlers.setdefault(topic_glob, [])
            entry = (priority, handler)
            if entry not in bucket:
                bucket.append(entry)
                bucket.sort(key=lambda item: -item[0])

        def unsubscribe() -> None:
            with self._lock:
                current = self._handlers.get(topic_glob, [])
                if entry in current:
                    current.remove(entry)

        return unsubscribe

    def subscribe_many(self, topic_globs: Iterable[str], handler: Handler, priority: int = 0) -> Callable[[], None]:
        """Se suscribe a varios temas de una vez y devuelve un único "darse de baja"."""
        detachers = [self.subscribe(pattern, handler, priority) for pattern in topic_globs]

        def unsubscribe_all() -> None:
            for detach in detachers:
                detach()

        return unsubscribe_all

    # ------------------------------------------------------------------ emitir --
    def publish(self, topic: str, **payload: Any) -> Event:
        """Publica ``topic`` con ``payload``. Nunca lanza aunque un oyente falle."""
        event = Event(topic=topic, payload=dict(payload), timestamp=time.time())
        with self._lock:
            self._history.append(event)
            targets: list[Handler] = []
            for pattern, bucket in self._handlers.items():
                if pattern == topic or fnmatch.fnmatch(topic, pattern):
                    targets.extend(handler for _priority, handler in bucket)
        for handler in targets:
            try:
                handler(topic, event.payload)
            except Exception as exc:  # un oyente roto no puede tumbar al emisor
                self._errors += 1
                self.log.exception("oyente de %s falló: %s", topic, exc)
        return event

    # ------------------------------------------------------------------ historial --
    def recent(self, topic_glob: str = "*", limit: int = 20) -> list[Event]:
        with self._lock:
            events = [event for event in self._history if fnmatch.fnmatch(event.topic, topic_glob)]
        return events[-limit:]

    def last(self, topic: str) -> Event | None:
        for event in reversed(self.recent(topic, limit=1)):
            return event
        return None

    @property
    def handler_count(self) -> int:
        with self._lock:
            return sum(len(bucket) for bucket in self._handlers.values())

    @property
    def error_count(self) -> int:
        return self._errors

    def clear(self) -> None:
        """Vacía historial y suscripciones (se usa en el hot-reload)."""
        with self._lock:
            self._history.clear()
            self._handlers.clear()


#: Bus por defecto de la aplicación: simple, sin registro global de estado en
#: los módulos (cada motor recibe su bus en el constructor y puede testearse
#: con uno propio).
_default = EventBus()


def default_bus() -> EventBus:
    return _default


def publish(topic: str, **payload: Any) -> Event:
    return _default.publish(topic, **payload)
