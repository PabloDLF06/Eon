# EON

**Tu compañero de escritorio y agente autónomo, enteramente local.**
Una pequeña isla negra que respira en el borde superior de tu pantalla: te oye, te piensa, te obedece y se mejora a sí mismo — sin nube, sin cuentas, sin coste mensual. Nada de lo que dices o haces sale de tu portátil.

Diseñado para un Windows 11 con Ryzen 7, RTX 4060 (8 GB) y 16 GB de RAM, pero funciona en cualquier PC de gama media con Python.

---

## 1. Instalar (dos doble-clics, ni una línea de terminal)

1. Ten instalados estos tres programas (si ya los tienes, salta el paso):
   - **Python 3.11 o 3.12** — <https://www.python.org/downloads/> → al instalar, **marca la casilla «Add python.exe to PATH»**.
   - **Git** — <https://git-scm.com/download/win> (siguiente, siguiente, siguiente).
   - **Ollama** — <https://ollama.com/download> (es el motor que hace pensar a EON; es gratis).
2. Descarga esta carpeta (botón verde **Code → Download ZIP** en GitHub, o clónala si usas Git).
3. Doble clic a **`install.bat`**. El instalador te lleva de la mano: crea el entorno, instala todo, descarga los tres modelos y hace una comprobación rápida. La primera vez tarda un rato (los modelos ocupan varios gigas); las siguientes, segundos.
4. Doble clic a **`start.bat`**. En la parte superior central de tu pantalla aparecerá la isla de EON con su personaje asomando.

> ¿Se queda el instalador pensando? Es descargando modelos. No lo cierres; la barra de tareas lo sigue mostrando.

## 2. Qué es esa cosa del borde de la pantalla

La **isla** (notch) vive pegada al bisel superior como si fuera una pestaña del propio monitor. Su aspecto cambia según lo que esté haciendo EON, y el **personaje** (el blob de degradado marfil) asoma dentro para darle cara:

| La isla se ve… | EON está… |
|---|---|
| Cápsula estrecha 140×32, brillo cian que respira despacio | **En reposo**, escuchando el «Eon» por si lo llamas |
| Se abre a 260×42 con barritas cian que saltan | **Escuchándote** (di tu orden con normalidad) |
| 220×48, un anillo violeta orbitando | **Pensando** — si el anillo gira, además está cambiando de modelo en la gráfica |
| Tarjeta 360×80 con carátula, «Loser — Tame Impala» y ecualizador ámbar | **Poniendo música** u otra reproducción |
| Tarjeta 360×80 con borde cian y una línea de pasos | **Actuando**: moviendo el ratón y escribiendo por ti |
| Tarjeta rosada con cara de agobio | Algo ha fallado; explica qué en una línea |
| Pastillita 120×30 con dos «^ ^» y zZ flotando | **Durmiendo** (dile «despierta» o púlsala) |

Expresiones del personaje: parpadea cada 4–7 segundos con pequeñas sacudidas de ojo, respira (sube y baja muy suave), abre los ojos como platos y levanta las antenas con una onda radial cuando lo llamas por su nombre, mueve la boca al ritmo de su propia voz, y se pone una visera HUD mientras mira tu pantalla o hace clics.

El **borde de la pantalla se ilumina en cian** durante medio segundo cuando EON te está mirando (captura de pantalla, visión o automatización en curso): es tu semáforo de privacidad — si el borde brilla, está usando tus ojos.

## 3. Hablar con EON

- **Pulsar la isla** (o escribir en ella) y dar una orden.
- Decir su nombre: **«Eon»** — te escucha a partir de ahí, como un asistente normal. Si te interrumpe hablando, **simplemente háblale**: corta su voz en cuanto te oye (menos de 50 ms) y te escucha; a esto le llama *barge-in* y es la razón de que se sienta vivo.
- **Doble palmada**: aplaude dos veces seguidas (separadas un cuarto de segundo a tres cuartos). EON saluda según la hora («Buenas noches, Pablo»…) y pone *Loser* de Tame Impala — primero intenta Spotify; si no puede, abre Comet en modo aplicación; si tampoco, tu navegador. La isla pasa a tarjeta de música. Una palmada suelta o un portazo **no** la confunden: el detector mide el hueco entre los dos golpes.

