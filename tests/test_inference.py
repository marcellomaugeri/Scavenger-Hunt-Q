"""Inference transport and scoring-readiness checks without models or hardware."""
import ast
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app/python'))
from inference import FrameDetector
import recognition


class InferenceReadinessTests(unittest.TestCase):
    def callback(self):
        # Load the actual callback without executing main.py's hardware startup.
        source = ROOT / 'app/python/main.py'
        tree = ast.parse(source.read_text())
        callback = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'inference_seen')
        health = {'last_inference_at': 50.0, 'detections': 0}
        queued = []
        from engine.events import GameEvent, GameEventType
        scope = {'time': SimpleNamespace(monotonic=lambda: 100.0), 'MAX_DETECTION_AGE': 1.5,
                 'CAMERA_RESOLUTION': (1440, 1080), 'select_recognition': recognition.select_recognition,
                 'health': health, 'health_lock': threading.Lock(),
                 'ui': SimpleNamespace(send_message=lambda *_: None),
                 'GameEvent': GameEvent, 'GameEventType': GameEventType,
                 'enqueue': lambda *args, **kwargs: queued.append((args, kwargs))}
        exec(compile(ast.Module(body=[callback], type_ignores=[]), str(source), 'exec'), scope)
        return scope['inference_seen'], health, queued

    def test_stale_results_cannot_keep_scoring_ready(self):
        callback, health, queued = self.callback()
        callback([], captured_at=98.0, duration=2.0)
        self.assertEqual(health['last_inference_at'], 50.0)
        self.assertEqual(queued, [])
        self.assertEqual(health['inference_frames'], 1)  # Diagnostics still report the reply.

    def test_fresh_empty_results_are_healthy(self):
        callback, health, queued = self.callback()
        callback([], captured_at=99.0, duration=1.0)
        self.assertEqual(health['last_inference_at'], 100.0)
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0][0][0].payload, {})
        self.assertEqual(queued[0][1]['captured_at'], 99.0)

    def test_guidance_includes_other_classes_and_scoring_keeps_every_instance(self):
        callback, health, queued = self.callback()
        boxes = [dict(label=label, value=confidence, team='red', x=10, y=10,
                      width=width, height=height)
                 for label, confidence, width, height in (
                     ('person', .99, 700, 1000), ('bed', .95, 650, 800),
                     ('bottle', .55, 300, 400), ('bottle', .72, 100, 200))]
        boxes.append({**boxes[0], 'label': []})
        callback(boxes, captured_at=99.0, duration=1.0)
        self.assertEqual(health['last_recognition']['red']['label'], 'bed')
        detections = queued[0][0][0].payload
        self.assertIn('bed', detections)
        self.assertEqual([item['confidence'] for item in detections['bottle']], [.55, .72])


class FrameDetectorTests(unittest.TestCase):
    def test_image_endpoint_preserves_sample_time_and_reports_empty_result(self):
        frame = SimpleNamespace(shape=(1080, 1440, 3))
        camera = SimpleNamespace(resolution=(1440, 1080), capture_sample=lambda: (frame, 123.0))
        results, requests = [], []
        detector = FrameDetector(camera, 'http://detector:1337', lambda *args: results.append(args))

        class Session:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def post(self, url, **kwargs):
                requests.append((url, kwargs))
                return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {'result': {'bounding_boxes': []}})

        def receive(*args):
            results.append(args)
            detector._stop.set()

        detector.on_result = receive
        cv2 = SimpleNamespace(IMWRITE_JPEG_QUALITY=1, imencode=lambda *_: (True, SimpleNamespace(tobytes=lambda: b'jpeg')))
        with patch.dict(sys.modules, {'cv2': cv2, 'requests': SimpleNamespace(Session=Session)}), \
             patch('camera_feed.letterbox_frame', return_value=frame):
            detector._run()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][:2], ([], 123.0))
        self.assertEqual(requests[0][0], 'http://detector:1337/api/image')
        self.assertEqual(requests[0][1]['files']['file'], ('frame.jpg', b'jpeg', 'image/jpeg'))
        self.assertEqual(requests[0][1]['timeout'], (2, 5))


if __name__ == '__main__':
    unittest.main()
