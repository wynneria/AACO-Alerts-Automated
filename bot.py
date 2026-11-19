"""Anne Arundel County traffic and weather alert drafting/publishing bot.

Use with the public MDOT CHART JSON feeds and NWS active alerts.
The CHART incident/closure feed URL is configurable and intentionally not guessed.
Only posts via an authorized Facebook Page access token (never a personal profile).
"""
import hashlib
import json
import math
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


# Approximate community centers in Anne Arundel County. We say "near", not "in",
# because the nearest center does not establish a municipal boundary.
# Coordinates are only used if CHART supplies a valid incident latitude/longitude.
COMMUNITIES = {
    'Annapolis': (38.9784, -76.4922),
    'Arnold': (39.0321, -76.5027),
    'Severna Park': (39.0704, -76.5452),
    'Pasadena': (39.1073, -76.5711),
    'Glen Burnie': (39.1626, -76.6247),
    'Brooklyn Park': (39.2287, -76.6020),
    'Linthicum Heights': (39.2051, -76.6520),
    'Ferndale': (39.1878, -76.6402),
    'Hanover': (39.1929, -76.7240),
    'Jessup': (39.1493, -76.7758),
    'Odenton': (39.0840, -76.7003),
    'Gambrills': (39.0673, -76.6694),
    'Crofton': (39.0018, -76.6875),
    'Fort Meade': (39.1082, -76.7434),
    'Millersville': (39.0590, -76.6470),
    'Riviera Beach': (39.1647, -76.5072),
    'Lake Shore': (39.1028, -76.4768),
    'Cape St. Claire': (39.0431, -76.4480),
    'Parole': (38.9796, -76.5400),
    'Edgewater': (38.9026, -76.5494),
    'Mayo': (38.8871, -76.5130),
    'Shady Side': (38.8418, -76.5113),
    'Deale': (38.7779, -76.5550),
    'Friendship': (38.7365, -76.5708),
    'Lothian': (38.8238, -76.6110),
    'Davidsonville': (38.9220, -76.6286),
    'Riva': (38.9564, -76.5789),
    'Harwood': (38.8558, -76.6220),
    'Crownsville': (39.0287, -76.6014),
    'West River': (38.8239, -76.5420),
    'Churchton': (38.8036, -76.5401),
    'Galesville': (38.8438, -76.5427),
}


def nearest_community(item):
    """Return a nearby area, or None if coordinates are absent or uncertain.

    A centroid is *not* an authoritative city boundary; labels are "Near <name>".
    """
    try:
        lat = float(item.get('lat'))
        lon = float(item.get('lon'))
    except (ValueError, TypeError):
        return None
    if not (math.isfinite(lat) and math.isfinite(lon)):
        return None
    if not (38.65 <= lat <= 39.35 and -76.95 <= lon <= -76.25):
        return None
    radians = math.radians
    distances = {}
    for name, (c_lat, c_lon) in COMMUNITIES.items():
        dlat = radians(c_lat - lat)
        dlon = radians(c_lon - lon)
        a = math.sin(dlat / 2)**2 + math.cos(radians(lat)) * math.cos(radians(c_lat)) * math.sin(dlon / 2)**2
        distances[name] = 3958.76 * 2 * math.asin(min(1.0, math.sqrt(a)))
    name, distance = min(distances.items(), key=lambda pair: pair[1])
    return name if distance <= 3.8 else None


def pretty_location(description):
    """Strip generic CHART prefixes, preserving the source's road details."""
    text = clean(description)
    return re.sub(
        r'^(?:Incident|Action Event|Active Closure|Disabled Vehicle|Congestion|Special Event|Safety Message Event)\s*@\s*',
        '', text, flags=re.I
    ).strip() or text


def route_hashtags(location):
    """Create at most two route-specific tags from MD, US, or Interstate numbers."""
    tags = []
    for prefix, number in re.findall(r'\b(I|US|MD)[ -]?(\d{1,3})\b', location, flags=re.I):
        tag = f'#{prefix.upper()}{number}'
        if tag not in tags:
            tags.append(tag)
    return tags[:2]


