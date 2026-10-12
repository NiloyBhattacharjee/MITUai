"""Back up the database and the media folder (voice notes, photos) into one .tar.gz, keeping the newest BACKUP_KEEP.

    python backup.py     (docker-compose's backup service runs it once a day)

A backup on the same disk doesn't survive losing the server: copy BACKUP_DIR somewhere else too (rclone, aws s3 sync).
"""
import os
import sqlite3
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent


def backup(db_path: Path, media: Path, out_dir: Path, keep: int = 14) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"mitu-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.tar.gz"
    with tempfile.TemporaryDirectory() as tmp:
        snapshot = Path(tmp) / "shop.db"
        src, dst = sqlite3.connect(db_path), sqlite3.connect(snapshot)
        src.backup(dst)  # a consistent copy even while the app is writing
        src.close(), dst.close()
        partial = out.with_suffix(".partial")  # a crash mid-write never leaves a half archive that looks finished
        with tarfile.open(partial, "w:gz") as tar:
            tar.add(snapshot, "shop.db")
            if media.is_dir():
                # ponytail: full copy of media every day; switch to rsync/restic snapshots once media is gigabytes.
                tar.add(media, "media")
        partial.rename(out)
    for old in sorted(out_dir.glob("mitu-*.tar.gz"))[:-keep]:
        old.unlink()
    return out


if __name__ == "__main__":
    url = os.environ.get("DATABASE_URL", f"sqlite:///{HERE / 'shop.db'}")
    if not url.startswith("sqlite:///"):
        raise SystemExit("backup.py only backs up SQLite; use pg_dump for Postgres.")
    print("backed up to", backup(Path(url.removeprefix("sqlite:///")), Path(os.environ.get("MEDIA_DIR", HERE / "media")),
                                 Path(os.environ.get("BACKUP_DIR", HERE / "backups")), int(os.environ.get("BACKUP_KEEP", "14"))))
