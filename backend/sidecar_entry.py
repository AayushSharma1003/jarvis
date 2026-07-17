"""PyInstaller entrypoint (the spec needs a script, not a module)."""

from jarvis_backend.main import run

if __name__ == "__main__":
    run()
