#!/usr/bin/env python3
"""Terminal interactive CLI chat for AirGuide assistant.

Usage:
    python cli.py
"""
import sys
from airguide.assistant import respond
from airguide.context import UserContext


def main() -> int:
    """Run interactive terminal session with AirGuide."""
    print("AirGuide - Urban Air Quality Assistant (type 'quit' to exit)")
    print("Tell me your city or today's AQI to begin.\n")
    state = UserContext()
    while True:
        try:
            message = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting AirGuide. Stay safe!")
            return 0
        if message.lower() in {"quit", "exit"}:
            print("Exiting AirGuide. Stay safe!")
            return 0
        if not message:
            continue
        reply = respond(message, state)
        state = reply.state
        print(f"\nAirGuide: {reply.text}\n")


if __name__ == "__main__":
    sys.exit(main())
