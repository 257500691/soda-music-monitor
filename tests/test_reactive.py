import math
import unittest

from music_visualizer import MotionState
from reactive_audio import decode_frame


def payload():
    return {"ready": True, "timestamp": 100000, "bands": [.6] * 48,
            "wave": [.15, -.15] * 64, "bass": .7, "mid": .5,
            "treble": .3, "beat": 1, "rms": .05, "peak": .2, "sample_rate": 48000}


class ReactiveTests(unittest.TestCase):
    def test_real_feature_frame_keeps_bands_and_signed_wave(self):
        frame = decode_frame(payload(), now=100.1)
        self.assertEqual(frame["bands"], [.6] * 48)
        self.assertEqual(frame["wave"], [.15, -.15] * 64)
        self.assertEqual(frame["sample_rate"], 48000)

    def test_stale_and_future_frames_do_not_drive_animation(self):
        self.assertIsNone(decode_frame(payload(), now=102))
        self.assertIsNone(decode_frame(payload(), now=99))

    def test_failed_capture_and_incomplete_arrays_are_rejected(self):
        for key, value in (("ready", False), ("bands", [0] * 47), ("wave", []), ("wave", None)):
            data = payload()
            data[key] = value
            self.assertIsNone(decode_frame(data, now=100))

    def test_nonfinite_samples_are_rejected(self):
        for key in ("beat", "rms", "timestamp"):
            data = payload()
            data[key] = math.nan
            self.assertIsNone(decode_frame(data, now=100))
        data = payload()
        data["bands"][3] = math.inf
        self.assertIsNone(decode_frame(data, now=100))

    def test_values_are_bounded_without_losing_wave_polarity(self):
        data = payload()
        data["bands"][0], data["bands"][1] = 2, -1
        data["wave"][0], data["wave"][1] = 2, -2
        frame = decode_frame(data, now=100)
        self.assertEqual(frame["bands"][:2], [1, 0])
        self.assertEqual(frame["wave"][:2], [1, -1])

    def test_animation_reacts_then_fades_when_capture_disappears(self):
        motion = MotionState()
        motion.update(decode_frame(payload(), now=100), now=0)
        self.assertGreater(motion.bands[0], 0)
        self.assertEqual(motion.beat, 1)
        for index in range(1, 61):
            motion.update(None, now=index / 30)
        self.assertLess(max(motion.bands), .001)
        self.assertLess(motion.beat, .001)

    def test_silence_does_not_create_motion(self):
        data = payload()
        for key in ("bands", "wave"):
            data[key] = [0] * len(data[key])
        for key in ("bass", "mid", "treble", "beat", "rms", "peak"):
            data[key] = 0
        motion = MotionState()
        for index in range(30):
            motion.update(decode_frame(data, now=100), now=index / 30)
        self.assertEqual(motion.bands, [0] * 48)
        self.assertEqual(motion.beat, 0)

    def test_smoothing_is_independent_of_render_frame_rate(self):
        frame = decode_frame(payload(), now=100)
        values = []
        for fps in (20, 60):
            motion = MotionState()
            motion.update(None, now=0)
            for index in range(1, fps + 1):
                motion.update(frame, now=index / fps)
            values.append(motion.bands[0])
        self.assertAlmostEqual(values[0], values[1], places=7)


if __name__ == "__main__":
    unittest.main()
