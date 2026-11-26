"""Cleanup registration that stops all producers before waiting on consumers."""

import logging
from contextlib import ExitStack

LOG = logging.getLogger(__name__)


class Lifecycle:
    def __init__(self):
        self._resources = ExitStack()
        self._stoppers = []

    def own(self, name, cleanup, *, stop=None):
        self._resources.callback(self._call, name, cleanup)
        if stop is not None:
            self._stoppers.append((name, stop))

    @staticmethod
    def _call(name, callback):
        try:
            callback()
        except Exception:
            LOG.exception("Cleanup failed for %s; continuing remaining cleanup", name)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        for name, stop in self._stoppers:
            self._call(name, stop)
        self._resources.close()
