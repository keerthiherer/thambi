from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from aether.voice import _clean, VoiceAssistant, speech_segments


class TestStreamingSpeech(unittest.TestCase):
    def test_speech_segments_split_and_filter_tools(self):
        parts = [
            "Hello world. This is still the first answer. ",
            "```tool\n{\"name\": \"write_file\", \"args\": {}}\n```",
            "Second sentence! Done.",
        ]
        self.assertEqual(
            list(speech_segments(parts)),
            ["Hello world.", "This is still the first answer.", "Second sentence!", "Done."],
        )

    def test_live_stream_speaks_before_finish(self):
        va = VoiceAssistant()
        spoken: list[str] = []

        def fake_play(texts):
            for text in texts:
                spoken.append(text)

        with patch.object(va, "_play_texts", side_effect=fake_play):
            self.assertTrue(va.begin_stream())
            va.feed_stream("First line. ")
            deadline = time.time() + 2
            while not spoken and time.time() < deadline:
                time.sleep(0.02)
            self.assertEqual(spoken, ["First line."])
            va.feed_stream("Second line! ")
            va.end_stream()
            deadline = time.time() + 3
            while va._speaking.is_set() and time.time() < deadline:
                time.sleep(0.02)

        self.assertEqual(spoken, ["First line.", "Second line!"])

    def test_stream_tts_can_be_disabled(self):
        va = VoiceAssistant()
        va.cfg["stream_tts"] = False
        self.assertFalse(va.begin_stream())


class TestVoice(unittest.TestCase):
    def test_clean_stt(self):
        self.assertEqual(_clean("[00:01.000 --> 00:03.000] Hello world"), "Hello world")
        self.assertEqual(_clean("[silence]"), "")
        self.assertEqual(_clean("[music]"), "")
        self.assertEqual(_clean("Thanks for watching."), "")

    def test_models_present(self):
        va = VoiceAssistant()
        missing = va.missing()
        self.assertEqual(missing, [], f"Voice models missing: {missing}")
        self.assertTrue(va.available())

    def test_tts_roundtrip(self):
        va = VoiceAssistant()
        with tempfile.TemporaryDirectory() as td:
            wav_path = Path(td) / "test_tts.wav"
            out = va.synthesize_to_wav("Aether system online.", wav_path)
            self.assertTrue(out.exists())
            self.assertGreater(out.stat().st_size, 1000)

            # Transcribe back with whisper
            text = va.transcribe(out)
            self.assertTrue(len(text) > 0)
            self.assertIn("online", text.lower())


if __name__ == "__main__":
    unittest.main()
