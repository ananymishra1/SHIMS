"""Regression test for the copilot chemistry crash (P1).

The live crash: ``analyze_product_chemistry`` stores ``chemical_changes`` as a
dict ``{"ok":…, "changes":[…]}``; ``_enterprise_direct_shims_answer`` then did
``changes[:5]`` — TypeError: unhashable type: 'slice' — killing every copilot
chemistry turn. ``_as_rows`` must normalize every shape a brain can emit.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

import shims_enterprise.app as eapp


# ── _as_rows unit coverage ───────────────────────────────────────────────────
def test_as_rows_dict_shape():
    value = {'ok': True, 'changes': [{'change_summary': 'x'}, {'change_summary': 'y'}]}
    rows = eapp._as_rows(value, 'changes')
    assert rows == [{'change_summary': 'x'}, {'change_summary': 'y'}]
    assert rows[:5] == rows  # sliceable


def test_as_rows_dict_with_named_key():
    value = {'ok': True, 'stages': [{'stage_name': 's1'}]}
    assert eapp._as_rows(value, 'stages') == [{'stage_name': 's1'}]


def test_as_rows_none_and_empty():
    assert eapp._as_rows(None, 'changes') == []
    assert eapp._as_rows({}, 'changes') == []
    assert eapp._as_rows([], 'changes') == []


def test_as_rows_list_passthrough_filters_non_dicts():
    assert eapp._as_rows([{'a': 1}, 'junk', None], 'changes') == [{'a': 1}]


def test_as_rows_iterable():
    gen = ({'i': i} for i in range(3))
    assert len(eapp._as_rows(gen, 'changes')) == 3


# ── end-to-end: the previously crashing route ───────────────────────────────
def test_direct_answer_survives_dict_shaped_chemistry(monkeypatch: pytest.MonkeyPatch):
    """Reproduce the exact production shape that crashed and assert a clean answer."""
    monkeypatch.setattr(eapp, 'list_products', lambda limit=250: [{'product_name': 'TestProduct'}])
    monkeypatch.setattr(eapp, 'search_enterprise_memory', lambda *a, **k: {'memories': []})
    monkeypatch.setattr(eapp, 'search_bmr_corpus', lambda *a, **k: {'hits': []})
    monkeypatch.setattr(
        eapp, 'analyze_product_chemistry',
        lambda *a, **k: {
            'ok': True,
            'summary': {'raw_materials': ['RM-1'], 'solvents': ['methanol']},
            'stages': [{'stage_name': 'Stage 1', 'raw_materials': ['RM-1'], 'solvents': ['methanol']}],
            # THE BUG SHAPE: dict, not list
            'chemical_changes': {'ok': True, 'changes': [
                {'change_summary': 'condensation step', 'purge_or_control_strategy': 'recrystallize'},
            ]},
        },
    )
    monkeypatch.setattr(eapp, 'list_route_stages', lambda *a, **k: [])
    monkeypatch.setattr(eapp, 'list_chemical_changes',
                        lambda *a, **k: {'ok': True, 'changes': []})

    user = {'id': 1, 'role': 'admin', 'full_name': 'Test', 'department': 'executive'}
    result = asyncio.run(eapp._enterprise_direct_shims_answer(
        'explain the process and material balance for TestProduct', user, 'executive'))

    assert result is not None and result['ok']
    assert 'condensation step' in result['answer']
    assert isinstance(result['payload']['changes'], list)
    assert isinstance(result['payload']['stages'], list)


def test_direct_answer_survives_broken_chemistry(monkeypatch: pytest.MonkeyPatch):
    """Even a malformed brain output must degrade, not crash the stream."""
    monkeypatch.setattr(eapp, 'list_products', lambda limit=250: [{'product_name': 'TestProduct'}])
    monkeypatch.setattr(eapp, 'search_enterprise_memory', lambda *a, **k: {'memories': []})
    monkeypatch.setattr(eapp, 'search_bmr_corpus', lambda *a, **k: {'hits': []})
    monkeypatch.setattr(eapp, 'analyze_product_chemistry',
                        lambda *a, **k: {'summary': 'not-a-dict', 'stages': 42, 'chemical_changes': object()})
    monkeypatch.setattr(eapp, 'list_route_stages', lambda *a, **k: [])
    monkeypatch.setattr(eapp, 'list_chemical_changes', lambda *a, **k: {'changes': []})

    user = {'id': 1, 'role': 'admin', 'full_name': 'Test', 'department': 'executive'}
    result = asyncio.run(eapp._enterprise_direct_shims_answer(
        'explain the process for TestProduct', user, 'executive'))
    assert result is not None and result['ok']
