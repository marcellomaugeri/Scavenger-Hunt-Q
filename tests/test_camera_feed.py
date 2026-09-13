import queue
import sys
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app/python'))


class NativeCamera:
    """Hardware-boundary double: blocking reads and explicit unplug failures."""

    def __init__(self):
        self.frames = queue.Queue()
        self.calls = []
        self.stopped = threading.Event()

    def start(self):
        self.calls.append(('start', threading.get_ident()))

    def capture(self):
        self.calls.append(('capture', threading.get_ident()))
        try:
            frame = self.frames.get(timeout=.1)
        except queue.Empty:
            return None
        if isinstance(frame, Exception):
            raise frame
        return frame

    def stop(self):
        self.calls.append(('stop', threading.get_ident()))
        self.stopped.set()


class CameraTests(unittest.TestCase):
    def test_sample_time_precedes_a_delayed_read_and_round_boundary(self):
        now = [100.0]
        release = threading.Event()
        frame = object()
        native = NativeCamera()
        reads = []

        def capture():
            reads.append(True)
            if len(reads) == 1:
                now[0] = 100.4  # A round may have started at 100.2 meanwhile.
                return frame
            release.wait(2)
            return None

        native.capture = capture
        with patch('camera_feed.time.monotonic', side_effect=lambda: now[0]):
            camera = self.make_camera(lambda **_: native)
            camera.start()
            try:
                sample = camera.capture_sample()
                self.assertIsNotNone(sample)
                self.assertEqual(sample, (frame, 100.0))
            finally:
                release.set()
                camera.stop()

    def make_camera(self, factory, **kwargs):
        try:
            from camera_feed import SharedCamera
        except ImportError:
            self.fail('Resilient shared camera is not implemented')
        camera = SharedCamera(camera_factory=factory, **kwargs)
        self.addCleanup(camera.stop)
        return camera

    def wait_for(self, predicate, timeout=3):
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() >= deadline:
                self.fail('Camera condition did not become true')
            time.sleep(.005)

    def test_boot_without_camera_does_not_block_and_retries(self):
        attempts = []

        def missing(**kwargs):
            attempts.append(time.monotonic())
            raise OSError('USB camera is absent')

        camera = self.make_camera(missing)
        started = time.monotonic()
        camera.start()
        camera.start()
        self.assertLess(time.monotonic() - started, .15)
        self.assertTrue(camera.is_started())
        self.wait_for(lambda: len(attempts) >= 2)
        self.assertGreater(attempts[1] - attempts[0], .1)
        self.assertEqual(camera.status, 'disconnected')
        started = time.monotonic()
        self.assertIsNone(camera.capture())
        self.assertGreater(time.monotonic() - started, .3)
        self.assertLess(time.monotonic() - started, .8)

    def test_missing_camera_recovers_and_configures_native_camera(self):
        native = NativeCamera()
        frame = object()
        native.frames.put(frame)
        attempts = []
        seen = []

        def factory(**kwargs):
            attempts.append(kwargs)
            if len(attempts) == 1:
                raise OSError('USB camera is absent')
            return native

        camera = self.make_camera(factory, on_frame=seen.append, codec='MJPG')
        camera.start()
        self.assertIs(camera.capture(), frame)
        self.assertEqual(camera.status, 'streaming')
        self.assertEqual(seen, [frame])
        self.assertEqual(attempts, [
            {'resolution': (640, 480), 'fps': 15, 'auto_reconnect': False, 'codec': 'MJPG'},
            {'resolution': (640, 480), 'fps': 15, 'auto_reconnect': False, 'codec': 'MJPG'},
        ])

    def test_readers_share_one_owner_and_each_receive_the_same_frame(self):
        native = NativeCamera()
        constructions = []

        def factory(**kwargs):
            constructions.append(threading.get_ident())
            return native

        camera = self.make_camera(factory)
        camera.start()
        camera.start()
        results = []
        readers = [threading.Thread(target=lambda: results.append(camera.capture())) for _ in range(3)]
        for reader in readers:
            reader.start()
        frame = object()
        native.frames.put(frame)
        for reader in readers:
            reader.join(1)
            self.assertFalse(reader.is_alive())
        camera.stop()
        self.assertEqual(results, [frame] * 3)
        self.assertEqual(len(constructions), 1)
        self.assertEqual({owner for _, owner in native.calls}, set(constructions))
        self.assertNotEqual(constructions[0], threading.get_ident())
        self.assertTrue(native.stopped.is_set())
        self.assertFalse(camera.is_started())

    def test_same_reader_never_receives_a_duplicate_or_disconnected_frame(self):
        native = NativeCamera()
        frame = object()
        native.frames.put(frame)
        camera = self.make_camera(lambda **kwargs: native)
        camera.start()
        self.assertIs(camera.capture(), frame)
        native.frames.put(OSError('USB disconnected'))
        self.wait_for(lambda: camera.status == 'disconnected')
        self.assertIsNone(camera.capture())
        other_reader = []
        reader = threading.Thread(target=lambda: other_reader.append(camera.capture()))
        reader.start()
        reader.join(1)
        self.assertEqual(other_reader, [None])

    def test_blocked_native_read_expires_frame_and_stop_wakes_waiting_readers(self):
        native = NativeCamera()
        unblock = threading.Event()
        self.addCleanup(unblock.set)
        frame = object()
        reads = []

        def capture():
            reads.append(threading.get_ident())
            if len(reads) == 1:
                return frame
            unblock.wait(4)
            return None

        native.capture = capture
        camera = self.make_camera(lambda **kwargs: native)
        camera.start()
        self.assertIs(camera.capture(), frame)
        before = time.monotonic()
        self.assertIsNone(camera.capture())
        self.assertGreater(time.monotonic() - before, .3)
        self.wait_for(lambda: camera.status == 'disconnected', timeout=2)
        stale_reader = []
        reader = threading.Thread(target=lambda: stale_reader.append(camera.capture()))
        reader.start()
        reader.join(1)
        self.assertEqual(stale_reader, [None])
        waiting = []
        reader = threading.Thread(target=lambda: waiting.append(camera.capture()))
        reader.start()
        unblock.set()
        camera.stop()
        reader.join(.3)
        self.assertEqual(waiting, [None])
        self.assertTrue(native.stopped.is_set())
        self.assertEqual(camera.status, 'disconnected')

    def test_failed_start_is_closed_by_owner_then_new_camera_recovers(self):
        failed = NativeCamera()
        native = NativeCamera()
        frame = object()
        native.frames.put(frame)
        devices = iter([failed, native])

        def fail_start():
            failed.calls.append(('start', threading.get_ident()))
            raise OSError('Could not open camera')

        failed.start = fail_start
        camera = self.make_camera(lambda **kwargs: next(devices))
        camera.start()
        self.assertIs(camera.capture(), frame)
        self.assertTrue(failed.stopped.is_set())
        self.assertEqual(len({owner for _, owner in failed.calls}), 1)

    def test_read_failure_recovers_with_a_new_native_camera(self):
        first, recovered = NativeCamera(), NativeCamera()
        before, after = object(), object()
        first.frames.put(before)
        recovered.frames.put(after)
        devices = iter([first, recovered])
        camera = self.make_camera(lambda **kwargs: next(devices))
        camera.start()
        self.assertIs(camera.capture(), before)
        first.frames.put(OSError('USB disconnected'))
        self.assertIs(camera.capture(), after)
        self.assertTrue(first.stopped.is_set())
        self.assertEqual(camera.status, 'streaming')

    def test_stop_and_restart_create_a_new_native_owner(self):
        first, second = NativeCamera(), NativeCamera()
        before, after = object(), object()
        first.frames.put(before)
        second.frames.put(after)
        devices = iter([first, second])
        camera = self.make_camera(lambda **kwargs: next(devices))
        camera.start()
        self.assertIs(camera.capture(), before)
        camera.stop()
        camera.stop()
        self.assertTrue(first.stopped.is_set())
        self.assertIsNone(camera.capture())
        camera.start()
        self.assertIs(camera.capture(), after)
        self.assertTrue(camera.is_started())

    def test_usb_buffers_are_configured_by_owner_before_capture_and_after_reconnect(self):
        class UsbCamera(NativeCamera):
            v4l_path = '/dev/video2'

            def start(self):
                super().start()
                self.buffer_count = 1
                self._cap = SimpleNamespace(set=self.set_buffer_count,
                                            get=lambda prop: self.buffer_count)

            def set_buffer_count(self, prop, count):
                self.calls.append(('configure', threading.get_ident()))
                self.buffer_count = count
                return True

            def capture(self):
                if self.buffer_count != 4:
                    raise OSError('Capture started with too few USB buffers')
                return super().capture()

        first, second = UsbCamera(), UsbCamera()
        before, after = object(), object()
        first.frames.put(before)
        second.frames.put(after)
        devices = iter([first, second])
        with patch.dict(sys.modules, {'cv2': SimpleNamespace(CAP_PROP_BUFFERSIZE=38)}):
            camera = self.make_camera(lambda **kwargs: next(devices))
            camera.start()
            self.assertIs(camera.capture(), before)
            first.frames.put(OSError('USB disconnected'))
            self.assertIs(camera.capture(), after)
            camera.stop()
        for native in (first, second):
            self.assertEqual([name for name, _ in native.calls][:3],
                             ['start', 'configure', 'capture'])
            self.assertEqual(len({owner for _, owner in native.calls}), 1)
            self.assertTrue(native.stopped.is_set())

    def test_unsupported_usb_buffer_setting_keeps_camera_available(self):
        native = NativeCamera()
        native.v4l_path = '/dev/video2'
        native._cap = SimpleNamespace(set=lambda prop, count: False,
                                      get=lambda prop: 1)
        frame = object()
        native.frames.put(frame)
        with patch.dict(sys.modules, {'cv2': SimpleNamespace(CAP_PROP_BUFFERSIZE=38)}):
            with self.assertLogs('scavenger.camera', level='WARNING') as logs:
                camera = self.make_camera(lambda **kwargs: native)
                camera.start()
                self.assertIs(camera.capture(), frame)
                camera.stop()
        self.assertTrue(any('buffer' in message.lower() for message in logs.output))

    def test_slow_constructor_does_not_block_start_or_shutdown_readers(self):
        entered, release = threading.Event(), threading.Event()
        native = NativeCamera()
        self.addCleanup(release.set)

        def factory(**kwargs):
            entered.set()
            release.wait(4)
            return native

        camera = self.make_camera(factory)
        before = time.monotonic()
        camera.start()
        self.assertLess(time.monotonic() - before, .15)
        self.assertTrue(entered.wait(1))
        waiting = []
        reader = threading.Thread(target=lambda: waiting.append(camera.capture()))
        reader.start()
        stopping = threading.Thread(target=camera.stop)
        stopping.start()
        reader.join(.3)
        self.assertEqual(waiting, [None])
        release.set()
        stopping.join(1)
        self.assertFalse(stopping.is_alive())
        self.assertTrue(native.stopped.is_set())
        self.assertEqual([name for name, _ in native.calls], ['stop'])


if __name__ == '__main__':
    unittest.main()
