"""Content intelligence module."""
import store

def init():
    store.x("CREATE TABLE IF NOT EXISTS intel_collections (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, keywords TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL)")
