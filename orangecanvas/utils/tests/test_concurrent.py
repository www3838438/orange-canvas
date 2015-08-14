import unittest
import threading
import time
from concurrent import futures

from AnyQt.QtCore import QObject, QCoreApplication, QThread, QThreadPool
from AnyQt.QtCore import pyqtSlot as Slot

from .. import concurrent


class StateSetter(QObject):
    state = None
    calling_thread = None

    @Slot(object)
    def set_state(self, value):
        self.state = value
        self.calling_thread = QThread.currentThread()


class State(object):
    def __init__(self, state):
        self.state = state

    def set(self, state):
        self.state = state


class _BaseCoreTest(unittest.TestCase):
    def setUp(self):
        self.app = QCoreApplication([])

    def tearDown(self):
        del self.app


class TestExecutor(_BaseCoreTest):

    def test_executor(self):
        executor = concurrent.ThreadPoolExecutor()
        f1 = executor.submit(pow, 100, 100)

        f2 = executor.submit(lambda: 1 / 0)

        f3 = executor.submit(QThread.currentThread)

        self.assertTrue(f1.result(), pow(100, 100))

        with self.assertRaises(ZeroDivisionError):
            f2.result()

        self.assertIsInstance(f2.exception(), ZeroDivisionError)

        self.assertIsNot(f3.result(), QThread.currentThread())

        executor.shutdown(wait=True)

    def test_executor_map(self):
        executor = concurrent.ThreadPoolExecutor()

        r = executor.map(pow, list(range(1000)), list(range(1000)))

        results = list(r)

        self.assertTrue(len(results) == 1000)


class TestMethodInvoke(_BaseCoreTest):

    def test_method_invoke(self):
        executor = concurrent.ThreadPoolExecutor()

        def func(callback):
            callback(QThread.currentThread())

        obj = StateSetter()
        f1 = executor.submit(
            func, concurrent.method_invoke(obj.set_state, (object,)))
        f1.result()

        # Flush the event queue, so the queued method can be called
        QCoreApplication.sendPostedEvents(obj, 0)

        self.assertIs(obj.calling_thread, QThread.currentThread(),
                      "set_state was called from the wrong thread")

        self.assertIsNot(obj.state, QThread.currentThread(),
                         "set_state was invoked in the main thread")

        executor.shutdown(wait=True)


class TestFutureWatcher(_BaseCoreTest):
    def test_watcher(self):
        executor = concurrent.ThreadPoolExecutor(
            threadPool=QThreadPool(maxThreadCount=1)
        )
        event = threading.Event()
        event.clear()
        # Block the worker thread to ensure subsequent future can be cancelled
        executor.submit(event.wait)

        cancelled = State(False)
        watcher = concurrent.FutureWatcher()
        watcher.cancelled.connect(lambda: cancelled.set(True))

        f = executor.submit(lambda: None)
        watcher.setFuture(f)

        self.assertTrue(f.cancel())

        # Unblock the work thread
        event.set()

        with self.assertRaises(futures.CancelledError):
            f.result()

        # ensure the waiters/watchers were notified (by
        # set_running_or_notify_cancelled in the worker thread)
        f = executor.submit(lambda: None)
        f.result()

        QCoreApplication.sendPostedEvents(watcher, 0)

        self.assertTrue(cancelled.state)

        finished = State(False)
        result = State(None)
        exception = State(None)

        watcher = concurrent.FutureWatcher()
        watcher.done.connect(self.app.quit)
        watcher.finished.connect(lambda: finished.set(True))
        watcher.resultReady.connect(result.set)
        watcher.exceptionReady.connect(exception.set)

        watcher.setFuture(executor.submit(lambda: time.sleep(0.1) or 42))
        self.app.exec_()

        self.assertEqual(watcher.result(), 42)
        self.assertTrue(finished.state)
        self.assertEqual(result.state, 42)
        self.assertIs(exception.state, None)

        finished = State(False)
        result = State(None)
        exception = State(None)

        watcher = concurrent.FutureWatcher()
        watcher.done.connect(self.app.quit)
        watcher.finished.connect(lambda: finished.set(True))
        watcher.resultReady.connect(result.set)
        watcher.exceptionReady.connect(exception.set)

        watcher.setFuture(executor.submit(lambda: time.sleep(0.1) or 1 / 0))
        self.app.exec_()

        with self.assertRaises(ZeroDivisionError):
            watcher.result()

        self.assertTrue(finished.state)
        self.assertEqual(result.state, None)
        self.assertIsInstance(exception.state, ZeroDivisionError)

        executor.shutdown()


class TestAnonymousSubmit(_BaseCoreTest):
    def test_concurrent_run(self):
        f = concurrent.submit(lambda i: (i ** i ** i ** i), 2)
        f.result()