Órdenes típicas (funcionan también por texto en la isla):

| Dile… | Hace… |
|---|---|
| «pon música» / «pon Loser de Tame Impala» | La rutina de la palmada, a demanda |
| «para la música» | La pausa |
| «qué hay en pantalla» / «describe mi pantalla» | La mira (VLM) y te la cuenta |
| «abre el bloc de notas y escribe la receta» | Automatización con visión: mira, planifica, mueve el ratón con curvas humanas, escribe |
| «crea una aplicación de gastos» | La **fábrica**: genera el proyecto en `workspace/projects/`, lo comprueba y te lo abre |
| «mejórate con un modo foco» | El **auto-programador**: rama nueva, código, pruebas, y si todo pasa se fusiona y reinicia |
| «cállate» / «para» | Silencio inmediato / **todo detenido** (ver abajo) |
| «duerme» | Se acurruca en modo pastilla con zZ |

Durante una automatización puedes interrumpir con una corrección («no, el otro botón») — EON congela la cola, vuelve a mirar y replanifica.

## 4. El interruptor de seguridad (léelo dos veces)

EON mueve ratón y teclado, así que incluye un **matamarchas** que nunca depende de los modelos:

- **Ctrl+Shift+Espacio** (en cualquier sitio), o
- **sacudir el ratón** violentamente (más de 1500 px en menos de 0,3 s; dos manotazos laterales rápidos bastan).

Al accionarlo: se cortan las automatizaciones, se sueltan todas las teclas y botones que EON tuviera pulsados, la pantalla deja de brillar y la isla colapsa a reposo. Vuelve a pulsar el combo para reanudar. Si el bot se vuelve loco —que no debería—, el interruptor es tu rienda y funciona aunque el 90 % del programa esté caído: su propio archivo lleva un candado.

Además, la seguridad es **autoincorruptible**: `safety/killswitch.py` lleva un hash SHA-256 grabado (`assets/integrity/killswitch.sha256`) que se verifica antes de que EON haga nada; si el archivo cambia sin tu permiso, **EON se niega a arrancar** hasta que tú lo certifiques ejecutando `tools/attest_killswitch.py` (esto último es para humanos, y el auto-programador tiene terminantemente prohibido escribir en `safety/`).

## 5. Hardware y consumo

| Pieza | Cuánto ocupa | Cuándo |
|---|---|---|
| `llama3.1:8b` (el cerebro) | ~4,9 GB de VRAM | residentes ~3–5 min tras hablar (configurable) |
| `llama3.2-vision` (los ojos) | ~7,9 GB | **a demanda**: se carga, mira, y se descarga |
| `qwen2.5-coder` (las manos de código) | ~4,7 GB | solo al programar apps o mejorarse |
| Whisper + wake word + tu voz | CPU/RAM, 0 GB | siempre |
| Reposo (isla + personaje + escucha) | 1,5–2,5 GB | todo el santo día |

**Regla de la monogamia de VRAM:** en la gráfica nunca hay dos modelos a la vez. Antes de cargar uno, EON expulsa al que esté y espera la confirmación; por eso el anillo violeta a veces orbita un poco antes de contestar — es el peaje de no tumbarte el portátil con un desbordamiento de memoria.

## 6. Personalizar sin miedo

Crea un archivo `settings.json` junto a `main.py` con lo que quieras cambiar; todo lo demás se queda igual:

```json
{
  "USER_NAME": "Pablo",
  "WAKE_WORD": "eon",
  "MIC_ENABLED": true,
  "ACTION_CONFIRM_MODE": "danger",
  "MAX_VRAM_BUDGET_GB": 6.4
}
```

