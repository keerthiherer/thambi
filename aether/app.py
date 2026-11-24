from __future__ import annotations

import argparse
import json
import sys
import threading
import time

from .config import ensure_dirs
from .hardware import probe


def _cli(module: str = "chat", listen: bool = False) -> int:
    from .agent import ChatEngine
    from .llm import LlamaServer

    ensure_dirs()
    print("THAMBI CLI - private local assistant. /help for commands, /exit to quit.")
    if module == "research":
        print("Research mode active - ask reasoning questions about research, projects, hackathons.")
    server = LlamaServer()
    if not server.health():
        print("Starting llama.cpp ...")
        try:
            server.start(print)
        except Exception as e:
            print(f"ERROR: {e}")
            return 1
    engine = ChatEngine(server)
    voice = None
    if listen:
        try:
            from .voice import VoiceAssistant

            def on_voice(text: str) -> None:
                print(f"\nYou (voice)> {text}")
                print("\nTHAMBI> ", end="", flush=True)
                try:
                    res = engine.reply(text, module)
                    answer = res.get("text") or ""
                    print(answer)
                    if answer:
                        va.speak(answer)
                except Exception as e:
                    print(f"\nERROR: {e}")
                finally:
                    va.turn_finished()

            va = VoiceAssistant(
                on_transcript=on_voice,
                on_status=lambda s: print(f"[voice] {s}"),
            )
            if va.available():
                va.start()
                voice = va
                print("Voice listening - say 'Hey Thambi' (handsfree follow-ups enabled)")
            else:
                print("Voice models missing; use scripts\\setup_voice.ps1")
        except Exception as e:
            print(f"Voice unavailable: {e}")
    try:
        while True:
            try:
                text = input("\nYou> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not text:
                continue
            if text.lower() in ("/exit", "/quit"):
                break
            if text == "/clear":
                engine.command("/clear")
                print("\033[2J\033[H", end="")
                continue
            print("\nTHAMBI> ", end="", flush=True)
            stop = threading.Event()
            try:
                res = engine.reply(text, module, on_token=lambda t: print(t, end="", flush=True), stop_event=stop)
            except KeyboardInterrupt:
                stop.set()
                print("\n[stopped]")
                continue
            except Exception as e:
                print(f"\nERROR: {e}")
                continue
            print()
            if res.get("sources"):
                print("\nSources:")
                seen_sources: set[str] = set()
                source_number = 0
                for s in res["sources"]:
                    path = str(s.get("path") or "").strip()
                    key = path.lower().rstrip("/")
                    if not key or key in seen_sources:
                        continue
                    seen_sources.add(key)
                    source_number += 1
                    print(f"  [S{source_number}] {path}")
            if res.get("tool_results"):
                for t in res["tool_results"]:
                    print(f"  [tool {t['name']}: {'ok' if t['ok'] else 'error'}]")
            if res.get("elapsed"):
                print(f"  ({res['elapsed']}s)")
    finally:
        try:
            if voice:
                voice.stop()
        except Exception:
            pass
        server.stop()
    return 0


def _utcp_serve() -> int:
    from .utcp import UtcpServer

    ensure_dirs()
    srv = UtcpServer()
    url = srv.start()
    print(f"THAMBI UTCP gateway listening on {url}")
    print(f"Manual: {url}/utcp/manual")
    print("Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        srv.stop()
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="thambi", description="THAMBI private offline assistant")
    ap.add_argument("--cli", action="store_true", help="text-only interface")
    ap.add_argument("--module", default="chat",
                    choices=["chat", "research", "coding", "ece", "desktop", "ceo", "psychology", "hr"],
                    help="CLI module (default chat)")
    ap.add_argument("--listen", action="store_true", help="always-on 'Hey Thambi' hot word in CLI")
    ap.add_argument("--utcp", action="store_true", help="run the UTCP tool gateway and exit on Ctrl+C")
    ap.add_argument("--probe", action="store_true", help="print hardware report and exit")
    ap.add_argument("--version", action="store_true")
    args = ap.parse_args(argv)
    if args.version:
        from . import __version__
        print(__version__)
        return 0
    if args.probe:
        print(json.dumps(probe(), indent=2))
        return 0
    if args.utcp:
        return _utcp_serve()
    if args.cli:
        return _cli(args.module, args.listen)
    ensure_dirs()
    from .ui import run
    run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
