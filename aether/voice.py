from __future__ import annotations

import argparse
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path
from typing import Callable, Iterable, Iterator

from .config import DATA_DIR, get, load, resolve

SAMPLE_RATE = 16000
BLOCK = 1600


def _clean(text: str) -> str:
    text = re.sub(r"\[[\d:.\s\->]+\]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    low = text.lower().strip(" .!?")
    if low in {
        "", "[blank_audio]", "(silence)", "[silence]", "[music]", "[applause]",
        "thank you", "thanks for watching", "you", "bye", ".",
    }:
        return ""
    return text


_SENTENCE_END = re.compile(r"[.!?][\"')\]]?(?:\s+|$)|\n+")


def _consume_fences(text: str, in_fence: bool) -> tuple[str, bool]:
    out: list[str] = []
    while text:
        if "```" not in text:
            if not in_fence:
                out.append(text)
            break
        before, _, after = text.partition("```")
        if not in_fence:
            out.append(before)
        in_fence = not in_fence
        text = after
    return "".join(out), in_fence


def _take_sentence(buffer: str) -> tuple[str, str]:
    match = _SENTENCE_END.search(buffer)
    if match:
        cut = match.end()
        return buffer[:cut].strip(), buffer[cut:]
    if len(buffer) >= 240:
        cut = max(buffer.rfind(" "), 200)
        if cut > 0:
            return buffer[:cut].strip(), buffer[cut:]
    return "", buffer


def speech_segments(parts: Iterable[str]) -> Iterator[str]:
    buffer = ""
    in_fence = False
    for part in parts:
        text, in_fence = _consume_fences(str(part), in_fence)
        buffer += text
        while True:
            sentence, buffer = _take_sentence(buffer)
            if not sentence:
                break
            clean = _clean(sentence)
            clean = re.sub(r"<tool>.*?</tool>", " ", clean, flags=re.I | re.S).strip()
            if clean:
                yield clean
    clean = _clean(buffer)
    clean = re.sub(r"<tool>.*?</tool>", " ", clean, flags=re.I | re.S).strip()
    if clean:
        yield clean


class VoiceAssistant:
    def __init__(self, on_transcript: Callable[[str], None] | None = None,
                 on_status: Callable[[str], None] | None = None,
                 on_state: Callable[[str], None] | None = None,
                 on_wake: Callable[[], None] | None = None,
                 speak_lock=None) -> None:
        self.on_transcript = on_transcript or (lambda _t: None)
        self.on_status = on_status or (lambda _s: None)
        self.on_state = on_state or (lambda _s: None)
        self.on_wake = on_wake or (lambda: None)
        self.cfg = load()["voice"]
        self.mic_device = self.cfg.get("mic_device")
        self._stop = threading.Event()
        self._trigger = threading.Event()
        self._busy = threading.Event()
        self._speaking = threading.Event()
        self._speak_stop = threading.Event()
        self._turn_done = threading.Event()
        self._thread: threading.Thread | None = None
        self._tts_thread: threading.Thread | None = None
        self._tts_lock = threading.Lock()
        self._tts_stream = None
        self._tts_gen = 0
        self._stream_queue: queue.Queue[str | None] | None = None
        self._streaming = False
        self._piper = None
        self._kws = None
        self._last_error = ""

    def paths(self) -> dict[str, Path]:
        return {
            "whisper": resolve(self.cfg.get("whisper_binary", "runtime/whisper/whisper-cli.exe")),
            "whisper_model": resolve(self.cfg.get("whisper_model", "models/ggml-base.en.bin")),
            "piper": resolve(self.cfg.get("piper_model", "models/piper/en_US-lessac-medium.onnx")),
            "kws": resolve(self.cfg.get("kws_model_dir", "models/kws")),
        }

    def missing(self) -> list[str]:
        p = self.paths()
        missing = []
        for key in ("whisper", "whisper_model", "piper"):
            if not p[key].exists():
                missing.append(str(p[key]))
        if not (p["piper"].with_suffix(p["piper"].suffix + ".json")).exists():
            missing.append(str(p["piper"]) + ".json")
        kws = p["kws"]
        for name in (
            "tokens.txt", "en.phone", "keywords.txt",
            "encoder-epoch-13-avg-2-chunk-16-left-64.int8.onnx",
            "decoder-epoch-13-avg-2-chunk-16-left-64.onnx",
            "joiner-epoch-13-avg-2-chunk-16-left-64.int8.onnx",
        ):
            if not (kws / name).exists():
                missing.append(str(kws / name))
        return missing

    def available(self) -> bool:
        return not self.missing()

    @property
    def last_error(self) -> str:
        return self._last_error

    def _keyword_spotter(self):
        if self._kws is not None:
            return self._kws
        import sherpa_onnx

        p = self.paths()
        kws = p["kws"]
        self._kws = sherpa_onnx.KeywordSpotter(
            tokens=str(kws / "tokens.txt"),
            encoder=str(kws / "encoder-epoch-13-avg-2-chunk-16-left-64.int8.onnx"),
            decoder=str(kws / "decoder-epoch-13-avg-2-chunk-16-left-64.onnx"),
            joiner=str(kws / "joiner-epoch-13-avg-2-chunk-16-left-64.int8.onnx"),
            keywords_file=str(kws / "keywords.txt"),
            num_threads=max(1, int(get("idle.kws_threads", 1))),
            sample_rate=SAMPLE_RATE,
            max_active_paths=4,
            keywords_score=2.5,
            keywords_threshold=float(self.cfg.get("wake_threshold", 0.15)),
            num_trailing_blanks=3,
            provider="cpu",
        )
        return self._kws

    def _piper_voice(self):
        if self._piper is not None:
            return self._piper
        from piper import PiperVoice

        p = self.paths()["piper"]
        self._piper = PiperVoice.load(str(p), str(p) + ".json", use_cuda=False)
        return self._piper

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        missing = self.missing()
        if missing:
            raise RuntimeError("Voice models are missing:\n" + "\n".join(missing))
        self._stop.clear()
        self._thread = threading.Thread(target=self._wake_loop, name="thambi-voice", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._trigger.set()
        self.interrupt()
        if self._thread:
            self._thread.join(timeout=3)
        self._thread = None
        self.on_state("Voice: off")

    def listen_once(self) -> None:
        self._trigger.set()

    def interrupt(self) -> None:
        self._speak_stop.set()
        self._streaming = False
        pending = self._stream_queue
        if pending is not None:
            pending.put(None)
        self._halt_tts_stream()

    def turn_finished(self) -> None:
        self._turn_done.set()

    def _halt_tts_stream(self) -> None:
        stream = self._tts_stream
        self._tts_stream = None
        if stream is not None:
            try:
                stream.abort()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass

    def _reset_speech(self) -> None:
        self._speak_stop.set()
        self._streaming = False
        pending = self._stream_queue
        self._stream_queue = None
        if pending is not None:
            pending.put(None)
        self._halt_tts_stream()
        if self._tts_thread and self._tts_thread.is_alive():
            self._tts_thread.join(timeout=2.5)
        self._speak_stop.clear()

    def speak(self, text: str) -> None:
        text = text.strip()
        if not text or not self.cfg.get("tts_enabled", True):
            return
        with self._tts_lock:
            self._reset_speech()
            self._speaking.set()
            self._tts_thread = threading.Thread(target=self._speak_worker, args=(text,),
                                                name="thambi-tts", daemon=True)
            self._tts_thread.start()

    def begin_stream(self) -> bool:
        if not self.cfg.get("tts_enabled", True) or not self.cfg.get("stream_tts", True):
            return False
        with self._tts_lock:
            self._reset_speech()
            pending: queue.Queue[str | None] = queue.Queue()
            self._stream_queue = pending
            self._streaming = True
            self._speaking.set()
            self.on_status("Speaking live ...")
            self._tts_thread = threading.Thread(target=self._stream_worker, args=(pending,),
                                                name="thambi-tts", daemon=True)
            self._tts_thread.start()
            return True

    def feed_stream(self, text: str) -> None:
        if text and self._streaming and self._stream_queue is not None:
            self._stream_queue.put(text)

    def end_stream(self, unstreamed_text: str = "") -> None:
        pending = self._stream_queue
        if pending is None:
            return
        if unstreamed_text:
            pending.put(unstreamed_text)
        pending.put(None)
        self._streaming = False

    def _speakable(self, text: str) -> str:
        clean = _clean(text)
        return re.sub(r"<tool>.*?</tool>", " ", clean, flags=re.I | re.S).strip()

    def _speak_worker(self, text: str) -> None:
        try:
            self._play_texts([text])
        finally:
            self._speaking.clear()

    def _stream_worker(self, pending: queue.Queue[str | None]) -> None:
        try:
            self._play_texts(self._live_segments(pending))
        finally:
            self._stream_queue = None
            self._streaming = False
            self._speaking.clear()

    def _live_segments(self, pending: queue.Queue[str | None]) -> Iterator[str]:
        buffer = ""
        in_fence = False
        while True:
            try:
                item = pending.get(timeout=0.2)
            except queue.Empty:
                if self._speak_stop.is_set() or self._stop.is_set():
                    break
                continue
            if item is None:
                break
            text, in_fence = _consume_fences(item, in_fence)
            buffer += text
            while True:
                sentence, buffer = _take_sentence(buffer)
                if not sentence:
                    break
                clean = self._speakable(sentence)
                if clean:
                    yield clean
        clean = self._speakable(buffer)
        if clean:
            yield clean

    def _play_texts(self, texts: Iterable[str]) -> None:
        try:
            import numpy as np
            import sounddevice as sd

            voice = self._piper_voice()
            stream = None
            rate = 0
            block = 1024
            try:
                for text in texts:
                    if self._speak_stop.is_set() or self._stop.is_set():
                        break
                    chunks = list(voice.synthesize(text))
                    if not chunks:
                        continue
                    if stream is None:
                        rate = int(chunks[0].sample_rate)
                        block = max(1024, rate // 10)
                        stream = sd.OutputStream(samplerate=rate, channels=1, dtype="float32",
                                                 blocksize=0, latency="high")
                        self._tts_stream = stream
                        stream.start()
                    audio = np.concatenate([
                        np.asarray(chunk.audio_float_array, dtype=np.float32).reshape(-1)
                        for chunk in chunks
                    ])
                    if audio.size == 0:
                        continue
                    audio = np.clip(audio, -1.0, 1.0)
                    for i in range(0, audio.size, block):
                        if self._speak_stop.is_set() or self._stop.is_set():
                            break
                        stream.write(audio[i:i + block].reshape(-1, 1))
            finally:
                current = self._tts_stream
                self._tts_stream = None
                if current is not None:
                    try:
                        current.stop()
                    except Exception:
                        pass
                    try:
                        current.close()
                    except Exception:
                        pass
        except Exception as e:
            self._last_error = f"{type(e).__name__}: {e}"
            self.on_status(f"Voice output error: {e}")

    def _wait_for_tts(self, timeout: float = 240.0) -> None:
        deadline = time.time() + timeout
        while self._speaking.is_set() and not self._stop.is_set() and time.time() < deadline:
            time.sleep(0.05)

    def _open_mic(self):
        import sounddevice as sd

        return sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            blocksize=BLOCK,
            device=self.mic_device,
        )

    def _wake_loop(self) -> None:
        try:
            kws = self._keyword_spotter()
            self.on_state("Voice: on")
            self.on_status("Voice ready - say 'Hey Thambi'")
            while not self._stop.is_set():
                if not self._listen_for_wake(kws):
                    continue
                if self._stop.is_set():
                    break
                self._notify_wake()
                self._handle_conversation()
        except Exception as e:
            self._last_error = f"{type(e).__name__}: {e}"
            self.on_status(f"Voice error: {e}")
            self.on_state("Voice: error")
        finally:
            if self._stop.is_set():
                self.on_state("Voice: off")

    def _listen_for_wake(self, kws) -> bool:
        import numpy as np
        from collections import deque

        stream = kws.create_stream()
        gate = bool(get("idle.enabled", True)) and bool(get("idle.energy_gate", True))
        threshold = float(get("idle.energy_threshold", self.cfg.get("vad_threshold", 0.012)))
        pre_roll = deque(maxlen=max(1, int(get("idle.pre_roll_blocks", 3))))
        silent = True
        with self._open_mic() as mic:
            while not self._stop.is_set():
                data, overflowed = mic.read(BLOCK)
                if overflowed:
                    self.on_status("Voice input overflow; audio may be choppy")
                block = data.reshape(-1)
                if self._trigger.is_set():
                    self._trigger.clear()
                    kws.reset_stream(stream)
                    return True
                if self._speaking.is_set() or self._busy.is_set():
                    pre_roll.clear()
                    silent = True
                    continue
                if gate:
                    rms = float(np.sqrt(np.mean(block * block))) if block.size else 0.0
                    if rms < threshold:
                        pre_roll.append(block.copy())
                        silent = True
                        continue
                    if silent:
                        for prev in pre_roll:
                            stream.accept_waveform(SAMPLE_RATE, prev)
                        pre_roll.clear()
                        silent = False
                stream.accept_waveform(SAMPLE_RATE, block)
                while kws.is_ready(stream):
                    kws.decode_stream(stream)
                    if kws.get_result(stream):
                        kws.reset_stream(stream)
                        return True
        return False

    def _notify_wake(self) -> None:
        try:
            self.on_wake()
        except Exception:
            pass

    def _handle_conversation(self) -> None:
        self._busy.set()
        first = True
        try:
            while not self._stop.is_set():
                self.on_state("Voice: on - listening" if first else "Voice: follow-up")
                timeout = float(self.cfg.get("vad_start_timeout_s", 6)) if first else float(
                    self.cfg.get("followup_timeout_s", 8))
                wav_path = self._record_utterance(start_timeout=timeout)
                if not wav_path:
                    if first:
                        self.on_status("Voice: no speech detected")
                    break
                self.on_status("Voice: transcribing locally ...")
                text = self.transcribe(wav_path)
                if not text:
                    self.on_status("Voice: speech was not understood")
                    break
                if not first and self._is_stop_phrase(text):
                    self.on_status("Voice: conversation ended")
                    break
                self.on_status("Voice: thinking ...")
                self._turn_done.clear()
                self.on_transcript(text)
                self._turn_done.wait(timeout=float(self.cfg.get("turn_timeout_s", 120)))
                self._wait_for_tts()
                first = False
                if not bool(self.cfg.get("continuous", True)):
                    break
        except Exception as e:
            self._last_error = f"{type(e).__name__}: {e}"
            self.on_status(f"Voice error: {e}")
        finally:
            self._turn_done.clear()
            self._busy.clear()
            if not self._stop.is_set():
                self.on_state("Voice: on")
                self.on_status("Voice ready - say 'Hey Thambi'")

    @staticmethod
    def _is_stop_phrase(text: str) -> bool:
        low = re.sub(r"[^a-z' ]+", " ", text.lower())
        low = re.sub(r"\s+", " ", low).strip()
        return low in {
            "stop", "stop listening", "stop talking", "cancel", "never mind", "nevermind",
            "that's all", "thats all", "goodbye", "good bye", "bye", "thank you", "thanks",
            "shut up", "enough", "quiet",
        }

    def _record_utterance(self, start_timeout: float | None = None) -> Path | None:
        import numpy as np

        DATA_DIR.joinpath("voice").mkdir(parents=True, exist_ok=True)
        threshold = float(self.cfg.get("vad_threshold", 0.012))
        silence_needed = float(self.cfg.get("vad_silence_ms", 900)) / 1000.0
        max_seconds = float(self.cfg.get("max_record_seconds", 15))
        if start_timeout is None:
            start_timeout = float(self.cfg.get("vad_start_timeout_s", 6))
        block_seconds = BLOCK / SAMPLE_RATE
        frames: list = []
        speech_started = False
        silence = 0.0
        began = time.time()
        with self._open_mic() as mic:
            while not self._stop.is_set():
                data, _ = mic.read(BLOCK)
                block = data.reshape(-1)
                frames.append(block.copy())
                rms = float(np.sqrt(np.mean(block * block))) if block.size else 0.0
                elapsed = time.time() - began
                if not speech_started:
                    if rms >= threshold:
                        speech_started = True
                        silence = 0.0
                    elif elapsed >= start_timeout:
                        return None
                else:
                    if rms < threshold:
                        silence += block_seconds
                    else:
                        silence = 0.0
                    if silence >= silence_needed or elapsed >= max_seconds:
                        break
        if not speech_started or not frames:
            return None
        audio = np.concatenate(frames)
        peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        if peak > 0:
            audio = audio / peak * 0.9
        pcm = np.clip(audio * 32767, -32768, 32767).astype("int16")
        path = DATA_DIR / "voice" / f"utterance_{int(time.time() * 1000)}.wav"
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(pcm.tobytes())
        return path

    def transcribe(self, wav_path: str | Path) -> str:
        p = self.paths()
        binary = p["whisper"]
        model = p["whisper_model"]
        if not binary.exists():
            raise FileNotFoundError(f"whisper-cli not found: {binary}")
        if not model.exists():
            raise FileNotFoundError(f"Whisper model not found: {model}")
        with tempfile.TemporaryDirectory(prefix="aether_stt_") as td:
            out_base = Path(td) / "result"
            cmd = [
                str(binary),
                "-m", str(model),
                "-f", str(wav_path),
                "-l", "en",
                "-t", str(int(self.cfg.get("whisper_threads", 6))),
                "-nt",
                "-np",
                "-otxt",
                "-of", str(out_base),
            ]
            proc = subprocess.run(
                cmd,
                cwd=str(binary.parent),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
                creationflags=0x08000000,
            )
            txt = out_base.with_suffix(".txt")
            if txt.exists():
                raw = txt.read_text(encoding="utf-8", errors="replace")
            else:
                raw = (proc.stdout or "") + (proc.stderr or "")
            if proc.returncode != 0 and not raw.strip():
                raise RuntimeError(f"whisper-cli failed ({proc.returncode}): {raw[-500:]}")
            return _clean(raw)

    def synthesize_to_wav(self, text: str, path: str | Path) -> Path:
        import numpy as np

        voice = self._piper_voice()
        chunks = list(voice.synthesize(text))
        if not chunks:
            raise RuntimeError("Piper produced no audio")
        rate = int(chunks[0].sample_rate)
        audio = np.concatenate([np.asarray(c.audio_float_array, dtype=np.float32).reshape(-1) for c in chunks])
        pcm = np.clip(audio * 32767, -32768, 32767).astype("int16")
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(out), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(rate)
            wf.writeframes(pcm.tobytes())
        return out


def _check() -> int:
    v = VoiceAssistant()
    missing = v.missing()
    if missing:
        print("Voice is NOT ready. Missing:")
        for m in missing:
            print(" -", m)
        print("Run: powershell -ExecutionPolicy Bypass -File scripts\\setup_voice.ps1")
        return 1
    try:
        import numpy  # noqa: F401
        import sounddevice  # noqa: F401
        import sherpa_onnx  # noqa: F401
        import piper  # noqa: F401

        v._keyword_spotter()
        with tempfile.TemporaryDirectory(prefix="aether_voice_check_") as td:
            wav = v.synthesize_to_wav("Thambi voice check. The local speech engine is working.", Path(td) / "check.wav")
            text = v.transcribe(wav)
            print("Piper TTS: OK")
            print("Whisper STT: OK")
            print("Heard:", text or "(nothing intelligible)")
            if not text:
                return 2
        print("Wake-word model: OK")
    except Exception as e:
        print(f"Voice self-check failed: {type(e).__name__}: {e}")
        return 3
    print("Voice is ready.")
    return 0


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="aether.voice")
    ap.add_argument("--check", action="store_true", help="validate models and round-trip TTS/STT")
    ap.add_argument("--listen", action="store_true", help="start wake-word listening until Ctrl+C")
    ap.add_argument("--say", help="speak the supplied text")
    args = ap.parse_args(argv)
    if args.check:
        return _check()
    if args.say:
        VoiceAssistant().speak(args.say)
        time.sleep(1)
        return 0
    if args.listen:
        def got(text: str) -> None:
            print("You said:", text)
        def status(text: str) -> None:
            print(text)
        v = VoiceAssistant(on_transcript=got, on_status=status)
        v.start()
        try:
            while True:
                time.sleep(0.5)
        except KeyboardInterrupt:
            v.stop()
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(_main())
