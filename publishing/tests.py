from datetime import datetime
from django.test import SimpleTestCase

from publishing.services.engine import build_cycle_pairs, build_telethon_entities, calculate_interval, is_working_time


class PublishingEngineTests(SimpleTestCase):
    def test_interval_is_fixed_at_thirty_seconds(self):
        self.assertEqual(calculate_interval(cycle_duration_minutes=5, message_count=1, group_count=1), 30)
        self.assertEqual(calculate_interval(cycle_duration_minutes=60, message_count=100, group_count=20), 30)

    def test_work_sleep_cycle_supports_more_than_one_day(self):
        self.assertTrue(is_working_time(now=datetime(2026, 1, 1, 21), work_hours=22, sleep_hours=4))
        self.assertFalse(is_working_time(now=datetime(2026, 1, 1, 23), work_hours=22, sleep_hours=4))

    def test_cycle_pairs_distribute_one_message_per_group(self):
        self.assertEqual(build_cycle_pairs(message_count=3, group_count=2, message_index=0, group_index=0), [(0, 0), (1, 1)])
        self.assertEqual(build_cycle_pairs(message_count=2, group_count=4, message_index=0, group_index=2), [(0, 2), (1, 3), (0, 0), (1, 1)])

    def test_cycle_pairs_continue_from_saved_indices(self):
        self.assertEqual(build_cycle_pairs(message_count=3, group_count=2, message_index=2, group_index=0), [(2, 0), (0, 1)])

    def test_cycle_pairs_are_limited_to_one_message_per_group(self):
        self.assertEqual(len(build_cycle_pairs(message_count=20, group_count=3, message_index=0, group_index=0)), 3)

    def test_custom_emoji_entity_is_converted_for_telethon(self):
        entities = build_telethon_entities([{
            "type": "custom_emoji",
            "offset": 0,
            "length": 2,
            "custom_emoji_id": "1234567890123456789",
        }])
        self.assertEqual(entities[0].document_id, 1234567890123456789)
