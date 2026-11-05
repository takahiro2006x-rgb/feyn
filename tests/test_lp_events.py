# -*- coding: utf-8 -*-
"""LPの効果測定（/api/lp-event）のテスト。人を特定する情報を記録しないこと・決まった形だけ記録すること"""
import re

import core
import ratelimit
from blueprints import pages

LANDING = open('landing.html', encoding='utf-8').read()


def _events():
    with core.get_db() as conn:
        return [dict(r) for r in conn.execute('SELECT event_type, user_id, mode FROM events ORDER BY id').fetchall()]


def test_view_and_clicks_are_recorded_without_user(client):
    assert client.post('/api/lp-event', json={'type': 'view'}).status_code == 204
    client.post('/api/lp-event', json={'type': 'cta', 'place': 'hero'})
    client.post('/api/lp-event', json={'type': 'contact'})
    assert _events() == [
        {'event_type': 'lp_view', 'user_id': None, 'mode': None},
        {'event_type': 'lp_cta_click', 'user_id': None, 'mode': 'hero'},
        {'event_type': 'lp_contact_click', 'user_id': None, 'mode': None},
    ]


def test_logged_in_user_is_not_attached(student_client):
    student_client.post('/api/lp-event', json={'type': 'cta', 'place': 'final'})
    assert [e['user_id'] for e in _events() if e['event_type'].startswith('lp_')] == [None]


def test_unknown_types_and_places_are_ignored(client):
    for body in [{'type': 'cta', 'place': 'somewhere'}, {'type': 'cta'}, {'type': 'signup'},
                 {'type': 'view', 'place': '<script>'}, {}]:
        assert client.post('/api/lp-event', json=body).status_code == 204
    client.post('/api/lp-event', data='not json', content_type='text/plain')
    assert [e for e in _events() if e['event_type'] != 'lp_view'] == []
    assert all(e['mode'] is None for e in _events())


def test_one_ip_can_only_record_a_limited_number(client):
    ratelimit.limiter.reset_all()
    for _ in range(pages.LP_EVENT_LIMIT + 10):
        client.post('/api/lp-event', json={'type': 'view'})
    assert len(_events()) == pages.LP_EVENT_LIMIT


def test_lp_metrics_count_views_and_each_button(client):
    for body in [{'type': 'view'}] * 4 + [{'type': 'cta', 'place': 'hero'}] * 2 + \
                [{'type': 'cta', 'place': 'sticky'}, {'type': 'contact'}]:
        client.post('/api/lp-event', json=body)
    m = core.compute_lp_metrics()
    assert m['views'] == 4
    assert m['cta_clicks']['hero'] == 2 and m['cta_clicks']['sticky'] == 1 and m['cta_clicks']['header'] == 0
    assert m['cta_clicks_total'] == 3 and m['cta_click_rate'] == 0.75
    assert m['contact_clicks'] == 1


def test_every_start_button_on_the_lp_says_which_button_it_is():
    starts = re.findall(r'<a [^>]*href="/login\?signup=1"[^>]*>', LANDING)
    places = [re.search(r'data-track-cta="(\w+)"', a).group(1) for a in starts]
    assert len(places) == len(set(places)) == len(starts)
    assert set(places) <= core.LP_CTA_PLACES
    assert 'data-track-contact' in LANDING
    assert "trackLp('view')" in LANDING
