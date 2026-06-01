"""In-memory game state (no database — design MVP constraint).

A single shared GameState lives in this process so the Discord bot and the web
dashboard read/write the same object. A JSON snapshot lets a restart resume.
"""
