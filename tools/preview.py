"""Previsualización sin Qt de los estados del notch y del personaje.

Uso::

    python tools/preview.py --out workspace/preview
    python tools/preview.py --states idle listening thinking media --scale 2

No necesita monitor, GPU, micrófono ni Ollama: rasteriza con Pillow las mismas
primitivas de ``gui.scene`` que pinta ``QPainter`` en la aplicación real. Sirve
para revisar proporciones y color, y lo usan los tests de humo de GUI.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config
from gui import notch_layout, scene
from gui.char_kinematics import CharModel, CharState
from gui.notch_layout import MediaInfo, NotchController
from tools.scene_render import render_scene_to_image

CHAR_W, CHAR_H = 220, 200


def desktop_backdrop(width: int, height: int, menu_text: str = "EON") -> Image.Image:
    """Fondo tipo escritorio con barra superior, para juzgar el notch en contexto."""
    img = Image.new("RGBA", (width, height), (12, 12, 18, 255))
    draw = ImageDraw.Draw(img)
    # degradado vertical simulando el wallpaper
    for y in range(height):
        t = y / max(1, height - 1)
        color = scene.mix("#141425", "#2b2140", t)
        draw.line([(0, y), (width, y)], fill=(*scene.hex_to_rgb(color), 255))
    draw.rectangle([0, 0, width, 26], fill=(6, 6, 10, 255))
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 13)
    except OSError:  # pragma: no cover
        font = ImageFont.load_default()
    draw.text((14, 6), menu_text, font=font, fill=(235, 238, 245, 200))
    draw.text((width - 120, 6), "10:42   100%", font=font, fill=(235, 238, 245, 160))
    return img


def render_notch_state(state: str, out_path: Path, scale: float = 2.0, seconds: float = 1.4) -> Image.Image:
    """Avanza el controlador hasta ``state`` y guarda su fotograma."""
    controller = NotchController()
    controller.set_state(state)
    if state in ("media", "action"):
        controller.set_media(
            MediaInfo(title="Loser", artist="Tame Impala", position_s=61.0, duration_s=222.0, source="Spotify", playing=True)
        )
    if state == "thinking":
        controller.set_status("Descargando weights", "llama3.2-vision")
    if state == "listening":
        controller.char.speak_level(0.5)
    if state == "error":
        controller.set_status("No hay respuesta de Ollama", "reintenta con start.bat")
    steps = max(8, int(seconds * 60))
    snapshot = None
    for i in range(steps):
        level = 0.6 * abs(scene.noise_series(1, i / 20.0)[0]) if state == "listening" else 0.0
        controller.char.update(1 / 60.0, level=min(1.0, level))
        snapshot = controller.tick(1 / 60.0)
    assert snapshot is not None
    full = notch_layout.build_scene(snapshot, screen_width=1920)
    image = render_scene_to_image(full, scale=scale, supersample=2, background="#050508")
    band_h = int(config.NOTCH_BAND_HEIGHT * scale)
    canvas = desktop_backdrop(int(760 * scale), band_h + int(60 * scale))
    x0 = (canvas.width - image.width) // 2
    canvas.alpha_composite(image, (x0, int(26 * scale)))
    draw = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", int(13 * scale))
    except OSError:  # pragma: no cover
        font = ImageFont.load_default()
    label = f"{state}   {snapshot.geometry.width:.0f}x{snapshot.geometry.height:.0f}   r={snapshot.geometry.corner:.1f}"
    draw.text((12, band_h + int(30 * scale)), label, font=font, fill=(200, 210, 235, 255))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path)
    return canvas


def render_char_state(state: str, out_path: Path, scale: float = 2.0) -> Image.Image:
    """Primer plano del personaje en un estado concreto (fondo oscuro)."""
    model = CharModel()
    model.set_state(state)
    frame = None
    for i in range(70):
        level = 0.55 + 0.45 * scene.noise_series(1, i / 8.0)[0] if state == "speaking" else 0.0
        frame = model.update(1 / 60.0, level=max(0.0, min(1.0, level)))
    assert frame is not None
    sc = scene.Scene(width=CHAR_W, height=CHAR_H, shapes=())
    from gui.char_kinematics import build_scene as char_scene

    built = char_scene(frame, (18, 26, CHAR_W - 36, CHAR_H - 46))
    image = render_scene_to_image(sc.with_shapes(built.shapes), scale=scale, supersample=2, background="#0a0a0e")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(out_path)
    return image


def montage(images: list[Image.Image], out_path: Path, columns: int, title: str) -> Image.Image:
    """Hoja de contacto para revisar todos los estados de un vistazo."""
    if not images:
        raise SystemExit("sin imágenes que componer")
    pad = 10
    cell_w = max(img.width for img in images)
    cell_h = max(img.height for img in images)
    rows = (len(images) + columns - 1) // columns
    sheet = Image.new("RGBA", (columns * (cell_w + pad) + pad, rows * (cell_h + pad) + pad + 34), (16, 16, 22, 255))
    draw = ImageDraw.Draw(sheet)
    draw.text((pad, 10), title, fill=(226, 232, 245, 255))
    for index, img in enumerate(images):
        r, c = divmod(index, columns)
        sheet.alpha_composite(img, (pad + c * (cell_w + pad), pad + 34 + r * (cell_h + pad)))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path)
    return sheet


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render de previsualización del notch y el personaje de EON")
    parser.add_argument("--out", default=str(config.WORKSPACE_DIR / "preview"), help="carpeta de destino")
    parser.add_argument("--scale", type=float, default=2.0, help="factor de zoom")
    parser.add_argument("--states", default="", help="lista separada por comas; por defecto todos")
    parser.add_argument("--chars", action="store_true", help="incluye primeros planos del personaje")
    args = parser.parse_args(argv)

    out_dir = Path(args.out)
    notch_states = [s.strip() for s in args.states.split(",") if s.strip()] or [
        "idle",
        "listening",
        "thinking",
        "media",
        "action",
        "error",
        "sleeping",
    ]
    notch_images = []
    for state in notch_states:
        image = render_notch_state(state, out_dir / f"notch_{state}.png", scale=args.scale)
        notch_images.append(image)
        print(f"[ok] notch {state} -> {out_dir / f'notch_{state}.png'}")
    montage(notch_images, out_dir / "notch_states.png", columns=2, title="EON · Dynamic Notch states")

    if args.chars:
        char_images = []
        for state in [s.value for s in CharState]:
            image = render_char_state(state, out_dir / f"char_{state}.png", scale=args.scale)
            char_images.append(image)
        montage(char_images, out_dir / "char_states.png", columns=4, title="EON · Char states")
        print(f"[ok] personaje -> {out_dir / 'char_states.png'}")
    print(f"[listo] previsualizaciones en {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
