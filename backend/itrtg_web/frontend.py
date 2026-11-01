from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles


def mount_frontend(app: FastAPI, dist_directory: Path) -> None:
    """Serve an optional production build after the application's API routes."""
    if (dist_directory / "index.html").is_file():
        app.mount("/", StaticFiles(directory=dist_directory, html=True), name="frontend")
