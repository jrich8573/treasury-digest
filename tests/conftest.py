import os
import sys


def _add_repo_root_to_sys_path():
    here = os.path.dirname(__file__)
    root = os.path.abspath(os.path.join(here, os.pardir))
    if root not in sys.path:
        sys.path.insert(0, root)


_add_repo_root_to_sys_path()
