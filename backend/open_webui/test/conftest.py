import pytest


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    report = yield
    if report.when == "call":
        item._canary_call_passed = report.passed
    return report


@pytest.fixture
def canary_harness(request, capfd):
    """Capture every operational sink and check it for canaries (OBS-17).

    See ``open_webui/test/util/canary_harness.py`` for what is captured and how
    to use it. If the test passes without calling ``assert_no_canaries``, the
    check runs at teardown.
    """
    from open_webui.test.util.canary_harness import CanaryHarness

    with CanaryHarness(capfd) as harness:
        yield harness
        if not harness.checked and getattr(request.node, "_canary_call_passed", False):
            harness.assert_no_canaries()
