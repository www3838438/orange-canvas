"""
Utilities for building concurrent PyQt5 applications.

"""

from __future__ import absolute_import

import threading
import weakref
import logging
import warnings
import concurrent.futures
import types
from typing import Callable, Optional, Any, Tuple, Union

from AnyQt.QtCore import (
    Qt, QObject, Q_ARG, QMetaObject, QThreadPool, QRunnable, QEvent,
    QCoreApplication
)
from AnyQt.QtCore import pyqtSignal as Signal


def method_invoke(method, sig, conntype=Qt.QueuedConnection):
    """
    Return a callable to invoke a QObject's method registered with Qt
    meta object system.

    This wrapper can be used to invoke/call a method across thread
    boundaries.

    NOTE
    ----
    An event loop MUST be running in the target QObject's thread.

    Note
    ----
    It it the callers responsibility to keep the receiving object alive.

    Parameters
    ----------
    method : Union[types.MethodType, types.BuiltinMethodType]
        A bound method of a QObject registered with the QObject meta system
        (decorated by a Slot or Signature decorators)
    sig : Tuple[type]
        A tuple of positional argument types.
    conntype: Qt.ConnectionType
        The connection/call type (Qt.QueuedConnection and
        Qt.BlockingConnection are the most interesting)

    See Also
    --------
    QtCore.QMetaObject.invokeMethod

    Example
    -------
    >>> app = QCoreApplication.instance() or QCoreApplication([])
    >>> quit = method_invoke(app.quit, ())
    >>> t = threading.Thread(target=quit)
    >>> t.start()
    >>> app.exec()
    0
    """
    name = method.__name__
    obj = method.__self__

    if not isinstance(obj, QObject):
        raise TypeError("Require a QObject's bound method.")

    ref = weakref.ref(obj)

    # TODO: Check that the method with name exists in object's metaObject()
    def call(*args):
        obj = ref()
        if obj is None:
            return False

        args = [Q_ARG(atype, arg) for atype, arg in zip(sig, args)]
        return QMetaObject.invokeMethod(obj, name, conntype, *args)

    return call

# TODO: Should this aliases be here? Let the client import directly from stdlib

#: Alias for `concurrent.futures.Future`
Future = concurrent.futures.Future

#: Alias for `concurrent.futures.CancelledError`
CancelledError = concurrent.futures.CancelledError

#: Alias for `concurrent.futures.TimeoutError`
TimeoutError = concurrent.futures.TimeoutError

#: Alias for `concurrent.futures.FIRST_COMPLETED`
FIRST_COMPLETED = concurrent.futures.FIRST_COMPLETED
#: Alias for `concurrent.futures.FIRST_EXCEPTION`
FIRST_EXCEPTION = concurrent.futures.FIRST_EXCEPTION
#: Alias for `concurrent.futures.ALL_COMPLETED`
ALL_COMPLETED = concurrent.futures.ALL_COMPLETED

#: Alias for `concurrent.futures.wait`
wait = concurrent.futures.wait

#: Alias for `concurrent.futures.as_completed`
as_completed = concurrent.futures.as_completed


