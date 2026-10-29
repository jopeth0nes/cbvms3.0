"""Disposable display tombstones, never business logs or evidence.

Exact membership cannot both forget old identities and guarantee that polling
will never resurrect them. SQLite's temporary, disk-backed database keeps the
page cache bounded without approximate membership or an unbounded Python set.
Only hashed display identities are stored; closing the session deletes it.
"""
import hashlib
import sqlite3


class ActivityDismissals:
    def __init__(self):
        # Empty filename creates a private temporary database, removed on close.
        self.connection = sqlite3.connect('')
        self.connection.execute('PRAGMA cache_size=-64')
        self.connection.execute('PRAGMA mmap_size=0')
        self.connection.execute('PRAGMA journal_mode=OFF')
        self.connection.execute('PRAGMA synchronous=OFF')
        self.connection.execute('CREATE TABLE dismissed (identity BLOB PRIMARY KEY) WITHOUT ROWID')

    @staticmethod
    def _digest(key):
        return hashlib.sha256(key.encode()).digest()

    def contains(self, *keys):
        if self.connection is None:
            return True  # A destroyed session never accepts further cards.
        return any(self.connection.execute('SELECT 1 FROM dismissed WHERE identity=?',
                   (self._digest(key),)).fetchone() is not None for key in keys if key)

    def add(self, keys):
        if self.connection is not None:
            with self.connection:
                self.connection.executemany('INSERT OR IGNORE INTO dismissed VALUES (?)',
                    ((self._digest(key),) for key in keys if key))

    def close(self):
        if self.connection is not None:
            self.connection.close()
            self.connection = None
