"""
Widgets Scheme
==============

A Scheme for Orange Widgets Scheme (.ows).

This is a subclass of the general :class:`Scheme`. It is responsible for
the construction and management of OWBaseWidget instances corresponding
to the scheme nodes, as well as delegating the signal propagation to a
companion :class:`WidgetsSignalManager` class.

.. autoclass:: WidgetsScheme
   :bases:

.. autoclass:: WidgetsSignalManager
  :bases:

"""
import sys
import logging
import concurrent.futures

from collections import namedtuple

import sip

from PyQt4.QtGui import (
    QShortcut, QKeySequence, QWhatsThisClickedEvent, QWidget
)

from PyQt4.QtCore import Qt, QObject, QCoreApplication, QTimer, QEvent
from PyQt4.QtCore import pyqtSignal as Signal

from .signalmanager import SignalManager, compress_signals, can_enable_dynamic
from .scheme import Scheme, SchemeNode
from .events import WorkflowEvent, NodeEvent

from ..utils import name_lookup
from ..resources import icon_loader

log = logging.getLogger(__name__)


class WidgetManager(QObject):
    """
    GUI widget manager class.

    This class handles the lifetime of GUI widget/window instances for a
    :class:`Scheme`. It also acts as an adapter between the GUI as a
    view/controler and the workflow model (a `Scheme` instance).

    """
    #: A new QWidget was created and added by the manager.
    widget_for_node_added = Signal(SchemeNode, QWidget)

    #: An QWidget was removed, hidden and will be deleted when appropriate.
    widget_for_node_removed = Signal(SchemeNode, QWidget)

    #: Widget initialization states
    Delayed = namedtuple("Delayed", ["node", "future"])
    Materialized = namedtuple("Materialized", ["node", "widget"])

    class WidgetInitEvent(QEvent):
        DelayedInit = QEvent.registerEventType()

        def __init__(self, initstate):
            QEvent.__init__(self, WidgetManager.WidgetInitEvent.DelayedInit)
            self._initstate = initstate

        def initstate(self):
            return self._initstate

    def __init__(self, parent=None):
        QObject.__init__(self, parent)
        self.__scheme = None
        self.__widgets = []
        self.__initstate_for_node = {}
        self.__widget_for_node = {}
        self.__node_for_widget = {}
        # If True then the initialization of the OWWidget instance
        # will be delayed (scheduled to run from the event loop)
        self.__delayed_init = True

    def set_scheme(self, scheme):
        """
        Set the :class:`Scheme` instance to manage.
        """
        if self.__scheme is scheme:
            return

        if self.__scheme is not None:
            self.close()

        self.__scheme = scheme
        if scheme is not None:
            scheme.installEventFilter(self)
            for node in scheme.nodes:
                self.add_widget_for_node(node)

    def scheme(self):
        """
        Return the scheme instance on which this manager is installed.
        """
        return self.__scheme

    def signal_manager(self):
        """
        Return the signal manager in use on the :func:`scheme`.
        """
        if self.__scheme is None:
            return None
        return self.__scheme.findChild(SignalManager)

    def widget_for_node(self, node):
        """
        Return the QWidget instance for the scheme node.
        """
        state = self.__initstate_for_node[node]
        if isinstance(state, WidgetManager.Delayed):
            # Create the widget now if it is still in the event queue.
            state = self.__materialize(state)
            self.__initstate_for_node[node] = state
            return state.widget
        elif isinstance(state, WidgetManager.Materialized):
            return state.widget
        else:
            assert False

    def node_for_widget(self, widget):
        """
        Return the SchemeNode instance for the QWidget.

        Raise a KeyError if the widget does not map to a node in the scheme.
        """
        return self.__node_for_widget[widget]

    def add_widget_for_node(self, node):
        """
        Create a new QWidget instance for the scheme node.
        """
        future = concurrent.futures.Future()
        state = WidgetManager.Delayed(node, future)
        self.__initstate_for_node[node] = state

        event = WidgetManager.WidgetInitEvent(state)
        if self.__delayed_init:
            def schedule_later():
                try:
                    QCoreApplication.postEvent(
                        self, event, Qt.LowEventPriority - 10)
                except RuntimeError:
                    pass

            QTimer.singleShot(int(1000 / 30) + 10, schedule_later)
        else:
            QCoreApplication.sendEvent(self, event)
        node.installEventFilter(self)

    def __materialize(self, state):
        # Initialize an QWidget for a Delayed widget initialization.
        assert isinstance(state, WidgetManager.Delayed)
        node, future = state.node, state.future

        widget = self.create_widget_instance(node)
        # Install a help shortcut on the widget
        help_shortcut = QShortcut(QKeySequence("F1"), widget)
        help_shortcut.activated.connect(self.__on_help_request)

        # Up shortcut (activate/open parent)
        up_shortcut = QShortcut(
            QKeySequence(Qt.ControlModifier + Qt.Key_Up), widget)
        up_shortcut.activated.connect(self.__on_activate_parent)

        self.__widgets.append(widget)
        self.__widget_for_node[node] = widget
        self.__node_for_widget[widget] = node

        self.__initialize_widget_state(node, widget)

        state = WidgetManager.Materialized(node, widget)
        self.__initstate_for_node[node] = state

        future.set_result(widget)
        self.widget_for_node_added.emit(node, widget)

        return state

    def remove_widget_for_node(self, node):
        """
        Remove the QWidget instance for node.
        """
        state = self.__initstate_for_node[node]
        if isinstance(state, WidgetManager.Delayed):
            state.future.cancel()
            del self.__initstate_for_node[node]
        else:
            # emit the signals while the widget is still valid
            self.widget_for_node_removed.emit(node, state.widget)
            self.remove_widget(state.widget)
            self.__widgets.remove(state.widget)
            del self.__initstate_for_node[node]
            del self.__widget_for_node[node]

        node.removeEventFilter(self)

    def remove_widget(self, widget):
        """
        Remove a QWidget instance.

        This method is called from remove widget_for_node and is
        intended for subclasses to override the widget deletion process.

        The default implementation calls ``widget.deleteLater()``
        """
        widget.deleteLater()

    def create_widget_instance(self, node):
        """
        Create a QWidget instance for the node.
        """
        raise NotImplementedError

    def customEvent(self, event):
        if event.type() == WidgetManager.WidgetInitEvent.DelayedInit:
            state = event.initstate()
            node, future = state.node, state.future
            if not (future.cancelled() or future.done()):
                QCoreApplication.flush()
                self.__initstate_for_node[node] = self.__materialize(state)
            event.accept()
        else:
            QObject.customEvent(self, event)

    def eventFilter(self, receiver, event):
        print(receiver, event)
        if event.type() == NodeEvent.NodeActivateRequest and \
               receiver in self.__initstate_for_node:
            widget = self.widget_for_node(receiver)
            widget.show()
            widget.raise_()
            widget.activateWindow()
        elif event.type() == NodeEvent.NodeAdded \
                and receiver is self.__scheme:
            self.add_widget_for_node(event.node())
        elif event.type() == NodeEvent.NodeRemoved \
                and receiver is self.__scheme:
            self.remove_widget_for_node(event.node())

        return QObject.eventFilter(self, receiver, event)

    def close(self):
        """
        Close/remove all managed GUI widgets and dissociate from the scheme.
        """
        if self.__scheme is None:
            return

        # Notify and remove the widget instances.
        for node in self.__scheme.nodes:
            self.remove_widget_for_node(node)

        self.__scheme.removeEventFilter(self)
        self.__scheme = None

    def __on_help_request(self):
        """
        Help shortcut was pressed. We send a `QWhatsThisClickedEvent` to
        the scheme and hope someone responds to it.

        """
        # Sender is the QShortcut, and parent the QWidget
        widget = self.sender().parent()
        try:
            node = self.node_for_widget(widget)
        except KeyError:
            pass
        else:
            url = "help://search?id={0}".format(node.description.id)
            event = QWhatsThisClickedEvent(url)
            QCoreApplication.sendEvent(self.scheme(), event)

    def __on_activate_parent(self):
        """
        Activate parent shortcut was pressed.
        """
        event = WorkflowEvent(WorkflowEvent.ActivateParentRequest)
        QCoreApplication.sendEvent(self.scheme(), event)
