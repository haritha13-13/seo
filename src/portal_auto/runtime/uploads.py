"""A Run's uploaded Input files: a private folder `<root>/uploads/run-<id>` that never outlives the Run."""
import os
import shutil
import stat
import time
from collections.abc import Mapping
from pathlib import Path

from portal_auto.inputs import UploadedFile, safe_filename
from portal_auto.runtime.session_files import PRIVATE_FILE_MODE

PRIVATE_DIR_MODE = 0o700
RUN_DIR_PREFIX = "run-"


def run_uploads_dir(uploads_dir: Path, run_id: int) -> Path:
    return uploads_dir / f"{RUN_DIR_PREFIX}{run_id}"


def write_uploads(folder: Path, files: Mapping[str, UploadedFile]) -> dict[str, Path]:
    """Writes each file as `<folder>/<Input name>/<its base name>` and returns where each one went."""
    _private_dir(folder.parent, exist_ok=True)
    _private_dir(folder, exist_ok=False)
    return {name: _write(folder / name, file) for name, file in files.items()}


def remove_uploads(folder: Path) -> None:
    shutil.rmtree(folder, ignore_errors=True)


def sweep_stale_uploads(uploads_dir: Path, max_age_seconds: float) -> None:
    """Removes Run folders a killed worker could not clean up; younger ones may belong to a live Run."""
    cutoff = time.time() - max_age_seconds
    for folder in uploads_dir.glob(f"{RUN_DIR_PREFIX}*"):
        try:
            info = folder.lstat()
        except OSError:
            continue
        if stat.S_ISDIR(info.st_mode) and info.st_mtime < cutoff:
            remove_uploads(folder)


def _private_dir(path: Path, exist_ok: bool) -> None:
    path.mkdir(mode=PRIVATE_DIR_MODE, parents=exist_ok, exist_ok=exist_ok)
    os.chmod(path, PRIVATE_DIR_MODE)


def _write(directory: Path, file: UploadedFile) -> Path:
    _private_dir(directory, exist_ok=False)
    path = directory / safe_filename(file.filename)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, PRIVATE_FILE_MODE)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(file.content)
    return path.absolute()
