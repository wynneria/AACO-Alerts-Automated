"""Anne Arundel County traffic and weather alert drafting/publishing bot.

Use with the public MDOT CHART JSON feeds and NWS active alerts.
The CHART incident/closure feed URL is configurable and intentionally not guessed.
Only posts via an authorized Facebook Page access token (never a personal profile).
"""
import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

LOG = logging.getLogger('aa-alerts')
LOCAL = ZoneInfo('America/New_York')
STATE_PATH = Path(os.getenv('STATE_PATH', 'state.json'))
SOURCE_URL = 'https://chart.maryland.gov/Incidents/GetIncidents'
NWS_URL = 'https://api.weather.gov/alerts/active?zone=MDC003'
HEADERS = {'User-Agent': os.getenv('NWS_USER_AGENT', 'AnneArundelCommunityAlerts/0.1 (contact: community-page-admin)'), 'Accept': 'application/geo+json, application/json'}
COUNTY = re.compile(r'\banne[ -]?arundel\b', re.I)
MAX_POSTS = max(0, int(os.getenv('MAX_POSTS_PER_RUN', '3')))
DRY_RUN = os.getenv('DRY_RUN', 'true').lower() not in ('0', 'no', 'false')


def get_json(url):
    response = requests.get(url, timeout=25, headers=HEADERS)
    response.raise_for_status()
    result = response.json()
    if not isinstance(result, (dict, list)):
        raise ValueError('Expected JSON object/list')
    return result


def records(payload):
    """MDOT's documented feed data records are normally under data[]."""
    if isinstance(payload, list):
        return payload
    for key in ('data', 'incidents', 'events', 'items', 'closures'):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    raise ValueError('Unrecognized CHART JSON structure, refusing to publish')


def chart_events(url, label):
    if not url:
        LOG.info('%s feed not configured, skipping', label)
        return []
    items = records(get_json(url))
    events = []
    for item in items:
        if not isinstance(item, dict) or not COUNTY.search(str(item.get('county', ''))):
            continue
        if item.get('closed') is True:
            continue
        event_id = item.get('id')
        description = item.get('description') or item.get('name')
        if not event_id or not description:
            continue  # Never publish incomplete events
        incident_type = str(item.get('incidentType') or '').strip()
        # MDOT calls roadway vehicle fires 'Vehicle fire'; not a building fire dispatch
        if re.search(r'fire', incident_type, re.I):
            icon, title = '🔥', 'REPORTED ROADWAY VEHICLE FIRE'
        elif label == 'closures':
            icon, title = '🚧', 'ROAD CLOSURE / WORK ZONE'
        elif item.get('trafficAlert') is True:
            icon, title = '🚨', 'TRAFFIC ALERT'
        elif str(item.get('type')) == '255':
            icon, title = '🚙', 'DISABLED VEHICLE'
        else:
            icon, title = '🚗', 'ROADWAY INCIDENT'
        fields = [f'{icon} {title} | ANNE ARUNDEL COUNTY', '', f'📍 {clean(description)}']
        if incident_type:
            fields.append(f'Incident type: {clean(incident_type)}')
        direction = str(item.get('direction') or '').strip()
        if direction and direction.lower() not in ('none', 'unknown'):
            fields.append(f'Direction: {clean(direction)}')
        lanes = str(item.get('lanesStatus') or item.get('lanesClosed') or '').strip()
        if lanes:
            fields.append(f'Lane information (as reported): {clean(lanes)}')
        detail = str(item.get('trafficAlertTextMsg') or '').strip()
        if detail:
            fields.append(f'MDOT advisory: {clean(detail)}')
        fields.extend(['', 'Please use caution and follow directions from responders.', f'Source: Maryland CHART | {SOURCE_URL}', 'Independent community page. Not affiliated with MDOT.'])
        events.append({'id': f'chart:{label}:{event_id}', 'message': '\n'.join(fields), 'source': 'chart'})
    return events


def clean(value):
    value = re.sub(r'\s+', ' ', str(value)).strip()
    return value[:420]


