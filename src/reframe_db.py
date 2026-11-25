"""Entry point so agents can run ``python -m reframe_db`` from the src folder."""
from reframe.reframe_db import *  # noqa: F401,F403
from reframe.reframe_db import main

if __name__ == "__main__":
    import sys
    sys.exit(main())
