"""The browser fixtures (tests/e2e_support.py), shared by every module here: one browser per run."""

from e2e_support import browser, desktop, muninn, newcomer, page  # noqa: F401 (fixtures)

try:
    from playwright.sync_api import expect
except ImportError:
    pass
else:
    # what a screen is told shows after the round of reads on its way and the one after it: seconds on a machine running
    # four browsers at once, against a passing check returning as soon as it holds
    expect.set_options(timeout=15_000)
