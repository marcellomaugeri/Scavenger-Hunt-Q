"""One reconnecting camera owner shared by inference and browser readers."""

import logging
import threading
import time


log = logging.getLogger('scavenger.camera')


def _configure_usb_buffers(native):
    """Keep USB transfers queued while the owner drains frames continuously.

    App Bricks 0.12's V4LCamera forces one buffer and has no public setting.
    Configure its OpenCV handle after start, on the same owner thread. Other
    camera backends keep their own settings; unsupported drivers still work.
    """
    capture = getattr(native, '_cap', None)
    if not hasattr(native, 'v4l_path') or capture is None:
        return
    import cv2
    if capture.set(cv2.CAP_PROP_BUFFERSIZE, 4):
        actual = capture.get(cv2.CAP_PROP_BUFFERSIZE)
        if actual == 4:
            log.info('USB camera capture configured with four buffers')
            return
        log.warning('USB camera reports %s buffers after requesting four', actual)
    else:
        log.warning('USB camera driver rejected four buffers; keeping its default')


class SharedCamera:
    """Start immediately, then open hardware and publish fresh frames in a worker.

    Readers receive each generation at most once per thread. Frames are shared
    without copying; consumers and ``on_frame`` must treat them as read-only.
    ``is_started()`` describes the worker; ``status`` describes fresh video.
    """

    def __init__(self, resolution=(640, 480), fps=15, on_frame=None, camera_factory=None, codec=None, capture_resolution=None):
        if fps <= 0:
            raise ValueError('FPS must be positive')
        self.resolution = resolution
        self.capture_resolution = capture_resolution or resolution
        self.fps = fps
        self.codec = codec
        self._on_frame = on_frame
        self._camera_factory = camera_factory
        self._condition = threading.Condition()
        self._stop = threading.Event()
        self._stop.set()
        self._thread = None
        self._readers = threading.local()
        self._frame = None
        self._frame_at = 0.0
        self._generation = 0

    def start(self):
        """Idempotent and nonblocking, including when no camera is attached."""
        with self._condition:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name='SharedCamera', daemon=True)
            self._thread.start()

    def stop(self):
        """Wake readers and request owner cleanup; never close hardware concurrently.

        A native driver call cannot be cancelled safely. If it is stuck, return
        after two seconds; the owner closes the camera when that call returns.
        """
        with self._condition:
            self._stop.set()
            self._frame = None
            self._condition.notify_all()
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)

    def is_started(self):
        with self._condition:
            return not self._stop.is_set() and self._thread is not None and self._thread.is_alive()

    def _fresh(self):
        return not self._stop.is_set() and self._frame is not None and time.monotonic() - self._frame_at < 1.0

    @property
    def status(self):
        with self._condition:
            return 'streaming' if self._fresh() else 'disconnected'

    def capture(self):
        """Return a new fresh frame, or None after at most half a second."""
        sample = self.capture_sample()
        return sample[0] if sample is not None else None

    def capture_sample(self):
        """Keep the image and its acquisition timestamp together for inference."""
        deadline = time.monotonic() + .5
        with self._condition:
            while not self._stop.is_set():
                if self._fresh() and getattr(self._readers, 'generation', -1) != self._generation:
                    self._readers.generation = self._generation
                    return self._frame, self._frame_at
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(remaining)
        return None

    def _disconnect(self):
        with self._condition:
            self._frame = None
            self._condition.notify_all()

    def _run(self):
        delay = .25
        try:
            while not self._stop.is_set():
                native = None
                try:
                    factory = self._camera_factory
                    if factory is None:
                        from arduino.app_peripherals.camera import Camera
                        factory = Camera
                    options = dict(resolution=self.capture_resolution, fps=self.fps, auto_reconnect=False)
                    if self.codec is not None:
                        options['codec'] = self.codec
                    native = factory(**options)
                    if self._stop.is_set():
                        break
                    native.start()
                    _configure_usb_buffers(native)
                    while not self._stop.is_set():
                        captured_at = time.monotonic()
                        frame = native.capture()
                        if frame is None:
                            raise OSError('Camera returned no frame')
                        if self.capture_resolution != self.resolution:
                            height, width = frame.shape[:2]
                            crop_width, crop_height = self.resolution
                            if width < crop_width or height < crop_height:
                                raise OSError(f'Camera frame {width}x{height} is smaller than the requested crop')
                            left, top = (width - crop_width) // 2, (height - crop_height) // 2
                            frame = frame[top:top + crop_height, left:left + crop_width]
                        if self._stop.is_set():
                            break
                        if self._on_frame is not None:
                            try:
                                self._on_frame(frame)
                            except Exception:
                                log.exception('Camera frame callback failed')
                        with self._condition:
                            if self._stop.is_set():
                                break
                            self._frame = frame
                            # Preserve the read-start watermark across a slow driver call.
                            self._frame_at = captured_at
                            self._generation += 1
                            self._condition.notify_all()
                        delay = .25
                        self._stop.wait(max(0, 1.0 / self.fps - (time.monotonic() - captured_at)))
                except Exception as exc:
                    log.warning('Camera unavailable; retrying: %s', exc)
                finally:
                    self._disconnect()
                    if native is not None:
                        try:
                            native.stop()
                        except Exception:
                            log.exception('Camera cleanup failed')
                if self._stop.wait(delay):
                    break
                delay = min(delay * 2, 5.0)
        finally:
            self._disconnect()


def letterbox_frame(frame, resolution=(416, 416)):
    """Downsample the complete frame once, preserving proportions and all edges."""
    import cv2
    from recognition import frame_layout
    height, width = frame.shape[:2]
    layout = frame_layout(width, height, resolution)
    resized = cv2.resize(frame, (layout['resized_width'], layout['resized_height']), interpolation=cv2.INTER_AREA)
    return cv2.copyMakeBorder(resized, layout['pad_y'], resolution[1] - layout['resized_height'] - layout['pad_y'],
                             layout['pad_x'], resolution[0] - layout['resized_width'] - layout['pad_x'],
                             cv2.BORDER_CONSTANT, value=(114, 114, 114))
