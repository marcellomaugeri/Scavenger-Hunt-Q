"""Full-frame image inference with no backlog or movement tracking."""
import logging
import threading
import time

log = logging.getLogger('scavenger.inference')


class FrameDetector:
    """Evaluate the latest complete camera frame with one stateless model pass."""

    def __init__(self, camera, url, on_result, resolution=(416, 416)):
        self.camera = camera
        self.url = url
        self.on_result = on_result
        self.resolution = resolution
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name='FrameDetector', daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=6)

    def _run(self):
        import cv2
        import requests
        from camera_feed import letterbox_frame
        from recognition import map_frame_boxes
        with requests.Session() as session:
            while not self._stop.is_set():
                try:
                    sample = self.camera.capture_sample()
                    if sample is None:
                        continue
                    frame, captured_at = sample
                    height, width = frame.shape[:2]
                    if (width, height) != self.camera.resolution:
                        raise ValueError(f'Expected {self.camera.resolution}, received {(width, height)}')
                    started = time.monotonic()
                    image = letterbox_frame(frame, self.resolution)
                    encoded, jpeg = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, 95])
                    if not encoded:
                        raise ValueError('Could not encode detector input')
                    response = session.post(self.url + '/api/image',
                                            files={'file': ('frame.jpg', jpeg.tobytes(), 'image/jpeg')},
                                            timeout=(2, 5))
                    response.raise_for_status()
                    result = response.json().get('result', {})
                    boxes = map_frame_boxes(result.get('bounding_boxes'), width, height, self.resolution)
                    if self._stop.is_set():
                        return
                    # No frame backlog or tracking: the next capture returns the latest image.
                    self.on_result(boxes, captured_at, time.monotonic() - started)
                except Exception:
                    log.exception('Full-frame inference unavailable; retrying')
                    self._stop.wait(1)
