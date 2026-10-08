"""Stand-in for the stdlib ``logging`` module inside the vendored engine.  # [jpsim]

The engine only ever created module loggers, tested ``isEnabledFor(DEBUG)`` and called
``logger.debug`` (the Track 3 adapter never configures a handler, so nothing was ever emitted).
Importing the real ``logging`` costs ~8 ms at every container start (it pulls in traceback,
string, threading, weakref); this module costs nothing and keeps every call a no-op.
"""

DEBUG, INFO, WARNING, ERROR, CRITICAL = 10, 20, 30, 40, 50


class _NullLogger:
    __slots__ = ()

    def isEnabledFor(self, level):  # noqa: N802 - logging API
        return False

    def setLevel(self, level):  # noqa: N802
        pass

    def _noop(self, *args, **kwargs):
        pass

    debug = info = warning = warn = error = critical = exception = log = _noop


_LOGGER = _NullLogger()


def getLogger(name=None):  # noqa: N802
    return _LOGGER


def basicConfig(**kwargs):  # noqa: N802
    pass
