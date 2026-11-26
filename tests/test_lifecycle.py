"""Resource cleanup survives startup and cleanup failures."""

import pytest

from network_monitor.lifecycle import Lifecycle


def test_stop_all_before_cleanup_and_continue_after_failure():
    actions = []

    def fail():
        actions.append("failed cleanup")
        raise OSError("injected cleanup failure")

    with pytest.raises(ValueError, match="startup"):
        with Lifecycle() as resources:
            resources.own("storage", lambda: actions.append("storage closed"))
            resources.own("worker", fail, stop=lambda: actions.append("worker stopped"))
            resources.own(
                "HTTP",
                lambda: actions.append("HTTP closed"),
                stop=lambda: actions.append("HTTP stopped"),
            )
            raise ValueError("startup")
    assert actions == [
        "worker stopped",
        "HTTP stopped",
        "HTTP closed",
        "failed cleanup",
        "storage closed",
    ]
