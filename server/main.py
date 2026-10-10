from __future__ import annotations

try:
    from .utils.audio import *
    from .utils.audio import main
except ImportError:
    from utils.audio import *
    from utils.audio import main


if __name__ == "__main__":
    raise SystemExit(main())
