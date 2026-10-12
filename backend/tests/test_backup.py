import sqlite3
import tarfile

from backup import backup


def test_backup_has_database_and_media_and_keeps_only_the_newest(tmp_path):
    db = tmp_path / "shop.db"
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE orders (id INTEGER)")
        c.execute("INSERT INTO orders VALUES (7)")
    (tmp_path / "media").mkdir()
    (tmp_path / "media" / "voice.ogg").write_bytes(b"OGG")

    out = backup(db, tmp_path / "media", tmp_path / "backups", keep=1)
    with tarfile.open(out) as tar:
        assert {"shop.db", "media/voice.ogg"} <= set(tar.getnames())
        tar.extract("shop.db", tmp_path / "restored")
    assert sqlite3.connect(tmp_path / "restored" / "shop.db").execute("SELECT id FROM orders").fetchall() == [(7,)]

    out.rename(out.with_name("mitu-00000000-000000.tar.gz"))  # pretend it's an old one
    newest = backup(db, tmp_path / "media", tmp_path / "backups", keep=1)
    assert list((tmp_path / "backups").glob("mitu-*.tar.gz")) == [newest]