class ThreadPoolExecutor(QObject, concurrent.futures.Executor):
    """
    A concurrent.futures.Executor using a QThreadPool.

    Parameters
    ----------
    parent : QObject
        Parent object.
    threadPool : QThreadPool or None
        The thread pool to use for thread allocations.
        If `None` then `QThreadPool.globalInstance()` will be used.
    trackPending : bool
        Should the `ThreadPoolExecutor` track unfinished futures in order to
        wait on them when calling `shutdown(wait=True)`. Setting this to
        `False` signifies that the executor will be shut down without waiting
        (default: True)

    See also
    --------
    concurrent.futures.Executor
    """
    def __init__(self, parent=None, threadPool=None, trackPending=True,
                 **kwargs):
        super(ThreadPoolExecutor, self).__init__(parent=parent, **kwargs)
        if threadPool is None:
            threadPool = QThreadPool.globalInstance()
        self.__threadPool = threadPool
        #: A lock guarding the internal state
        self.__lock = threading.Lock()
        #: A set of all pending uncompleted futures
        #: Since we are using possibly shared thread pool we
        #: cannot just wait on it as it can also run task from other
        #: executors, ...
        self.__pending_futures = set()
        #: Was the executor shutdown?
        self.__shutdown = False
        #: Should this executor track non-completed futures. If False then
        #: `executor.shutdown(wait=True)` will not actually wait for all
        #: futures to be done. It is the client's responsibility to keep track
        #: of the futures and their done state (use concurrent.wait)
        self.__track = trackPending

    def submit(self, fn, *args, **kwargs):
        """
        Reimplemented from `concurrent.futures.Executor.submit`
        """
        with self.__lock:
            if self.__shutdown:
                raise RuntimeError(
                    "cannot schedule new futures after shutdown")
            future = concurrent.futures.Future()

            def notify_done(future):
                with self.__lock:
                    self.__pending_futures.remove(future)

            task = FutureRunnable(future, fn, args, kwargs)
            task.setAutoDelete(True)
            if self.__track:
                future.add_done_callback(notify_done)
                self.__pending_futures.add(future)

            self.__threadPool.start(task)
            return future

    def shutdown(self, wait=True):
        """
        Reimplemented from `concurrent.futures.Executor.shutdown`

        Note
        ----
        If wait is True then all futures submitted through this executor will
        be waited on (this requires that they be tracked (see `trackPending`).

        This is in contrast to `concurrent.future.ThreadPoolExecutor` where
        the threads themselves are joined. This class cannot do that since it
        does not own the threads (they are owned/managed by a QThreadPool)
        """
        futures = None
        with self.__lock:
            self.__shutdown = True
            if wait:
                futures = list(self.__pending_futures)
        if wait and not self.__track:
            warnings.warn(
                "`shutdown` called with wait=True, but `trackPending` was "
                "set to False at initialization.",
                UserWarning, stacklevel=2
            )
        if wait:
            concurrent.futures.wait(futures)


class FutureRunnable(QRunnable):
    """
    A QRunnable to fulfil a `Future` in a QThreadPool managed thread.

    Parameters
    ----------
    future : concurrent.futures.Future
        Future whose contents will be set with the result of executing
        `func(*args, **kwargs)` after completion
    func : Callable
        Function to invoke in a thread
    args : tuple
        Positional arguments for `func`
    kwargs : dict
        Keyword arguments for `func`

    Example
    -------
    >>> f = concurrent.futures.Future()
    >>> task = FutureRunnable(f, int, (42,), {})
    >>> QThreadPool.globalInstance().start(task)
    >>> f.result()
    42
    """
    def __init__(self, future, func, args, kwargs):
        # type: (Future, Callable, tuple, dict) -> None
        super(FutureRunnable, self).__init__()
        self.future = future
        self.task = (func, args, kwargs)

    def run(self):
        """
        Reimplemented from `QRunnable.run`
        """
        try:
            if not self.future.set_running_or_notify_cancel():
                # future was cancelled
                return
            func, args, kwargs = self.task
            self.task = None
            try:
                result = func(*args, **kwargs)
            except BaseException as ex:
                self.future.set_exception(ex)
            else:
                self.future.set_result(result)
        except BaseException:
            log = logging.getLogger(__name__)
            log.critical("Exception in worker thread.", exc_info=True)


def submit(func, *args, **kwargs):
    """
    Schedule a callable `func` to run in a global QThreadPool.

    Parameters
    ----------
    func : callable
    args : tuple
        Positional arguments for `func`
    kwargs : dict
        Keyword arguments for `func`

    Returns
    -------
    future : Future
        Future with the (eventual) result of `func(*args, **kwargs)`

    Example
    -------
    >>> f = submit(pow, 10, 10)
    >>> f.result()
    10000000000
    """
    f = concurrent.futures.Future()
    task = FutureRunnable(f, func, args, kwargs)
    QThreadPool.globalInstance().start(task)
    return f



