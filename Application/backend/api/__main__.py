"""Makes `python -m Application.backend.api` work.

`__init__.py` defines `main()`; without this file `-m` on a package fails
with "cannot be directly executed", which is how a documented command and
a working one drift apart.
"""
from Application.backend.api import main

main()
