"""Execution errors that must retain the recovery journal. Author: Fubuki."""


class WorkerStopUnconfirmed(RuntimeError):
    """A constructor failed without confirming that its child has stopped."""
