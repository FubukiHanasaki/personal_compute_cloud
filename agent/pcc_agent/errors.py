"""Execution errors that must retain the recovery journal. Author: FubukiHanasaki."""


class WorkerStopUnconfirmed(RuntimeError):
    """Process cleanup failed without confirming that its children have stopped."""
