# SPDX-License-Identifier: AGPL-3.0-or-later
"""#230: dump_stream() streams the snapshot without buffering it in memory."""

import sqlite3
import subprocess

import pytest

from zephyrex.extensions.backup_restore.BLL_Backup import SqliteBackupCommand


def _make_db(path) -> None:
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE widgets (id INTEGER PRIMARY KEY, name TEXT)")
    con.execute("INSERT INTO widgets (name) VALUES ('alpha')")
    con.commit()
    con.close()


class TestSqliteDumpStream:
    def test_streams_full_dump(self, tmp_path):
        db = tmp_path / "src.db"
        _make_db(db)
        cmd = SqliteBackupCommand(db_path=str(db))
        with cmd.dump_stream() as stream:
            data = stream.read()
        assert b"CREATE TABLE widgets" in data
        assert b"alpha" in data
        # Streamed output equals the buffered dump() byte-for-byte.
        assert data == cmd.dump()

    def test_nonzero_exit_raises(self, tmp_path):
        # A directory is not a valid SQLite DB, so sqlite3 exits non-zero -- the
        # context manager must surface that on exit rather than silently
        # succeeding with a truncated/empty artifact.
        cmd = SqliteBackupCommand(db_path=str(tmp_path))
        with pytest.raises(subprocess.CalledProcessError):
            with cmd.dump_stream() as stream:
                stream.read()
