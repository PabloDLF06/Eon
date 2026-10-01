"""Pruebas del núcleo de EON. Corren sin GUI, sin micrófono, sin Ollama y sin red.

Esta suite es doblemente importante: es la verificación de calidad y a la vez
el *cordón de seguridad* que ejecuta el auto-programador antes de fusionar una
mejora propia (``core/self_programmer.py`` corre exactamente estos tests).
Por eso cada prueba debe ser determinista, rápida y no tocar periféricos.
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402

# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #


class TestConfig:
    def test_notch_sizes_cover_every_state(self):
        from gui.notch_layout import NotchState

        assert set(config.NOTCH_SIZES) == {s.value for s in NotchState}

    def test_notch_sizes_match_spec(self):
        assert config.NOTCH_SIZES["idle"] == (140, 32)
        assert config.NOTCH_SIZES["listening"] == (260, 42)
        assert config.NOTCH_SIZES["thinking"] == (220, 48)
        assert config.NOTCH_SIZES["media"] == (360, 80)
        assert config.NOTCH_SIZES["action"] == (360, 80)

    def test_palette_hex_colors(self):
        import re

        for name, hex_value in vars(config.COLORS).items():
            if name.startswith("_") or not isinstance(hex_value, str):
                continue
            assert re.fullmatch(r"#[0-9a-fA-F]{6}", hex_value), (name, hex_value)

    def test_apply_overrides_rejects_unknown_keys(self):
        original = config.NOTCH_BAND_HEIGHT
        try:
            applied = config.apply_overrides({"NOTCH_BAND_HEIGHT": 160, "NO_EXISTE": 1})
            assert applied == ["NOTCH_BAND_HEIGHT"]
            assert config.NOTCH_BAND_HEIGHT == 160
        finally:
            config.NOTCH_BAND_HEIGHT = original

    def test_runtime_dirs_created(self):
        config.ensure_runtime_dirs()
        for directory in (config.LOGS_DIR, config.WORKSPACE_DIR, config.PROJECTS_DIR, config.CACHE_DIR):
            assert Path(directory).is_dir()

    def test_models_follow_vram_monogamy(self):
        # el presupuesto debe dejar margen dentro de los 8 GB del portátil
        assert config.MAX_VRAM_BUDGET_GB < 8.0
        assert config.EPHEMERAL_KEEP_ALIVE in ("0", 0)


# --------------------------------------------------------------------------- #
# safety.killswitch
# --------------------------------------------------------------------------- #


class TestKillSwitch:
    def test_engage_release_and_events(self):
        from safety.killswitch import KillSwitch

        switch = KillSwitch(enable_keyboard_hook=False, enable_mouse_watchdog=False)
        seen: list[str] = []
        switch.register("probe", lambda reason: seen.append(reason))
        switch.start()
        assert not switch.engaged
        switch.engage("prueba")
        assert switch.engaged and switch.should_stop()
        assert seen == ["prueba"]
        switch.release()
        assert not switch.engaged
        switch.stop()

    def test_toggle_flips(self):
        from safety.killswitch import KillSwitch

        switch = KillSwitch(enable_keyboard_hook=False, enable_mouse_watchdog=False)
        assert switch.toggle("una") is True
        assert switch.toggle("dos") is False
        switch.release()

    def test_hook_failure_does_not_stop_others(self):
        from safety.killswitch import KillSwitch

        switch = KillSwitch(enable_keyboard_hook=False, enable_mouse_watchdog=False)
        hits: list[str] = []
        switch.register("malo", lambda _r: 1 / 0)
        switch.register("bueno", lambda _r: hits.append("ok"))
        switch.start()
        switch.engage()
        assert hits == ["ok"]
        switch.release()
        switch.stop()

    def test_critical_hook_failure_is_counted_not_swallowed(self):
        from safety.killswitch import KillSwitch

        switch = KillSwitch(enable_keyboard_hook=False, enable_mouse_watchdog=False)
        switch.register("crítica", lambda _r: 1 / 0, critical=True)
        switch.start()
        failures = switch.engage()
        assert failures >= 1  # se reporta; la pausa igualmente ocurrió
        assert switch.engaged
        switch.release()
        switch.stop()

    def test_guard_raises_paused_and_wait_releases(self):
        from safety.killswitch import EonPaused, KillSwitch

        switch = KillSwitch(enable_keyboard_hook=False, enable_mouse_watchdog=False)
        switch.engage()
        with pytest.raises(EonPaused):
            switch.guard()
        import threading

        threading.Timer(0.15, lambda: switch.release()).start()
        assert switch.wait_resume(timeout=3.0) is True
        switch.guard()  # no lanza: ya estamos en marcha
        assert switch.wait_resume(timeout=0.5) is True  # sin pausa, regala el visto bueno
        assert switch.wait_resume(timeout=0.1) is True
        switch.stop()

    def test_integrity_baseline_verifies(self, tmp_path):
        from safety.killswitch import compute_file_sha256, verify_integrity

        target = tmp_path / "safety" / "killswitch.py"
        target.parent.mkdir(parents=True)
        target.write_text("x = 1\n", encoding="utf-8")
        baseline = tmp_path / "assets" / "integrity" / "killswitch.sha256"
        baseline.parent.mkdir(parents=True)
        digest = compute_file_sha256(target)
        baseline.write_text(f"{digest}  safety/killswitch.py\n", encoding="utf-8")
        report = verify_integrity(root=tmp_path)
        assert report.ok and not report.skipped

    def test_integrity_detects_tampering(self, tmp_path):
        from safety.killswitch import verify_integrity

        target = tmp_path / "safety" / "killswitch.py"
        target.parent.mkdir(parents=True)
        target.write_text("x = 1\n", encoding="utf-8")
        baseline = tmp_path / "assets" / "integrity" / "killswitch.sha256"
        baseline.parent.mkdir(parents=True)
        baseline.write_text("0" * 64 + "  safety/killswitch.py\n", encoding="utf-8")
        report = verify_integrity(root=tmp_path)
        assert not report.ok
        assert "INTEGRIDAD ROTA" in report.summary()

    def test_repo_baseline_matches(self):
        # si esta prueba falla, alguien cambió el kill switch sin atestarlo:
        # python tools/attest_killswitch.py
        from safety.killswitch import verify_integrity

        report = verify_integrity(root=ROOT)
        assert report.ok, report.summary()

    def test_assert_writable_path_blocks_protected(self, tmp_path):
        from safety.killswitch import EonPaused, assert_writable_path

        (tmp_path / "safety").mkdir()
        (tmp_path / "safety" / "killswitch.py").write_text("x = 1", encoding="utf-8")
        (tmp_path / "workspace").mkdir()
        with pytest.raises(EonPaused):
            assert_writable_path(tmp_path / "safety" / "extra.py", root=tmp_path)
        with pytest.raises(EonPaused):
            assert_writable_path(tmp_path / "config.py", root=tmp_path)
        ok = assert_writable_path(tmp_path / "workspace" / "projects" / "algo.py", root=tmp_path)
        assert Path(ok).name == "algo.py"


# --------------------------------------------------------------------------- #
# core.event_bus
# --------------------------------------------------------------------------- #


class TestEventBus:
    def test_glob_subscription_and_unsubscribe(self):
        from core.event_bus import EventBus

        bus = EventBus()
        got: list[str] = []
        off = bus.subscribe("eon.voice.*", lambda topic, payload: got.append(topic))
        bus.publish("eon.voice.wake", score=0.9)
        bus.publish("eon.model.swap", model="x")
        off()
        bus.publish("eon.voice.listening")
        assert got == ["eon.voice.wake"]

    def test_listener_failure_is_contained(self):
        from core.event_bus import EventBus

        bus = EventBus()
        seen: list[int] = []
        bus.subscribe("t", lambda topic, payload: 1 / 0)
        bus.subscribe("t", lambda topic, payload: seen.append(1))
        bus.publish("t")
        assert seen == [1]
        assert bus.error_count >= 1

    def test_history_and_last(self):
        from core.event_bus import EventBus

        bus = EventBus(history_size=8)
        for i in range(12):
            bus.publish("num", value=i)
        assert bus.last("num").payload["value"] == 11
        assert len(bus.recent("num", limit=5)) == 5


# --------------------------------------------------------------------------- #
# core.model_router
# --------------------------------------------------------------------------- #


class TestModelRouter:
    def test_extract_json_variants(self):
        from core.model_router import extract_json

        assert extract_json('texto {"a": 1,} fin') == {"a": 1}
        assert extract_json("```json\n{\"b\": [1,2]}\n```") == {"b": [1, 2]}
        with pytest.raises(ValueError):
            extract_json("nada que ver")

    def test_normalize_model_name(self):
        from core.model_router import normalize_model_name

        assert normalize_model_name("llama3.1:8b") == "llama3.1:8b"
        assert normalize_model_name(" llama3.1:8b-instruct-q4_K_M ") == "llama3.1:8b-instruct-q4_K_M"

    def test_router_unreachable_degrades(self):
        from core.model_router import ModelRouter, OllamaClient

        router = ModelRouter(client=OllamaClient(host="http://127.0.0.1:1", timeout=0.3))
        health = router.health(check_models=False)
        assert not health.reachable
        assert router.degraded

    def test_keep_alive_policy_ephemeral(self):
        from core.model_router import ModelRouter, OllamaClient

        router = ModelRouter(client=OllamaClient(host="http://127.0.0.1:1", timeout=0.3))
        vision = router.model_for("vision")
        assert router._keep_alive_for(vision, None) == config.EPHEMERAL_KEEP_ALIVE
        brain = router.model_for("brain")
        assert router._keep_alive_for(brain, None) == config.BRAIN_KEEP_ALIVE


# --------------------------------------------------------------------------- #
# core.acoustic_detector — doble palmada
# --------------------------------------------------------------------------- #


class TestClapDetection:
    @pytest.mark.parametrize("gap", [0.25, 0.36, 0.5, 0.7])
    def test_double_clap_detected_in_window(self, gap):
        from core.acoustic_detector import analyse, make_clap_signal

        samples = make_clap_signal(gap_s=gap)
        _onsets, pair = analyse(samples, 16000)
        assert pair is not None, f"no se detectó el par con gap {gap}"
        first, second, _score = pair
        assert abs((second.time_s - first.time_s) - gap) < 0.12

    def test_single_clap_rejected(self):
        from core.acoustic_detector import analyse, make_clap_signal

        samples = make_clap_signal(gap_s=0.36)
        single = samples[: len(samples) // 2] + [0.0] * 4000
        _onsets, pair = analyse(single, 16000)
        assert pair is None

    def test_gap_too_long_rejected(self):
        from core.acoustic_detector import analyse, make_clap_signal

        samples = make_clap_signal(gap_s=1.4)
        _onsets, pair = analyse(samples, 16000)
        assert pair is None

    def test_continuous_noise_rejected(self):
        from core.acoustic_detector import analyse

        rate = 16000
        noise = [0.3 * math.sin(2 * math.pi * 900 * i / rate) for i in range(rate)]
        _onsets, pair = analyse(noise, rate)
        assert pair is None

    def test_silence_rejected(self):
        from core.acoustic_detector import analyse

        _onsets, pair = analyse([0.0] * 16000, 16000)
        assert pair is None

    def test_greeting_windows(self):
        from core.acoustic_detector import greeting_for

        assert "Buenos días" in greeting_for(datetime(2026, 5, 4, 8, 30), name="Pablo")
        assert "Buenas tardes" in greeting_for(datetime(2026, 5, 4, 15, 0), name="Pablo")
        assert "Buenas noches" in greeting_for(datetime(2026, 5, 4, 23, 30), name="Pablo")

    def test_ear_fires_once_then_refractory(self):
        from core.acoustic_detector import AcousticEar, LaunchResult, make_clap_signal

        fired: list[str] = []
        ear = AcousticEar(mic=None, on_trigger=lambda greeting, _result: fired.append(greeting), logger=None)

        class NoMusic:
            def play(self):
                return LaunchResult(True, "simulado", "test", ())

        ear.launcher = NoMusic()  # no dependemos de Spotify/Comet en el test
        samples = make_clap_signal(gap_s=0.36)
        assert ear.feed(samples, 16000) is True
        deadline = time.time() + 3.0
        while not fired and time.time() < deadline:  # la rutina corre en su hilo
            time.sleep(0.02)
        assert fired and fired[0]  # un solo disparo, con saludo no vacío
        assert ear.feed(samples, 16000) is False  # ventana refractaria

    def test_music_launcher_cascade(self, monkeypatch):
        from core import acoustic_detector as ad

        calls: list[list[str]] = []
        state = {"up": False}

        def fake_runner(command):
            calls.append(list(command))
            state["up"] = True
            return True

        monkeypatch.setattr(ad, "find_spotify", lambda: Path("C:/fake/Spotify.exe"), raising=False)
        monkeypatch.setattr(ad, "process_running", lambda _name: state["up"], raising=False)
        launcher = ad.MusicLauncher(runner=fake_runner)
        result = launcher.play()
        assert result.ok and result.source == "Spotify"
        assert calls and any("Spotify.exe" in command[0] for command in calls)

    def test_browser_app_command_shape(self):
        from core.acoustic_detector import browser_app_command

        command = browser_app_command(Path("/fake/chrome.exe"), "http://127.0.0.1:8765/index.html")
        assert command and command[0].endswith("chrome.exe")
        assert "--app=http://127.0.0.1:8765/index.html" in command[1]


# --------------------------------------------------------------------------- #
# core.voice_engine — DSP puro
# --------------------------------------------------------------------------- #


class TestVoiceDsp:
    def test_bytes_to_float_roundtrip_scale(self):
        from core.voice_engine import bytes_to_float

        pcm = (0).to_bytes(2, "little", signed=True) + (16384).to_bytes(2, "little", signed=True)
        values = bytes_to_float(pcm)
        assert values[0] == pytest.approx(0.0)
        assert values[1] == pytest.approx(0.5, abs=1e-3)

    def test_rms_sine(self):
        from core.voice_engine import rms

        sine = [math.sin(2 * math.pi * 440 * i / 16000) for i in range(16000)]
        assert 0.6 < rms(sine) < 0.75
        assert rms([0.0] * 500) == 0.0

    def test_envelope_length_and_range(self):
        from core.voice_engine import compute_envelope

        signal = [math.sin(2 * math.pi * 100 * i / 16000) * (i / 8000) for i in range(8000)]
        env = compute_envelope(signal, bins=32)
        assert len(env) == 32
        assert all(0.0 <= v <= 1.0 for v in env)
        assert env[-1] > env[0]  # la señal crece: la envolvente también

    def test_trim_silence_keeps_speech(self):
        from core.voice_engine import trim_silence

        burst = [0.0] * 2000 + [math.sin(i) * 0.5 for i in range(4000)] + [0.0] * 2000
        trimmed = trim_silence(burst)
        assert len(trimmed) < len(burst)
        assert max(abs(v) for v in trimmed) > 0.4

    def test_split_sentences(self):
        from core.voice_engine import split_sentences

        parts = split_sentences("Hola señor. ¿Cómo está? Muy bien; gracias.", max_len=200)
        assert len(parts) >= 3
        long_text = "a" * 500
        assert all(len(p) <= 200 for p in split_sentences(long_text, max_len=200))

    def test_wav_roundtrip(self):
        from core.voice_engine import _decode_wav, _encode_wav

        signal = [math.sin(2 * math.pi * 220 * i / 22050) for i in range(2205)]
        payload = _encode_wav(signal, 22050)
        out, rate = _decode_wav(payload)
        assert rate == 22050
        assert len(out) > 1500


# --------------------------------------------------------------------------- #
# core.barge_in
# --------------------------------------------------------------------------- #


class TestBargeIn:
    def test_energy_fallback_without_optional_vad_backends(self, monkeypatch):
        from core.barge_in import VoiceActivityDetector

        monkeypatch.setitem(sys.modules, "webrtcvad", None)
        monkeypatch.setitem(sys.modules, "silero_vad", None)
        vad = VoiceActivityDetector()
        assert vad.backend == "energy"
        assert vad.is_speech_bytes(bytes(960)) is False
        assert vad.is_speech([0.5 * math.sin(2 * math.pi * 130 * i / 16000) for i in range(480)]) is True

    def test_energy_vad_flags_speech(self):
        from core.barge_in import EnergyVad

        def voice_like(seconds: float = 0.03, rate: int = 16000) -> list[float]:
            n = int(rate * seconds)
            return [0.5 * math.sin(2 * math.pi * 130 * t / rate) * (0.6 + 0.4 * math.sin(2 * math.pi * 3 * t / rate)) for t in range(n)]

        vad = EnergyVad()
        assert vad.is_speech([0.0005] * 480) is False
        assert vad.is_speech(voice_like()) is True

    def test_monitor_interrupts_tts_once(self):
        import array

        from core.barge_in import BargeInConfig, BargeInMonitor

        fired: list[int] = []
        config_obj = BargeInConfig(voiced_frames=1, min_speech_ms=0, max_cut_ms=100, hangover_ms=0, ignore_first_ms=0)
        monitor = BargeInMonitor(mic=None, on_interrupt=lambda: fired.append(1), config=config_obj)
        assert monitor.start() is False  # sin micrófono compartido, no arranca
        monitor.note_tts_start()

        def frame(n: int) -> bytes:
            return array.array("h", (int(16000 * math.sin(2 * math.pi * 130 * i / 16000)) for i in range(n))).tobytes()

        ok = monitor.feed(frame(480), 16000, speaking=True)
        assert ok is True
        assert len(fired) == 1
        # el segundo frame inmediato no re-dispara (antirrebote de 250 ms)
        monitor.feed(frame(480), 16000)
        assert len(fired) == 1
        monitor.stop()


# --------------------------------------------------------------------------- #
# core.vision_actuator
# --------------------------------------------------------------------------- #


class TestVisionParsing:
    def test_fenced_plan_parsed(self):
        from core.vision_actuator import parse_plan

        raw = '```json\n[{"action":"click","x":100,"y":200,"description":"abrir"}]\n```'
        actions, error = parse_plan(raw, (1920, 1080))
        assert not error
        assert len(actions) == 1 and actions[0].action == "click"
        assert (actions[0].x, actions[0].y) == (100, 200)

    def test_single_object_parsed(self):
        from core.vision_actuator import parse_plan

        actions, error = parse_plan('{"action": "type", "text": "hola"}', (1920, 1080))
        assert not error and len(actions) == 1
        assert actions[0].text == "hola"

    def test_trailing_comma_tolerated(self):
        from core.vision_actuator import parse_plan

        actions, error = parse_plan('[{"action":"click","x":1,"y":2},]', (1920, 1080))
        assert not error and len(actions) == 1

    def test_model_error_surfaces(self):
        from core.vision_actuator import parse_plan

        actions, error = parse_plan('{"actions": [], "error": "no veo el botón"}', (1920, 1080))
        assert not actions
        assert "no veo el botón" in error

    @pytest.mark.parametrize(
        ("x", "y", "space", "expect"),
        [
            (0.5, 0.25, None, (960, 270)),
            (100, 200, None, (100, 200)),
            (500, 250, "normalized1000", (960, 270)),
        ],
    )
    def test_normalize_point_spaces(self, x, y, space, expect):
        from core.vision_actuator import normalize_point

        assert normalize_point(x, y, (1920, 1080), coord_space=space) == expect

    def test_actions_capped_and_clamped(self):
        from core.vision_actuator import parse_plan

        plan = json.dumps([{"action": "click", "x": 99999, "y": -50} for _ in range(30)])
        actions, _error = parse_plan(plan, (1920, 1080), max_steps=4)
        assert len(actions) == 4
        assert all(0 <= a.x <= 1920 and 0 <= a.y <= 1080 for a in actions)

    def test_killswitch_blocks_execution(self):
        from core.vision_actuator import InputActuator
        from safety.killswitch import KillSwitch

        switch = KillSwitch(enable_keyboard_hook=False, enable_mouse_watchdog=False)
        actuator = InputActuator(killswitch=switch)
        switch.engage()
        with pytest.raises(RuntimeError):
            actuator.click(10, 10)
        switch.release()


# --------------------------------------------------------------------------- #
# core.self_programmer
# --------------------------------------------------------------------------- #


class TestSelfProgrammerGuards:
    def test_sanitize_branch_hint(self):
        from core.self_programmer import sanitize_branch_hint

        assert sanitize_branch_hint("¡Avisos de correo!") == "avisos-de-correo"
        assert sanitize_branch_hint("") == "mejora"
        assert sanitize_branch_hint("///...") == "mejora"

    def test_branch_name_shape(self):
        from core.self_programmer import branch_name

        name = branch_name(when=datetime(2026, 1, 2, 3, 4, 5), hint="prueba")
        assert name.startswith("feature/self-upgrade-20260102-030405")
        assert name.endswith("prueba")

    def test_parse_manifest_tolerant(self):
        from core.self_programmer import parse_manifest

        files, summary, error = parse_manifest('{"summary": "saludo", "files": [{"path": "core/x.py", "content": "x = 1"}]}')
        assert not error
        assert files[0].path == "core/x.py"
        assert summary == "saludo"

        fenced, _summary, error = parse_manifest('```json\n{"files": [{"path": "core/y.py", "content": "y = 2"}]}\n```')
        assert not error and fenced[0].path == "core/y.py"

    def test_validate_files_blocks_protected(self, tmp_path):
        from core.self_programmer import PatchFile, validate_files

        (tmp_path / "safety").mkdir()
        (tmp_path / "safety" / "killswitch.py").write_text("x = 1", encoding="utf-8")
        files = [
            PatchFile("core/nuevo.py", "OK = 1\n"),
            PatchFile("safety/extra.py", "malo = 1\n"),
            PatchFile("config.py", "X = 2\n"),
            PatchFile("../escape.py", "malo\n"),
            PatchFile("core/roto.py", "def (oops\n"),
            PatchFile("notes.txt", "hola\n"),
        ]
        accepted, rejected = validate_files(files, root=tmp_path)
        assert [f.path for f in accepted] == ["core/nuevo.py"]
        assert len(rejected) == 5
        assert any("safety" in r for r in rejected)
        assert any("config.py" in r or "protegido" in r.lower() for r in rejected)

    def test_test_command_is_pytest_tests(self):
        from core.self_programmer import test_command

        command = test_command("/x/python")
        assert command[0] == "/x/python"
        assert "pytest" in command and "tests/" in command

    def test_upgrade_result_messages(self):
        from core.self_programmer import UpgradeResult

        failed = UpgradeResult(ok=False, stage="pruebas", rolled_back=True)
        assert "fallaron las pruebas de seguridad internas" in failed.message()
        assert "No pude añadir la funcionalidad" in failed.message()


# --------------------------------------------------------------------------- #
# core.app_builder
# --------------------------------------------------------------------------- #


class TestAppBuilder:
    def test_slug_and_keywords(self):
        from core.app_builder import slug_from_idea, slugify

        assert slugify("¡Notas Minimalistas!") == "notas-minimalistas"
        assert slug_from_idea("créame una aplicación de notas minimalista") == "notas-minimalista"
        assert slugify("   ", fallback="app") == "app"

    def test_manifest_traversal_rejected(self):
        from core.app_builder import parse_build_manifest, validate_files

        files, _meta, _error = parse_build_manifest(
            '{"files":[{"path":"../evil.py","content":"x=1"},{"path":"./index.html","content":"<!doctype html><html><body>ok</body></html>"}]}'
        )
        accepted, rejected = validate_files(files)
        assert [f.path for f in accepted] == ["index.html"]
        assert any("fuera del proyecto" in r for r in rejected)

    def test_verify_flags_broken_python(self):
        from core.app_builder import BuildFile, verify_files

        problems = verify_files([BuildFile("app.py", "def (roto")])
        assert any("sintaxis" in p for p in problems)

    def test_offline_build_produces_working_project(self, tmp_path):
        from core.app_builder import AppBuilder

        builder = AppBuilder(router=None, projects_root=tmp_path / "projects", serve_preview=False)
        result = builder.create("una app de lista de la compra")
        assert result.ok, result.detail
        assert result.used_template
        project = Path(result.directory)
        assert (project / "index.html").exists()
        assert (project / "app.js").read_text(encoding="utf-8").count("{") == (project / "app.js").read_text(encoding="utf-8").count("}")
        manifest = json.loads((project / ".eon-app.json").read_text(encoding="utf-8"))
        assert manifest["builder"] == "EON"
        assert result.message().startswith("Su aplicación está") or result.message().startswith("Señor")

    def test_python_stack_template_compiles(self, tmp_path):
        from core.app_builder import AppBuilder

        builder = AppBuilder(router=None, projects_root=tmp_path / "projects", serve_preview=False)
        result = builder.create("hazme un programa python para llevar gastos", stack="python")
        assert result.ok
        source = (Path(result.directory) / "app.py").read_text(encoding="utf-8")
        compile(source, "app.py", "exec")  # tiene que ser Python válido

    def test_preview_server_blocks_escape(self, tmp_path):
        import urllib.error
        import urllib.request

        from core.app_builder import PreviewServer

        (tmp_path / "index.html").write_text("<html><body>ok</body></html>", encoding="utf-8")
        server = PreviewServer(tmp_path)
        port = server.start()
        assert port, "el servidor no arrancó"
        try:
            body = urllib.request.urlopen(server.url("index.html"), timeout=3).read()
            assert b"ok" in body
            with pytest.raises(urllib.error.HTTPError) as excinfo:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/../secret", timeout=3)
            assert excinfo.value.code in (403, 404)
        finally:
            server.stop()

    def test_empty_idea_rejected(self, tmp_path):
        from core.app_builder import AppBuilder

        builder = AppBuilder(router=None, projects_root=tmp_path / "projects", serve_preview=False)
        assert not builder.create("   ").ok


# --------------------------------------------------------------------------- #
# gui — cinemática y layout (sin Qt: las escenas son objetos puros)
# --------------------------------------------------------------------------- #


class TestNotchLayout:
    def test_state_parse_aliases(self):
        from gui.notch_layout import NotchState

        assert NotchState.parse("media") is NotchState.MEDIA
        assert NotchState.parse(NotchState.LISTENING) is NotchState.LISTENING  # idempotencia con enums
        assert NotchState.parse("no-existe") is NotchState.IDLE

    def test_morph_geometry_endpoints(self):
        from gui.notch_layout import morph_geometry

        start = morph_geometry((140, 32), (260, 42), 0.0)
        end = morph_geometry((140, 32), (260, 42), 1.0)
        assert (round(start.width), round(start.height)) == (140, 32)
        assert (round(end.width), round(end.height)) == (260, 42)
        assert start.width < end.width

    def test_capsule_corner_grows_with_height_and_caps(self):
        from gui.notch_layout import capsule_corner

        small = capsule_corner(140, 32)
        big = capsule_corner(360, 80)
        assert 0 < small < big
        assert big <= 28.0 + 1e-6

    def test_soundbar_levels_bounded(self):
        from gui.notch_layout import soundbar_levels

        bars = soundbar_levels(13, phase=1.23, drive=0.8)
        assert len(bars) == 13
        assert all(0.0 <= v <= 1.0 for v in bars)

    def test_equalizer_decays_when_stopped(self):
        from gui.notch_layout import equalizer_levels

        peaks = [0.8] * 24
        now = equalizer_levels(24, phase=2.0, playing=False, decay=peaks)
        assert max(now) <= 0.8
        assert len(now) == 24

    def test_controller_transitions_and_collapse(self, monkeypatch):
        from gui.notch_layout import MediaInfo, NotchController, NotchState

        controller = NotchController(sizes=dict(config.NOTCH_SIZES), auto_collapse_s=0.05)
        controller.set_state(NotchState.LISTENING)
        assert controller.state is NotchState.LISTENING
        # avance hasta completar la animación y colapso automático por inactividad
        for _ in range(80):
            controller.tick(1 / 30)
            if not controller.morphing:
                break
        assert not controller.morphing
        time.sleep(0.07)
        controller.tick(1 / 30)
        assert controller.state is NotchState.IDLE

        controller.set_media(MediaInfo(title="Loser", artist="Tame Impala", playing=True))
        assert controller.state is NotchState.MEDIA
        assert controller.interactive
        controller.tick(1 / 30)
        assert controller.media.position_s > 0  # la tarjeta avanza sola
        controller.collapse()
        assert controller.state is NotchState.IDLE

    def test_error_state_times_out_to_idle(self):
        from gui.notch_layout import NotchController, NotchState

        controller = NotchController(sizes=dict(config.NOTCH_SIZES))
        controller.set_state(NotchState.ERROR)
        assert controller.state is NotchState.ERROR
        controller._error_until = time.monotonic() - 0.01  # acorta el reloj: el límite ya venció
        controller.tick(0.05)
        assert controller.state is NotchState.IDLE

    def test_build_scene_geometry_matches_snapshot(self):
        from gui.notch_layout import NotchController, NotchState, build_scene

        controller = NotchController(sizes=dict(config.NOTCH_SIZES))
        controller.set_state(NotchState.IDLE)
        for _ in range(40):
            controller.tick(1 / 30)
        scene = build_scene(controller.snapshot(), screen_width=760.0)
        assert abs(float(scene.width) - 760.0) < 1e-6
        assert abs(float(scene.height) - float(config.NOTCH_BAND_HEIGHT)) < 1e-6
        assert scene.shapes  # hay primitivas que pintar

    def test_accent_by_state(self):
        from gui.notch_layout import NotchState, default_accent

        assert default_accent(NotchState.IDLE) == config.COLORS.cyan
        assert default_accent(NotchState.ERROR) != config.COLORS.cyan
        assert default_accent(NotchState.MEDIA) != config.COLORS.cyan

    def test_char_state_mapping(self):
        from gui.char_kinematics import CharState
        from gui.notch_layout import NotchState, char_state_for

        assert char_state_for(NotchState.LISTENING) is CharState.LISTENING
        assert char_state_for(NotchState.SLEEPING) is CharState.SLEEPING


class TestCharKinematics:
    def test_states_advance_without_nan(self):
        from gui.char_kinematics import CharModel

        model = CharModel()
        for state in ("idle", "listening", "thinking", "speaking", "wake", "sleeping"):
            model.set_state(state)
            for _ in range(30):
                frame = model.update(1 / 60.0, level=0.5)
        values = vars(frame)
        for key, value in values.items():
            if isinstance(value, float):
                assert not math.isnan(value), (state, key)

    def test_blink_cycles(self):
        from gui.char_kinematics import CharModel

        model = CharModel()
        eyes_closed = 0
        for _ in range(60 * 20):  # 20 s deberían cubrir el rango 4-7 s con creces
            frame = model.update(1 / 60.0)
            if frame.eye_open < 0.3:
                eyes_closed += 1
        assert eyes_closed > 0, "el personaje nunca parpadea"

    def test_speaking_moves_the_mouth(self):
        from gui.char_kinematics import CharModel

        model = CharModel()
        model.set_state("speaking")
        opens = [model.update(1 / 60.0, level=level).mouth_open for level in (0.0, 1.0, 0.9, 0.2, 0.8)]
        assert max(opens) > 0.05, "la boca no se abre con el audio"

    def test_wake_wave_pulse(self):
        from gui.char_kinematics import CharModel

        model = CharModel()
        model.update(1 / 60.0)
        model.trigger_wake()
        frame = model.update(1 / 60.0)
        assert frame.halo_alpha > 0.0 or frame.eye_open > 0.9

    def test_frames_preview_complete(self):
        from gui.char_kinematics import frames_preview

        preview = frames_preview(seconds=0.5, fps=10, states=["idle", "listening"])
        assert set(preview) == {"idle", "listening"}
        assert all(len(frames) >= 4 for frames in preview.values())


# --------------------------------------------------------------------------- #
# main.py — enrutado de intención
# --------------------------------------------------------------------------- #


class TestIntentRouting:
    @pytest.mark.parametrize(
        ("text", "intent"),
        [
            ("pon música", "music"),
            ("ponme Loser de Tame Impala", "music"),
            ("tame impala, suelta loser", "music"),
            ("para la música", "music_stop"),
            ("cállate", "silence"),
            ("para", "stop_all"),
            ("basta.", "stop_all"),
            ("crea una aplicación de notas", "app_build"),
            ("créame una app para la lista de la compra", "app_build"),
            ("hazme una página web del gimnasio", "app_build"),
            ("mejórate con un modo foco", "self_upgrade"),
            ("añade una funcionalidad de pomodoro", "self_upgrade"),
            ("qué hay en pantalla", "look"),
            ("describe la pantalla", "look"),
            ("abre el bloc de notas", "automation"),
            ("haz clic en el icono de Spotify", "automation"),
            ("escribe 'hola' en el campo", "automation"),
            ("hola", "greeting"),
            ("buenos días Eon", "greeting"),
            ("gracias", "thanks"),
            ("duerme", "sleep"),
            ("explícame qué es la fotosíntesis", "chat"),
            ("   ", "ignore"),
        ],
    )
    def test_classify(self, text, intent):
        from main import classify

        assert classify(text) == intent

    def test_chat_system_is_spanish_and_tight(self):
        from main import chat_system

        text = chat_system()
        assert "español" in text
        assert "sin markdown" in text


class TestGuiSmoke:
    """La GUI de verdad, en un subproceso aislado (un fallo de Qt no debe tumbar pytest)."""

    def test_states_paint_without_exceptions(self):
        import subprocess

        script = ROOT / "tools" / "gui_smoke.py"
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
        try:
            result = subprocess.run(
                [sys.executable, str(script)], capture_output=True, text=True, timeout=90, env=env, check=False,
            )
        except subprocess.TimeoutExpired:
            pytest.skip("el arranque de Qt excede 90 s en esta máquina")
        if result.returncode == 90:
            pytest.skip("PyQt6 no está instalado")
        combined = (result.stdout or "") + (result.stderr or "")
        if result.returncode != 0 and ("platform plugin" in combined or "cannot open shared object" in combined or "no Qt platform" in combined):
            pytest.skip(f"sin plataforma Qt usable aquí: {combined.strip()[:160]}")
        assert result.returncode == 0, f"gui_smoke falló ({result.returncode}): " + combined[-1500:]

class TestOrchestratorHeadless:
    def build(self):
        from main import EonOrchestrator

        return EonOrchestrator(allow_voice=False, headless=True)

    def test_boot_status_roundtrip(self):
        orch = self.build()
        try:
            status = orch.status()
            assert status["integrity"]
            assert "killswitch" in status and status["killswitch"]["engaged"] is False
            assert json.dumps(status, ensure_ascii=False)  # serializable
        finally:
            orch.shutdown()

    def test_stop_all_engages_killswitch(self):
        orch = self.build()
        try:
            orch.handle_text("para")
            assert orch.killswitch.engaged
            orch.killswitch.release()
        finally:
            orch.shutdown()

    def test_app_intent_builds_and_reports(self):
        orch = self.build()
        heard: list[tuple[str, dict]] = []
        orch.on_event(lambda name, payload: heard.append((name, payload)))
        try:
            orch.handle_text("crea una app de recetas")
            deadline = time.time() + 12.0
            while orch.builder.busy and time.time() < deadline:
                time.sleep(0.05)
            media_or_state = [payload for name, payload in heard if name == "state"]
            assert media_or_state, "el orquestador no informó a la GUI"
        finally:
            orch.shutdown()

    def test_control_socket_answers_ping(self):
        import socket

        from main import instance_is_up

        if instance_is_up():
            pytest.skip("hay un EON real en marcha; no le piso el puerto de control")
        orch = self.build()
        try:
            orch.start()
            deadline = time.time() + 3.0
            reply = ""
            while time.time() < deadline:
                try:
                    with socket.create_connection(("127.0.0.1", 47905), timeout=0.5) as client:
                        client.sendall(b"ping")
                        reply = client.recv(16).decode().strip()
                        break
                except OSError:
                    time.sleep(0.1)
            assert reply == "pong"
        finally:
            orch.shutdown()


# --------------------------------------------------------------------------- #
# integración suave: el pipeline de la palmada (sin GUI)
# --------------------------------------------------------------------------- #


class TestClapPipeline:
    def test_clap_triggers_greeting_and_media_event(self):
        from core.acoustic_detector import LaunchResult
        from main import EonOrchestrator

        orch = EonOrchestrator(allow_voice=False, headless=True)
        packets: list[tuple[str, dict]] = []
        orch.on_event(lambda name, payload: packets.append((name, payload)))
        try:
            # sustituir el lanzador real (no hay Spotify/Comet en la prueba)
            result = LaunchResult(True, "Comet", "simulado", ("fake",))
            greeting = "Buenas noches Pablo"
            orch._on_clap(greeting, result)
            names = [name for name, _ in packets]
            assert "wake" in names
            assert "media" in names
            media = next(payload for name, payload in packets if name == "media")
            assert media["title"] == "Loser" and media["artist"] == "Tame Impala"
            assert media["playing"] is True
        finally:
            orch.shutdown()

    def test_music_flow_uses_launcher_result(self):
        from core.acoustic_detector import LaunchResult
        from main import EonOrchestrator

        orch = EonOrchestrator(allow_voice=False, headless=True)
        try:
            class FakeLauncher:
                def play(self):
                    return LaunchResult(True, "Spotify", "ok", ("x",))

                def stop(self):
                    return LaunchResult(True, "Spotify", "pausa", ())

            orch.ear.launcher = FakeLauncher()  # type: ignore[assignment]
            packets: list[tuple[str, dict]] = []
            orch.on_event(lambda name, payload: packets.append((name, payload)))
            orch.play_music()
            media = [p for n, p in packets if n == "media"]
            assert media and media[0]["source"] == "Spotify"
        finally:
            orch.shutdown()
