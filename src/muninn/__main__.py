"""`python -m muninn`: the same as the `muninn` command (how the built-in schedule starts its jobs)."""

import sys

from muninn.main import main

sys.exit(main())