class FutureWatcher(QObject):
    """
    An `QObject` watching the state changes of a `concurrent.futures.Future`.

    Note
    ----
    The state change notification signals (`done`, `finished`, ...)
    are always emitted when the control flow reaches the event loop
    (even if the future is already completed when set).

    Note
    ----
    An event loop must be running, otherwise the notifier signals will
    not be emitted.

    Parameters
    ----------
    parent : QObject
        Parent object.
    future : Future
        The future instance to watch.

    Example
    -------
    >>> app = QCoreApplication.instance() or QCoreApplication([])
    >>> f = submit(lambda i, j: i ** j, 10, 3)
    >>> watcher = FutureWatcher(f)
    >>> watcher.resultReady.connect(lambda res: print("Result:", res))
    >>> watcher.done.connect(app.quit)
    >>> _ = app.exec()
    Result: 1000
    >>> f.result()
    1000
    """
    #: Signal emitted when the future is done (cancelled or finished)
    done = Signal(Future)

    #: Signal emitted when the future is finished (i.e. returned a result
    #: or raised an exception - but not if cancelled)
    finished = Signal(Future)

    #: Signal emitted when the future was cancelled
    cancelled = Signal(Future)

    #: Signal emitted with the future's result when successfully finished.
    resultReady = Signal(object)

    #: Signal emitted with the future's exception when finished with an
    #: exception.
    exceptionReady = Signal(BaseException)

    # A private event type used to notify the watcher of a Future's completion
    __FutureDone = QEvent.registerEventType()

    def __init__(self, future=None, parent=None, **kwargs):
        super(FutureWatcher, self).__init__(parent, **kwargs)
        self.__future = None  # type: concurrent.futures.Future
        if future is not None:
            self.setFuture(future)

    def setFuture(self, future):
        # type: (Future) -> None
        """
        Set the future to watch.

        Raise a `RuntimeError` if a future is already set.

        Parameters
        ----------
        future : Future
        """
        if self.__future is not None:
            raise RuntimeError("Future already set")

        self.__future = future
        selfweakref = weakref.ref(self)

        def on_done(f):
            assert f is future
            selfref = selfweakref()

            if selfref is None:
                return

            try:
                QCoreApplication.postEvent(
                    selfref, QEvent(FutureWatcher.__FutureDone))
            except RuntimeError:
                # Ignore RuntimeErrors (when C++ side of QObject is deleted)
                # (? Use QObject.destroyed and remove the done callback ?)
                pass

        future.add_done_callback(on_done)

    def future(self):
        # type: () -> Future
        """
        Return the future.
        """
        return self.__future

    def result(self):
        # type: () -> Any
        """
        Return the future's result.

        Note
        ----
        This method is non-blocking. If the future has not yet completed
        it will raise an error.
        """
        try:
            return self.__future.result(timeout=0)
        except TimeoutError:
            raise RuntimeError("Future is not yet done")

    def exception(self):
        # type: () -> Optional[BaseException]
        """
        Return the future's exception.

        Note
        ----
        This method is non-blocking. If the future has not yet completed
        it will raise an error.
        """
        try:
            return self.__future.exception(timeout=0)
        except TimeoutError:
            raise RuntimeError("Future is not yet done")

    def __emitSignals(self):
        assert self.__future is not None
        assert self.__future.done()
        if self.__future.cancelled():
            self.cancelled.emit(self.__future)
            self.done.emit(self.__future)
        elif self.__future.done():
            self.finished.emit(self.__future)
            self.done.emit(self.__future)
            if self.__future.exception():
                self.exceptionReady.emit(self.__future.exception())
            else:
                self.resultReady.emit(self.__future.result())
        else:
            assert False

    def customEvent(self, event):
        # Reimplemented.
        if event.type() == FutureWatcher.__FutureDone:
            self.__emitSignals()
        super(FutureWatcher, self).customEvent(event)
