"""Production infrastructure adapters for Wyrd."""

from .filesystem import FilesystemStorage, create_filesystem_storage

__all__ = ["FilesystemStorage", "create_filesystem_storage"]
