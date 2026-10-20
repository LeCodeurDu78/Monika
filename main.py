"""Monika's entry point."""

from agent import run_monika, run_monika_voice

if __name__ == "__main__":
    r = input("Voix ? (y/n): ").lower()
    if r == "y":
        run_monika_voice()
    else:
        run_monika()