"""Public factory for Wyrd's production filesystem storage adapter."""

from .adapter import FilesystemStorage, create_filesystem_storage

__all__ = ["FilesystemStorage", "create_filesystem_storage"]
