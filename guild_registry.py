"""Durable credentials for guilds added by the owner; only hashes are stored."""
import hashlib
import os
from pathlib import Path
import sqlite3


def fingerprint(key):
    return hashlib.sha256(key.encode('utf-8')).hexdigest()


class GuildRegistry:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        os.chmod(path, 0o600)
        self.db.execute('CREATE TABLE IF NOT EXISTS guilds (id TEXT PRIMARY KEY, key_hash TEXT UNIQUE NOT NULL)')
        self.db.commit()

    def load(self):
        return {int(guild): digest for guild, digest in self.db.execute('SELECT id, key_hash FROM guilds')}

    def add(self, guild, digest):
        with self.db:
            self.db.execute('INSERT INTO guilds VALUES (?, ?)', (str(guild), digest))

    def close(self):
        self.db.close()
