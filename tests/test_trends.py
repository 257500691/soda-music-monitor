import datetime
import math
import unittest

from dashboard import DashboardPanel
from ui_widgets import SampleHistory, nice_ceiling


def state(token="sample", peak=.35, io=120, memory=640):
    return {"timestamp": token, "audio_peak": peak, "io_rate_kbps": io,
            "process": {"mem_mb": memory}}


class TrendDataTests(unittest.TestCase):
    def test_repeated_collector_read_is_not_a_new_sample(self):
        history = SampleHistory()
        self.assertTrue(history.append(state(), 10))
        self.assertFalse(history.append(state(peak=.7), 10.4))
        self.assertEqual(history.points("audio", 30, 11), [(10, 35)])

    def test_new_sample_preserves_sensor_units(self):
        history = SampleHistory()
        history.append(state(), 10)
        history.append(state(token="next", peak=.5, io=140, memory=650), 11)
        self.assertEqual(history.points("audio", 30, 11), [(10, 35), (11, 50)])
        self.assertEqual(history.points("io", 30, 11)[-1], (11, 140))
        self.assertEqual(history.points("memory", 30, 11)[-1], (11, 650))

    def test_missing_sensor_data_is_not_zero(self):
        history = SampleHistory()
        history.append({"timestamp": "a"}, 10)
        self.assertEqual(history.points("audio", 30, 11), [(10, None)])
        self.assertEqual(history.points("io", 30, 11), [(10, None)])
        self.assertEqual(history.points("memory", 30, 11), [(10, None)])

    def test_silence_and_zero_io_are_valid_data(self):
        history = SampleHistory()
        history.append(state(peak=0, io=0, memory=0), 10)
        for metric in ("audio", "io", "memory"):
            self.assertEqual(history.points(metric, 30, 10), [(10, 0)])

    def test_invalid_data_cannot_break_chart_scaling(self):
        history = SampleHistory()
        history.append(state(peak=math.nan, io=math.inf, memory=-3), 10)
        for metric in ("audio", "io", "memory"):
            self.assertEqual(history.points(metric, 30, 10), [(10, None)])
        history.append(state(token="b", peak="invalid", io="12.5", memory="640"), 11)
        self.assertEqual(history.points("io", 30, 11)[-1][1], 12.5)

    def test_disconnection_adds_gap_and_stale_process_is_hidden(self):
        history = SampleHistory()
        history.append(state(), 10)
        history.gap(11)
        reconnect = state(token="reconnected")
        reconnect["process"]["stale"] = True
        history.append(reconnect, 12)
        self.assertEqual(history.points("memory", 30, 12), [(10, 640), (11, None), (12, None)])
        self.assertEqual(history.points("audio", 30, 12), [(10, 35), (11, None), (12, 35)])

    def test_time_range_filters_old_data_and_retention_is_bounded(self):
        history = SampleHistory()
        for second in range(201):
            history.append(state(token=str(second)), second)
        self.assertEqual(len(history.points("audio", 30, 200)), 31)
        self.assertEqual(len(history.points("audio", 120, 200)), 121)
        self.assertLessEqual(len(history.samples), 126)

    def test_dynamic_axis_includes_large_io_bursts(self):
        for value in (0, .003, .2, 1, 12, 640, 2000, 150000):
            ceiling = nice_ceiling(value)
            self.assertTrue(math.isfinite(ceiling))
            self.assertGreaterEqual(ceiling, value)
            self.assertGreater(ceiling, 0)

    def test_collector_state_must_be_recent(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        self.assertTrue(DashboardPanel.state_fresh({"timestamp": now.isoformat()}))
        self.assertFalse(DashboardPanel.state_fresh({"timestamp": (now - datetime.timedelta(seconds=20)).isoformat()}))
        self.assertFalse(DashboardPanel.state_fresh({"timestamp": (now + datetime.timedelta(seconds=20)).isoformat()}))
        self.assertFalse(DashboardPanel.state_fresh({"timestamp": "bad timestamp"}))
        self.assertFalse(DashboardPanel.state_fresh({}))


if __name__ == "__main__":
    unittest.main()
