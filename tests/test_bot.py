import os
import unittest
from unittest.mock import patch

import bot


class AlertTests(unittest.TestCase):
    def test_county_filter_and_no_false_lane_closure(self):
        fixture = {'data': [
            {'id': '123', 'county': 'Anne Arundel', 'description': 'Incident @ I-97 N', 'incidentType': 'Vehicle fire'},
            {'id': '124', 'county': 'Baltimore County', 'description': 'Incident @ I-95', 'incidentType': 'Collision'},
        ]}
        with patch.object(bot, 'get_json', return_value=fixture):
            result = bot.chart_events('https://example.com/feed', 'incidents')
        self.assertEqual(len(result), 1)
        self.assertIn('VEHICLE FIRE', result[0]['message'])
        self.assertNotIn('Lane information', result[0]['message'])

    def test_unknown_payload_refuses(self):
        with self.assertRaises(ValueError):
            bot.records({'unexpected': 'format'})

    def test_closed_does_not_post(self):
        fixture = {'data': [{'id': '123', 'county': 'Anne Arundel', 'description': 'Roadwork', 'closed': True}]}
        with patch.object(bot, 'get_json', return_value=fixture):
            self.assertEqual(bot.chart_events('https://example.com/feed', 'closures'), [])


if __name__ == '__main__':
    unittest.main()