def weather_events():
    payload = get_json(NWS_URL)
    items = payload.get('features', []) if isinstance(payload, dict) else []
    events = []
    for item in items:
        props = item.get('properties') or {}
        severity = str(props.get('severity') or '')
        event = str(props.get('event') or '')
        # Limit to important public safety alerts, not routine advisories
        if severity not in ('Severe', 'Extreme') and 'warning' not in event.lower():
            continue
        ident = item.get('id') or props.get('id')
        headline = props.get('headline') or event
        if not ident or not headline:
            continue
        ends = str(props.get('ends') or props.get('expires') or '')
        when = ''
        if ends:
            try:
                when = datetime.fromisoformat(ends.replace('Z', '+00:00')).astimezone(LOCAL).strftime('%b %-d, %-I:%M %p %Z')
            except (ValueError, OSError):
                pass
        text = '\n'.join([
            f'⚠️ NWS {clean(event).upper()} | ANNE ARUNDEL COUNTY', '',
            clean(headline),
            *( [f'Expires: {when}'] if when else [] ),
            '', 'Check the full official alert for affected areas and instructions.',
            'Source: National Weather Service | https://www.weather.gov/',
            'Independent community page. Not an official emergency warning service.'
        ])
        events.append({'id': f'nws:{ident}', 'message': text, 'source': 'nws'})
    return events


def facebook_post(message):
    page_id = os.getenv('FB_PAGE_ID', '').strip()
    token = os.getenv('FB_PAGE_ACCESS_TOKEN', '').strip()
    if not page_id or not token:
        raise RuntimeError('FB_PAGE_ID and FB_PAGE_ACCESS_TOKEN required to publish')
    if not re.fullmatch(r'\d+', page_id):
        raise ValueError('FB_PAGE_ID must be numeric')
    # Meta Graph API, Page feed publishing. Never print access token.
    version = os.getenv('FB_GRAPH_VERSION', 'v27.0')
    response = requests.post(f'https://graph.facebook.com/{version}/{page_id}/feed',
                             data={'message': message, 'access_token': token}, timeout=25)
    response.raise_for_status()
    body = response.json()
    if 'id' not in body:
        raise RuntimeError('Meta did not return a post ID')
    return body['id']


def load_state():
    if not STATE_PATH.exists():
        return {'initialized': False, 'seen': {}}
    data = json.loads(STATE_PATH.read_text(encoding='utf-8'))
    if not isinstance(data.get('seen'), dict):
        raise ValueError('Invalid state.json; refusing to risk duplicate posts')
    return data


def save_state(data):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    tmp.replace(STATE_PATH)


def run():
    state = load_state()
    all_events = []
    failures = []
    sources = [
        ('CHART incidents', lambda: chart_events(os.getenv('CHART_INCIDENTS_URL', '').strip(), 'incidents')),
        ('CHART closures', lambda: chart_events(os.getenv('CHART_CLOSURES_URL', '').strip(), 'closures')),
        ('NWS weather', weather_events),
    ]
    for name, getter in sources:
        try:
            all_events.extend(getter())
        except (requests.RequestException, ValueError, TypeError) as error:
            LOG.error('%s failed: %s', name, error)
            failures.append(name)
    if not all_events and failures:
        raise RuntimeError('All configured sources failed; state untouched')
    # Each record has a stable source ID; reuse between runs, avoid duplicate publishing.
    observed = {event['id']: event for event in all_events}
    now = datetime.now(timezone.utc).isoformat()
    if not state.get('initialized'):
        state['initialized'] = True
        for key in observed:
            state['seen'][key] = now
        save_state(state)
        LOG.info('Initial baseline created (%d incidents). No historic posts sent.', len(observed))
        return
    outstanding = [event for key, event in observed.items() if key not in state['seen']]
    LOG.info('%d new alerts. Publish mode: %s', len(outstanding), not DRY_RUN)
    for event in outstanding[:MAX_POSTS]:
        if DRY_RUN:
            print('\n--- PREVIEW (NOT POSTED) ---\n' + event['message'])
            # Do not mark as posted; can preview again later.
            continue
        try:
            post_id = facebook_post(event['message'])
        except (requests.RequestException, ValueError, RuntimeError) as error:
            LOG.error('Meta post failed for %s: %s', event['id'], error)
            continue
        state['seen'][event['id']] = now
        save_state(state)  # Write after each successful post
        LOG.info('Published %s as %s', event['id'], post_id)
    # Trim only stale entries; prevent unbounded growth while limiting repost risk.
    cutoff = datetime.now(timezone.utc) - timedelta(days=90)
    for key, ts in list(state['seen'].items()):
        try:
            if datetime.fromisoformat(ts) < cutoff:
                del state['seen'][key]
        except (TypeError, ValueError):
            pass
    save_state(state)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    run()
