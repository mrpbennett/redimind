"""Errors shared by memory lifecycle, index state, and retrieval."""


class MemoryError(Exception):
    """A rejected memory operation."""


class Conflict(MemoryError):
    """The entry changed since it was read."""
