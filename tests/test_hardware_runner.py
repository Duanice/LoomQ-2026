import unittest

from starter_kit.hardware_runner import (
    _counts_from_probabilities,
    _redact,
    _verify_bell,
)


class HardwareRunnerTests(unittest.TestCase):
    def test_bell_peaks_and_secret_redaction(self):
        self.assertEqual(_verify_bell({"00": 500, "01": 10, "10": 9, "11": 505}, 1024), ["11", "00"])
        self.assertEqual(
            _redact({"token": "secret", "nested": {"api_key": "secret"}}),
            {"token": "<redacted>", "nested": {"api_key": "<redacted>"}},
        )
        self.assertEqual(
            _counts_from_probabilities({"00": 0.49, "11": 0.51}, 1024),
            {"00": 502, "11": 522},
        )


if __name__ == "__main__":
    unittest.main()
