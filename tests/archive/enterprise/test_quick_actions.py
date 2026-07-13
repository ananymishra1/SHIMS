"""P3 tests: per-page, role-filtered quick actions for the Shims dock."""
from __future__ import annotations

from shims_enterprise.copilot_actions import quick_actions_for


def test_page_specific_actions_win():
    labels = [a['label'] for a in quick_actions_for('/qms/12', 'qa')]
    assert 'Draft CAPA for this record' in labels


def test_role_filtering():
    warehouse = [a['label'] for a in quick_actions_for('/qms/12', 'warehouse')]
    assert 'Draft CAPA for this record' not in warehouse  # warehouse can't draft CAPAs
    assert warehouse  # but still gets fallbacks


def test_different_pages_get_different_actions():
    qms = {a['label'] for a in quick_actions_for('/qms', 'admin')}
    wh = {a['label'] for a in quick_actions_for('/warehouse/stock', 'admin')}
    assert qms != wh
    assert any('CAPA' in l for l in qms)
    assert any('Reorder' in l or 'Materials' in l for l in wh)


def test_unknown_page_gets_fallbacks():
    actions = quick_actions_for('/some/unknown/page', 'qc')
    assert actions, 'fallback actions must always exist'
    assert all(a['prompt'] for a in actions)


def test_no_duplicate_labels_and_limit():
    actions = quick_actions_for('/production/bmr/5', 'admin', limit=5)
    labels = [a['label'] for a in actions]
    assert len(labels) == len(set(labels))
    assert len(labels) <= 5
