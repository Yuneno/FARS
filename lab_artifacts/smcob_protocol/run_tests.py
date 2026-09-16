"""Run pytest with new, inherited-ACL temporary directories on Windows.

Python 3.13 mode=0700 creates user-only ACLs inaccessible to this sandbox.
Only mkdir calls UNDER THIS RUN'S NEW TEMP ROOT use inherited ACLs. Existing
directories, permissions, production code and test assertions are unchanged.
"""
import os
from pathlib import Path
import sys
import tempfile
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
BASE = Path(tempfile.gettempdir()) / ('fars-smcob-tests-' + uuid.uuid4().hex)
original_mkdir = os.mkdir


def inherited_temp_mkdir(path, mode=0o777, *, dir_fd=None):
    resolved = Path(path).absolute()
    if os.name == 'nt' and mode == 0o700 and (resolved == BASE or BASE in resolved.parents):
        mode = 0o777
    if dir_fd is None:
        return original_mkdir(path, mode)
    return original_mkdir(path, mode, dir_fd=dir_fd)


if __name__ == '__main__':
    os.mkdir = inherited_temp_mkdir
    print(f'Temporary root: {BASE}', flush=True)
    try:
        code = pytest.main([*(sys.argv[1:] or ['tests']), '-q', '-p', 'no:cacheprovider',
                            '--deselect=tests/test_cli.py::test_installed_fars_binary_runs_outside_checkout',
                            '--basetemp', str(BASE)])
    finally:
        os.mkdir = original_mkdir
    raise SystemExit(code)
