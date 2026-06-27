from __future__ import annotations

from types import ModuleType

import pytest
from conftest import import_first, require_attr


def test_import_first_fails_when_contract_module_is_missing():
    with pytest.raises(pytest.fail.Exception):
        import_first(("definitely_missing_contract_surface",))


def test_require_attr_fails_when_contract_api_is_missing():
    module = ModuleType("contract_surface")

    with pytest.raises(pytest.fail.Exception):
        require_attr(module, ("missing_api",))
