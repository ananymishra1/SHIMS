import pytest


def test_basic_imports():
    import shims_core.settings
    import shims_core.security
    assert shims_core.settings.settings.company_name


def test_models_import_skipped_if_sqlalchemy_missing():
    pytest.importorskip('sqlalchemy')
    import shims_core.models
    assert shims_core.models
