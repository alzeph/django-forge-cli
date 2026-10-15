"""
Configuration pytest commune.

Garde-fou : aucun test ne doit lancer un vrai sous-processus (installation de
paquets, `django-admin`, `manage.py`). Un test qui en a besoin doit patcher
`subprocess.run` lui-même ; ce patch plus spécifique remplace celui-ci.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _forbid_real_subprocess():
    def refuse(*args, **kwargs):
        raise AssertionError(f"subprocess.run non mocké : {args[:1]}")

    with patch("subprocess.run", side_effect=refuse):
        yield
