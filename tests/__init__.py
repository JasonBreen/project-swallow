"""Keep sibling test helpers importable when tests/ is imported as a package.

Test modules import shared helpers by bare name, for example
``from test_disc import raw_sector``. ``python -m unittest discover -s tests``
puts this directory on sys.path itself. Discovery that uses the repository
root as the top-level directory (``-t .``, as the VS Code Python extension's
project-based discovery does) imports ``tests`` as a package instead, so the
directory is added here too. Both forms find the same tests.
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
