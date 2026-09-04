"""`aggrete --version` prints the installed package version and exits."""
from __future__ import annotations

import asyncio
import sys
from importlib.metadata import version
from unittest.mock import patch

from aggrete.proxy import main


def test_version_flag(capsys):
    with patch.object(sys, "argv", ["aggrete", "--version"]):
        asyncio.run(main())
    assert capsys.readouterr().out.strip() == f"aggrete {version('aggrete')}"