def incident_category(item, label):
    """Prefer grounded CHART values over speculative crash/severity labels."""
    detail = str(item.get('incidentType') or '').lower()
    desc = str(item.get('description') or '').lower()
    type_code = str(item.get('type') or '')
    action_data = item.get('additionalData') or {}
    actions = action_data.get('actionTypes', []) if isinstance(action_data, dict) else []
    action_names = ' '.join(str(x.get('actionType') or '') for x in actions if isinstance(x, dict)).lower()

    if 'vehicle fire' in detail:
        return '🔥', 'REPORTED VEHICLE FIRE', '#VehicleFire'
    if label == 'closures':
        return '🚧', 'ROADWORK / CLOSURE NOTICE', '#RoadClosure'
    if 'disabled' in detail or type_code == '255':
        return '🚙', 'DISABLED VEHICLE', '#DisabledVehicle'
    if 'debris' in detail or 'debris' in action_names:
        return '⚠️', 'DEBRIS REPORTED', '#RoadHazard'
    if 'signal' in action_names:
        return '🚦', 'TRAFFIC SIGNAL ISSUE', '#TrafficAlert'
    if 'pothole' in action_names:
        return '🚧', 'POTHOLE REPORTED', '#RoadHazard'
    if 'utility' in detail or 'utility' in action_names:
        return '⚠️', 'UTILITY ISSUE', '#RoadHazard'
    if 'police activity' in detail:
        return '🚨', 'POLICE ACTIVITY REPORTED', '#TrafficAlert'
    if 'high water' in detail or 'flood' in detail:
        return '🌊', 'HIGH WATER / FLOODING REPORTED', '#RoadHazard'
    if re.search(r'\b(crash|collision)\b', detail):
        return '🚗', 'REPORTED CRASH', '#TrafficAlert'
    if type_code == '250':
        return '🚗', 'CONGESTION REPORTED', '#TrafficAlert'
    if item.get('trafficAlert') is True:
        return '🚨', 'TRAFFIC ALERT', '#TrafficAlert'
    return '🚗', 'ROADWAY INCIDENT', '#TrafficAlert'


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

        icon, title, category_tag = incident_category(item, label)
        community = nearest_community(item)
        location = pretty_location(description)
        title_area = f'NEAR {community.upper()}' if community else 'ANNE ARUNDEL COUNTY'
        fields = [f'{icon} {title} | {title_area}', '', f'📍 Location: {location}']
        if community:
            fields.append(f'📌 Area: Near {community}, Anne Arundel County')
        else:
            fields.append('📌 Area: Anne Arundel County')

        direction = str(item.get('direction') or '').strip()
        if direction and direction.lower() not in ('none', 'unknown', 'other', 'no additional information'):
            fields.append(f'↗️ Direction (reported): {clean(direction)}')
        lanes = str(item.get('lanesStatus') or item.get('lanesClosed') or '').strip()
        if lanes and lanes.lower() not in ('none', 'unknown'):
            fields.append(f'🚧 Lane information (reported): {clean(lanes)}')
        advisory = str(item.get('trafficAlertTextMsg') or '').strip()
        if advisory:
            fields.append(f'⚠️ MDOT advisory: {clean(advisory)}')
        else:
            fields.append('⚠️ Please use caution in this area. Follow posted signs and directions from responders.')

        tags = ['#AnneArundelCounty', '#AACoTraffic']
        if community:
            tags.append('#' + re.sub(r'[^A-Za-z0-9]', '', community))
        tags.extend(route_hashtags(location))
        tags.append(category_tag)
        tags = list(dict.fromkeys(tags))[:6]
        fields.extend(['', f'📰 Source: Maryland CHART | {SOURCE_URL}', 'Independent community page; not affiliated with MDOT.', '', ' '.join(tags)])
        events.append({'id': f'chart:{label}:{event_id}', 'message': '\n'.join(fields), 'source': 'chart'})
    LOG.info('%s: %d records checked, %d matching county events', label, len(items), len(events))
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
            'Independent community page. Not an official emergency warning service.',
            '', '#AnneArundelCounty #AACoWeather'
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
        state['last_mode'] = 'preview' if DRY_RUN else 'live'
        save_state(state)
        LOG.info('Initial baseline created (%d incidents). No historic posts sent.', len(observed))
        return
    # On the FIRST live run (or whenever switching back from preview mode),
    # baseline *all currently reported* events rather than publish stale
    # incidents that were previewed but never marked as posted.
    # Do not finalize activation while any feed has failed: an older event
    # could otherwise appear as "new" when that feed recovers.
    if not DRY_RUN and state.get('last_mode') != 'live':
        if failures:
            raise RuntimeError(
                'Live activation paused: one or more sources failed. '
                'Keep preview mode enabled and fix the feed first.'
            )
        for key in observed:
            state['seen'][key] = now
        state['last_mode'] = 'live'
        save_state(state)
        LOG.info('Live mode activated. Baseline refreshed (%d current events). '
                 'No historical alerts were posted.', len(observed))
        return
    if DRY_RUN:
        state['last_mode'] = 'preview'
    outstanding = [event for key, event in observed.items() if key not in state['seen']]
    LOG.info('%d new alerts. Publish mode: %s', len(outstanding), not DRY_RUN)
    if DRY_RUN and not outstanding:
        # Display one already-known live-feed event so formatting can be inspected
        # immediately, without inventing an incident or changing deduplication state.
        sample = next((event for event in observed.values() if event['source'] == 'chart'), None)
        if sample:
            print('\n--- FORMATTING EXAMPLE (EXISTING FEED ITEM, NOT NEW, NOT POSTED) ---\n' + sample['message'])
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