Los valores posibles están documentados en `config.py` (español, sin rodeos). Las claves desconocidas se ignoran con un aviso: no puedes cargarla.

- `ACTION_CONFIRM_MODE`: `"danger"` (pregunta antes de pasos destructivos, por defecto), `"all"` (confirma cada clic) o `"none"`.
- `MUSIC_CONFIG`: cámbialo por tu canción y tu artista de ritual de palmada.

## 7. Si algo va mal

| Síntoma | Causa probable y arreglo |
|---|---|
| «Ollama no responde» | Abre Ollama (icono en la bandeja) y reintenta. EON sigue funcionando como interfaz y oído hasta entonces. |
| No te oye | Windows → Configuración → Privacidad → Micrófono: permite las apps de escritorio. Prueba `start.bat` otra vez. |
| «INTEGRIDAD ROTA» al arrancar | Alguien tocó `safety/killswitch.py`. Si fuiste tú: `python tools/attest_killswitch.py`. Si no: no arranques EON y revisa la carpeta. |
| La isla no aparece | Ejecuta `install.bat` de nuevo; mirando `logs/eon.log` verás el porqué. |
| Modelos a medias | Vuelve a pasar `install.bat`: los `ollama pull` incompletos se reanudan solos. |

El registro vive en `logs/eon.log` (texto plano, español). Cualquier subsistema que falla se degrada y **lo cuenta**: EON prefiere responder «no pude» antes que morirse en silencio.

## 8. Para el que quiera mirar dentro (no hace falta para usarlo)

```
main.py                 arranque + enrutador de órdenes en español
config.py               todos los ajustes del mundo, con comentarios
core/event_bus.py       el correo interno: nadie llama a nadie directamente
core/model_router.py    monogamia de VRAM y política de residencia de modelos
core/voice_engine.py    micro compartido, wake word, whisper, TTS, barge-in
core/acoustic_detector.py  doble palmada + cascada Spotify→Comet→navegador
core/vision_actuator.py captura + VLM + ratón con trayectorias humanas
core/self_programmer.py auto-mejora con git, pytest y rollback
core/app_builder.py     la fábrica de aplicaciones (workspace/projects/)
gui/scene.py            el lenguaje gráfico común (vector puro, sin Qt)
gui/notch_layout.py     estados y morfología de la isla (la única autoridad)
gui/char_kinematics.py  el personaje: respiración, parpadeo, ondas
gui/qt_paint.py         traductor del lenguaje gráfico a QPainter
safety/killswitch.py    el matamarchas con candado (intocable por diseño)
tools/preview.py        fotogramas de la isla y el personaje sin abrir Qt
tests/test_core.py      la verificación; también el cordón del auto-mejorador
```

Los cuatro pilares de diseño: **(1)** todo periférico (micro, altavoz, ratón, Ollama, Spotify) puede faltar o romperse y EON sigue arrancando, degradando con honestidad; **(2)** el estado lo decide una única fuente por subsistema (la GUI solo pinta); **(3)** antes de actuar, siempre se pregunta al interruptor de seguridad; **(4)** el auto-mejorador no escribe nunca en `safety/` ni en `config.py`, y su fusión exige que la suite completa pase.

## 9. Privacidad, palabra por palabra

- Ninguna llamada de EON sale de este equipo salvo que tú le mandes abrir una página.
- Los modelos los descarga Ollama directamente; el audio nunca se envía a ningún sitio: Whisper y openWakeWord corren aquí.
- Las capturas de pantalla se usan para que el VLM local te ayude y se borran; las automatizaciones siempre son visibles (borde cian + tarjeta de acción).
- `workspace/` (tus apps) y `logs/` (tus registros) quedan en tu disco, ignorados por Git.

---

*EON no es un producto: es un compañero que crece en tu máquina. Que se porte.*
