"""
Main Window
-----------

`MainWindow` is the primary main workflow editor/view.

"""
import os
import sys
import logging
import operator
import io
import errno
from functools import partial

import pkg_resources

import six

from AnyQt.QtWidgets import (
    QMainWindow, QWidget, QAction, QDialog, QVBoxLayout, QSizePolicy,
    QToolBar, QToolButton, QDockWidget, QApplication,
)

from AnyQt.QtGui import QColor, QKeySequence, QIcon, QDesktopServices
from AnyQt.QtCore import (
    Qt, QObject, QEvent, QSize, QUrl, QTimer, QFile, QByteArray
)

from AnyQt.QtNetwork import QNetworkDiskCache

# from AnyQt.QtWebKit import QWebView

from AnyQt.QtCore import pyqtProperty as Property, pyqtSignal as Signal

from .. import scheme
from ..scheme import readwrite

# Compatibility with PyQt < v4.8.3
from ..utils.qtcompat import QSettings, qunwrap

from ..gui.dropshadow import DropShadowFrame
from ..gui.dock import CollapsibleDockWidget
from ..gui.quickhelp import QuickHelpTipEvent
from ..gui.utils import (
    message_critical, message_question, message_warning, message_information
)

from ..help import HelpManager

from .canvastooldock import CanvasToolDock, QuickCategoryToolbar, \
                            CategoryPopupMenu, popup_position_from_source

from .schemeinfo import SchemeInfoDialog
from .outputview import OutputView
from .settings import UserSettingsDialog
from ..document.schemeedit import SchemeEditWidget
from ..document.quickmenu import SortFilterProxyModel


from ..preview import previewdialog, previewmodel

from .. import config

from .application import Document, DocumentController, file_format

log = logging.getLogger(__name__)

# Actions
#
#  File/
#    New, Open, Reload, Recent, ... [GLOBAL, defined by Document Controler]
#  Edit/
#    Undo, Redo, SelectAll [defined by Document, proxies by Document controler for menu bar]
#    Import, Paste, Copy,
#  View/
#    Expand Dock, Show margins [defined by Document proxied by Document controler for menu bar]
#  Widget/
#    Open, Remove, Rename, Help [defined by document]
#  Help/
#    Defined by application

# Action Flags
#  ApplicationAction | The action applies to the whole application
#                      and does no require an associated document
#                      (e.g. Add-Ons/Extensions/Preferences)
#  DocumentAction    | The action requires an associated (and active) document
#


class SchemeDocument(Document):
    def __init__(self, parent=None, **kwargs):
        super(SchemeDocument, self).__init__(parent, **kwargs)
        #: The MainWindow associated with the open workflow.
        self.__widget = None
        self.__workflow = None
        self.__defaultRegistry = None

    def documentTypes(self):
        """Return the supported document types.
        """
        return [file_format("Orange Workflow", None, "ows")]

    def setDefaultRegistry(self, registry):
        self.__defaultRegistry = registry

    def _createWidget(self, parent=None):
        """
        Create the widget for display/editing.
        """
        window = MainWindow(parent=parent)
#         window.set_widget_registry(self.widget_registry)
#         window.setStyleSheet(self.styleSheet())
        return window

    def widget(self):
        """
        Return the CanvasMainWindow for display/editing.

        If the widget does not already exist it will be created.
        """
        if self.__widget is None:
            self.__widget = self._createWidget()
            self.__widget.installEventFilter(self)
        return self.__widget

    def open(self, url):
        workflow = config.workflow_constructor()
        if self.__default_registry is not None:
            reg = self.__default_registry
        else:
            reg = readwrite.global_registry()

        with open(url, "rb") as f:
            readwrite.scheme_load(workflow, f, reg)

        widget = self.widget()

        if self.__default_registry:
            widget.set_widget_source(reg)

        widget.set_scheme(workflow)
        return True

    def writeToPath(self, url, fileformat=None):
        if self.check_can_save(url):
            return self.save_scheme_to(url)
        else:
            return False

    def _checkCanSave(self, path):
        """
        Check if saving the document to `path` would prevent it from
        being read by the version 1.0 of scheme parser. Return ``True``
        if the existing scheme is version 1.0 else show a message box and
        return ``False``

        .. note::
            In case of an error (opening, parsing), this method will return
            ``True``, so the

        """
        if path and os.path.exists(path):
            try:
                version = readwrite.sniff_version(open(path, "rb"))
            except (IOError, OSError):
                log.error("Error opening '%s'", path, exc_info=True)
                # The client should fail attempting to write.
                return True
            except Exception:
                log.error("Error sniffing scheme version in '%s'", path,
                          exc_info=True)
                # Malformed .ows file, ...
                return True

            if version == "1.0":
                # TODO: Ask for overwrite confirmation instead
                message_information(
                    self.tr("Can not overwrite a version 1.0 ows file. "
                            "Please save your work to a new file"),
                    title="Info",
                    parent=self.widget())
                return False
        return True

    def save_to(self, filename):
        widget = self.widget()
        scheme = self.widget().scheme()
        dirname, basename = os.path.split(filename)
        self.last_scheme_dir = dirname
        title = scheme.title or "untitled"

        contents = io.BytesIO()
        try:
            scheme.save_to(contents, pretty=True, pickle_fallback=True)
        except Exception:
            log.error("Error saving %r to %r", scheme, filename, exc_info=True)
            message_critical(
                self.tr('An error occurred while trying to save workflow '
                        '"%s" to "%s"') % (title, basename),
                title=self.tr("Error saving %s") % basename,
                exc_info=True,
                parent=self
            )
            return False

        try:
            with open(filename, "wb") as f:
                f.write(contents.getvalue())
            return True
        except (IOError, OSError) as ex:
            log.error("%s saving '%s'", type(ex).__name__, filename,
                      exc_info=True)
            if ex.errno == errno.ENOENT:
                # user might enter a string containing a path separator
                message_warning(
                    self.tr('Workflow "%s" could not be saved. The path does '
                            'not exist') % title,
                    title="",
                    informative_text=self.tr("Choose another location."),
                    parent=widget
                )
            elif ex.errno == errno.EACCES:
                message_warning(
                    self.tr('Workflow "%s" could not be saved. You do not '
                            'have write permissions.') % title,
                    title="",
                    informative_text=self.tr(
                        "Change the file system permissions or choose "
                        "another location."),
                    parent=widget
                )
            else:
                message_warning(
                    self.tr('Workflow "%s" could not be saved.') % title,
                    title="",
                    informative_text=ex.strerror,
                    exc_info=True,
                    parent=widget
                )
            return False

        except Exception:
            log.error("Error saving %r to %r", scheme, filename, exc_info=True)
            message_critical(
                self.tr('An error occurred while trying to save workflow '
                        '"%s" to "%s"') % (title, basename),
                title=self.tr("Error saving %s") % basename,
                exc_info=True,
                parent=widget
            )
            return False

    def isModified(self):
        window = self.widget()

        if window is not None:
            modified = window.isWindowModified()
        else:
            modified = False
        return modified or super(SchemeDocument, self).isModified()

    def tr(self, sourceText, disambiguation=None, n=-1):
        """
        Translate the `sourceText` string.
        """
        return six.text_type(Document.tr(self, sourceText, disambiguation, n))


class CanvasController(DocumentController):

    def __init__(self, parent=None):
        super(CanvasController, self).__init__(parent)
        self.__action_open_and_freeze = QAction(
            self.tr("Open and Freeze"), self,
            objectName="action-open-and-freeze",
            triggered=self.open_and_freeze
        )

        self.__action_show_properties = \
            QAction(self.tr("Workflow Info"), self,
                    objectName="show-properties-action",
                    toolTip=self.tr("Show workflow properties."),
                    triggered=self.show_scheme_properties,
                    shortcut=QKeySequence(Qt.ControlModifier | Qt.Key_I),
                    icon=canvas_icons("Document Info.svg")
                    )

        self.set_document_types([file_format("Orange Workflow", None, "ows")])
        self.set_default_document_type(SchemeDocument)

        self.__registry = None

        if CanvasController.__instance is None:
            CanvasController.__instance = self

    __instance = None

    @classmethod
    def instance(cls):
        if not cls.__instance:
            cls.__instance = CanvasController()
        return cls.__instance

    def document_class_for_url(self, url):
        return SchemeDocument

    def default_document_type(self):
        return SchemeDocument

    def open_and_freeze_action(self):
        return self.__open_and_freeze_action

    def show_properties_action(self):
        return self.__show_properties_action

    def new(self):
        klass = self.default_document_class()
        doc = klass(self)

        registry = self.__registry
        doc.set_default_registry(registry)

        docwidget = doc.widget()
        docwidget.show()
        docwidget.raise_()
        docwidget.activateWindow()

        if self.__show_properties_at_new:
            dlg = docwidget.show_properties_dialog()

            status = dlg.exec_()
            # TODO: Update show properties at new.
            self.__show_properties_at_new = dlg

    def open_and_freeze(self):
        filename, filetype = self.run_open_file_dialog()

        if not filename:
            return
        current = self.scheme_widget

        if current.is_transient():
            current.pause_action.setChecked(True)
            return current.open(filename)
        else:
            klass = self.document_class_for_url(filename)
            doc = klass(self)
            doc.pause_action.setChecked(True)
            if doc.open(filename):
                self.add_document(doc, filename)
                return True
            else:
                return False

    def show_scheme_properties(self):
        doc = self.scheme_widget
        if doc is None:
            return

        doc.show_scheme_properties()

    def browse_recent(self):
        """
        Browse recent workflows.

        Return `QDialog.Rejected` if the user canceled the operation
        and `QDialog.Accepted` otherwise.
        """
        dialog = previewdialog.PreviewDialog()
        dialog.setAttribute(Qt.WA_DeleteOnClose)

        title = self.tr("Recent Workflows")
        dialog.setWindowTitle(title)
        template = ('<h3 style="font-size: 26px">\n'
                    #'<img height="26" src="canvas_icons:Recent.svg">\n'
                    '{0}\n'
                    '</h3>')
        dialog.setHeading(template.format(title))

        recent = self.recent_documents()
        items = [previewmodel.PreviewItem(name=title, path=path)
                 for title, path in recent]
        model = previewmodel.PreviewModel(parent=dialog, items=items)

        dialog.setModel(model)
        # start the preview thumbnail scan.
        model.delayedScanUpdate()

        status = dialog.exec_()
        if status == QDialog.Accepted:
            index = dialog.currentIndex()
            selected = model.item(index)
            filename = six.text_type(selected.path())
            # TODO: Restore window state for the path.
            self.open_document(filename)
            window = self.documents()[0]

            window.show()
            window.raise_()
            window.activateWindow()


def style_icons(widget, standard_pixmap):
    """Return the Qt standard pixmap icon.
    """
    return QIcon(widget.style().standardPixmap(standard_pixmap))


def canvas_icons(name):
    """Return the named canvas icon.
    """
    icon_file = QFile("canvas_icons:" + name)
    if icon_file.exists():
        return QIcon("canvas_icons:" + name)
    else:
        filename = pkg_resources.resource_filename(
            config.__name__, os.path.join("icons", name))
        return QIcon(filename)


class FakeToolBar(QToolBar):
    """
    A QToolbar with no contents.

    (used to reserve top and bottom margins on the main window).

    """
    def __init__(self, *args, **kwargs):
        QToolBar.__init__(self, *args, **kwargs)
        self.setFloatable(False)
        self.setMovable(False)

        # Don't show the tool bar action in the main window's
        # context menu.
        self.toggleViewAction().setVisible(False)

    def paintEvent(self, event):
        # Do nothing.
        pass


class DockableWindow(QDockWidget):
    def __init__(self, *args, **kwargs):
        QDockWidget.__init__(self, *args, **kwargs)
        # Fist show after floating
        self.__firstShow = True
        # Flags to use while floating
        self.__windowFlags = Qt.Window
        self.setWindowFlags(self.__windowFlags)
        self.topLevelChanged.connect(self.__on_topLevelChanged)
        self.visibilityChanged.connect(self.__on_visbilityChanged)

        self.__closeAction = QAction(
            self.tr("Close"), self, shortcut=QKeySequence.Close,
            enabled=self.isFloating(), triggered=self.close
        )
        self.topLevelChanged.connect(self.__closeAction.setEnabled)
        self.addAction(self.__closeAction)

    def setFloatingWindowFlags(self, flags):
        """
        Set `windowFlags` to use while the widget is floating (undocked).
        """
        if self.__windowFlags != flags:
            self.__windowFlags = flags
            if self.isFloating():
                self.__fixWindowFlags()

    def floatingWindowFlags(self):
        """
        Return the `windowFlags` used when the widget is floating.
        """
        return self.__windowFlags

    def __fixWindowFlags(self):
        if self.isFloating():
            update_window_flags(self, self.__windowFlags)

    def __on_topLevelChanged(self, floating):
        if floating:
            self.__firstShow = True
            self.__fixWindowFlags()

    def __on_visbilityChanged(self, visible):
        if visible and self.isFloating() and self.__firstShow:
            self.__firstShow = False
            self.__fixWindowFlags()


def update_window_flags(widget, flags):
    currflags = widget.windowFlags()
    if int(flags) != int(currflags):
        hidden = widget.isHidden()
        widget.setWindowFlags(flags)
        # setting the flags hides the widget
        if not hidden:
            widget.show()


class MainWindow(QMainWindow):

    def __init__(self, parent=None, **kwargs):
        QMainWindow.__init__(self, parent, **kwargs)

        self.__scheme_margins_enabled = True
        self.__documentTitle = None
        self.__first_show = True

        #: Widget source model (widget registry)
        self.__widget_source = None
        # Proxy widget registry model
        self.__proxy_model = None
        # open help urls in an external browser
        self.__open_in_external = False
        self.help = HelpManager(self)

        self._setup_actions()
        self._setup_ui()

    def _setup_ui(self):
        """
        Setup main window user interface.
        """

        # Two dummy tool bars to reserve space
        self.__dummy_top_toolbar = FakeToolBar(
            objectName="__dummy_top_toolbar")
        self.__dummy_bottom_toolbar = FakeToolBar(
            objectName="__dummy_bottom_toolbar")

        self.__dummy_top_toolbar.setFixedHeight(20)
        self.__dummy_bottom_toolbar.setFixedHeight(20)

        # TODO: Can this be replaced by self.setContentsMargins(0, 20, 0, 20)
        self.addToolBar(Qt.TopToolBarArea, self.__dummy_top_toolbar)
        self.addToolBar(Qt.BottomToolBarArea, self.__dummy_bottom_toolbar)

        self.setCorner(Qt.BottomLeftCorner, Qt.LeftDockWidgetArea)
        self.setCorner(Qt.BottomRightCorner, Qt.RightDockWidgetArea)

        self.setDockOptions(QMainWindow.AnimatedDocks)
        # Create an empty initial scheme inside a container with fixed
        # margins.
        w = QWidget()
        w.setLayout(QVBoxLayout())
        w.layout().setContentsMargins(20, 0, 10, 0)

        self.scheme_widget = SchemeEditWidget()
        self.scheme_widget.setScheme(scheme.Scheme(parent=self))

        # Intercept file/url drops on the workflow view (i.e. drag/drop a
        # .ows file on the view should load it).
        # TODO: This should be moved to Document/Controller
#         dropfilter = UrlDropEventFilter(self)
#         dropfilter.urlDropped.connect(self.open_scheme_file)
#         self.scheme_widget.setAcceptDrops(True)
#         self.scheme_widget.installEventFilter(dropfilter)

        w.layout().addWidget(self.scheme_widget)

        self.setCentralWidget(w)

        # Drop shadow around the scheme document
        frame = DropShadowFrame(radius=15)
        frame.setColor(QColor(0, 0, 0, 100))
        frame.setWidget(self.scheme_widget)

        # Main window title and title icon.
        self.setDocumentTitle(self.scheme_widget.scheme().title)
        self.scheme_widget.titleChanged.connect(self.setDocumentTitle)
        self.scheme_widget.modificationChanged.connect(self.setWindowModified)

        # MainWindow's Dock widget containing the source widget toolbox and
        # actions
        self.dock_widget = CollapsibleDockWidget(objectName="main-area-dock")
        self.dock_widget.setFeatures(QDockWidget.DockWidgetMovable |
                                     QDockWidget.DockWidgetClosable)

        self.dock_widget.setAllowedAreas(Qt.LeftDockWidgetArea |
                                         Qt.RightDockWidgetArea)

        # Main canvas tool dock (with widget toolbox, common actions.
        # This is the widget that is shown when the dock is expanded.
        canvas_tool_dock = CanvasToolDock(objectName="canvas-tool-dock")
        canvas_tool_dock.setSizePolicy(QSizePolicy.Fixed,
                                       QSizePolicy.MinimumExpanding)

        # Bottom tool bar
        self.canvas_toolbar = canvas_tool_dock.toolbar
        self.canvas_toolbar.setIconSize(QSize(25, 25))
        self.canvas_toolbar.setFixedHeight(28)
        self.canvas_toolbar.layout().setSpacing(1)

        # Widgets tool box
        self.widgets_tool_box = canvas_tool_dock.toolbox
        self.widgets_tool_box.setObjectName("canvas-toolbox")
        self.widgets_tool_box.setTabButtonHeight(30)
        self.widgets_tool_box.setTabIconSize(QSize(26, 26))
        self.widgets_tool_box.setButtonSize(QSize(64, 84))
        self.widgets_tool_box.setIconSize(QSize(48, 48))

        self.widgets_tool_box.triggered.connect(
            self.on_tool_box_widget_activated
        )

        self.dock_help = canvas_tool_dock.help
        self.dock_help.setMaximumHeight(150)
        self.dock_help.document().setDefaultStyleSheet("h3, a {color: orange;}")

        self.dock_help_action = canvas_tool_dock.toogleQuickHelpAction()
        self.dock_help_action.setText(self.tr("Show Help"))
        self.dock_help_action.setIcon(canvas_icons("Info.svg"))

        self.canvas_tool_dock = canvas_tool_dock

        # Dock contents when collapsed (a quick category tool bar, ...)
        dock2 = QWidget(objectName="canvas-quick-dock")
        dock2.setLayout(QVBoxLayout())
        dock2.layout().setContentsMargins(0, 0, 0, 0)
        dock2.layout().setSpacing(0)
        dock2.layout().setSizeConstraint(QVBoxLayout.SetFixedSize)

        self.quick_category = QuickCategoryToolbar()
        self.quick_category.setButtonSize(QSize(38, 30))
        self.quick_category.actionTriggered.connect(
            self.on_quick_category_action
        )

        tool_actions = self.scheme_widget.toolbarActions()

        (self.canvas_zoom_action, self.canvas_align_to_grid_action,
         self.canvas_text_action, self.canvas_arrow_action,) = tool_actions

        self.canvas_zoom_action.setIcon(canvas_icons("Search.svg"))
        self.canvas_align_to_grid_action.setIcon(canvas_icons("Grid.svg"))
        self.canvas_text_action.setIcon(canvas_icons("Text Size.svg"))
        self.canvas_arrow_action.setIcon(canvas_icons("Arrow.svg"))

        dock_actions = [self.show_properties_action] + \
                       tool_actions + \
                       [self.freeze_action,
                        self.dock_help_action]

        # Tool bar in the collapsed dock state (has the same actions as
        # the tool bar in the CanvasToolDock
        actions_toolbar = QToolBar(orientation=Qt.Vertical)
        actions_toolbar.setFixedWidth(38)
        actions_toolbar.layout().setSpacing(0)

        actions_toolbar.setToolButtonStyle(Qt.ToolButtonIconOnly)

        for action in dock_actions:
            self.canvas_toolbar.addAction(action)
            button = self.canvas_toolbar.widgetForAction(action)
            button.setPopupMode(QToolButton.DelayedPopup)

            actions_toolbar.addAction(action)
            button = actions_toolbar.widgetForAction(action)
            button.setFixedSize(38, 30)
            button.setPopupMode(QToolButton.DelayedPopup)

        dock2.layout().addWidget(self.quick_category)
        dock2.layout().addWidget(actions_toolbar)

        self.dock_widget.setAnimationEnabled(False)
        self.dock_widget.setExpandedWidget(self.canvas_tool_dock)
        self.dock_widget.setCollapsedWidget(dock2)
        self.dock_widget.setExpanded(True)
        self.dock_widget.expandedChanged.connect(self._on_tool_dock_expanded)

        self.addDockWidget(Qt.LeftDockWidgetArea, self.dock_widget)
        self.dock_widget.dockLocationChanged.connect(
            self._on_dock_location_changed
        )

        self.output_dock = DockableWindow(self.tr("Output"), self,
                                          objectName="output-dock")
        self.output_dock.setAllowedAreas(Qt.BottomDockWidgetArea)
        output_view = OutputView()
        # Set widget before calling addDockWidget, otherwise the dock
        # does not resize properly on first undock
        self.output_dock.setWidget(output_view)
        self.output_dock.hide()

        self.help_dock = DockableWindow(self.tr("Help"), self,
                                        objectName="help-dock")
        self.help_dock.setAllowedAreas(Qt.NoDockWidgetArea)
        # self.help_view = QWebView()
        # manager = self.help_view.page().networkAccessManager()
        # TODO: The disk caches cannot share the same directory -> Share
        # a global QNetworkDiskCache instance. Better: help window is
        # global/shared.
        # cache = QNetworkDiskCache()
        # cache.setCacheDirectory(
        #     os.path.join(config.cache_dir(), "help", "help-view-cache")
        # )
        # manager.setCache(cache)
        self.help_dock.setWidget(self.help_view)
        self.help_dock.hide()

        self.setMinimumSize(600, 500)

    def _setup_actions(self):
        """
        Initialize main window actions.
        """
        self.close_action = QAction(
            self.tr("Close"), self, objectName="action-close",
            shortcut=QKeySequence.Close,
            triggered=self.close
        )
        self.show_properties_action = QAction(
            self.tr("Workflow Info"), self,
            objectName="action-show-properties",
            toolTip=self.tr("Show workflow properties."),
            triggered=self.show_scheme_properties,
            shortcut=QKeySequence(Qt.ControlModifier | Qt.Key_I),
            icon=canvas_icons("Document Info.svg")
        )

        self.show_output_action = QAction(
            self.tr("Show Output View"), self,
            toolTip=self.tr("Show application output."),
            triggered=self.show_output_view,
        )

        self.freeze_action = QAction(
            self.tr("Freeze"), self,
            objectName="action-signal-freeze",
            checkable=True,
            toolTip=self.tr("Freeze signal propagation."),
            triggered=self.set_signal_freeze,
            icon=canvas_icons("Pause.svg")
        )

        self.toggle_tool_dock_expand = QAction(
            self.tr("Expand Tool Dock"), self,
            objectName="action-toggle-tool-dock-expand",
            checkable=True,
            checked=True,
            shortcut=QKeySequence(Qt.ControlModifier |
                                  (Qt.ShiftModifier | Qt.Key_D)),
            triggered=self.set_tool_dock_expanded
        )

        # Gets assigned in setup_ui (the action is defined in CanvasToolDock)
        # TODO: This is bad (should be moved here).
        self.dock_help_action = None

        self.toogle_margins_action = QAction(
            self.tr("Show Workflow Margins"), self,
            checkable=True,
            checked=True,
            toolTip=self.tr("Show margins around the workflow view."),
            toggled=self.set_scheme_margins_enabled
        )

    def saveState(self):
        return {
            "__version__": 1,
            "tooldock": {
                "expanded": self.canvas_tool_dock.isExpanded(),
                "quick-help-visible": self.canvas_tool_dock.isQuickHelpVisible(),
                "toolbox-exclusive": self.widgets_tool_box.isExclusive(),
                "floatable": bool(self.dock_widget.features() &
                                  QDockWidget.DockWidgetFloatable),
            },
            "editor": {
                "quick-menu-triggers": self.scheme_widget.quickMenuTriggers(),
                "channel-names-visible": self.scheme_widget.channelNamesVisible(),
                "node-animations-enabled": self.scheme_widget.nodeAnimationEnabled(),
            },
#                 toolbox-dock-use-popover-menu
            "margins-enabled": self.toogle_margins_action.isChecked(),
        }

    def restoreState(self, state):
        version = state["__version__"]
        if version != 1:
            return False
        dockstate = state["tooldock"]

        floatable = bool(dockstate["floatable"])
        expanded = bool(dockstate["expanded"])
        exclusive = bool(dockstate["toolbox-exclusive"])
        helpvisible = bool(dockstate["quick-help-visible"])
        showmargins = bool(state["margins-enabled"])

        self.dock_widget.setExpanded(expanded)
        features = self.dock_widget.features()
        if floatable:
            features = features | QDockWidget.DockWidgetFloatable
        else:
            features = features & ~QDockWidget.DockWidgetFloatable

        self.dock_widget.setFeatures(features)
        self.widgets_tool_box.setExclusive(exclusive)
        self.toogle_margins_action.setChecked(showmargins)
        self.canvas_tool_dock.setQuickHelpVisible(helpvisible)

    def setDocumentTitle(self, title):
        """Set the document title (and the main window title). If `title`
        is an empty string a default 'untitled' placeholder will be used.

        """
        if self.__documentTitle != title:
            self.__documentTitle = title

            if not title:
                # TODO: should the default name be platform specific
                title = self.tr("untitled")

            self.setWindowTitle(title + "[*]")

    def documentTitle(self):
        """Return the document title.
        """
        return self.__documentTitle

    def setWidgetSourceModel(self, registry):
        """Set the source widget model.
        """
        if self.__widget_source is not None:
            self.widgets_tool_box.setModel(None)
            self.quick_category.setModel(None)
            self.scheme_widget.setRegistry(None)
            self.help.set_registry(None)
            self.__proxy_model.deleteLater()
            self.__proxy_model = None

        self.__widget_source = registry

        proxy = SortFilterProxyModel(self)
        proxy.setSourceModel(registry.model())
        self.__proxy_model = proxy

#         self.__update_registry_filters()

        self.widgets_tool_box.setModel(proxy)
        self.quick_category.setModel(proxy)

        self.scheme_widget.setRegistry(registry)
        self.scheme_widget.quickMenu().setModel(proxy)

        self.help.set_registry(registry)

        # Restore possibly saved widget toolbox tab states
        settings = QSettings()
        state = settings.value("mainwindow/widgettoolbox/state",
                                defaultValue=QByteArray(),
                                type=QByteArray)
        if state:
            self.widgets_tool_box.restoreState(state)

    def current_document(self):
        return self.scheme_widget

    def on_tool_box_widget_activated(self, action):
        """A widget action in the widget toolbox has been activated.
        """
        widget_desc = qunwrap(action.data())
        if widget_desc:
            scheme_widget = self.scheme_widget
            if scheme_widget:
                scheme_widget.createNewNode(widget_desc)

    def on_quick_category_action(self, action):
        """The quick category menu action triggered.
        """
        category = action.text()
        # Show a popup menu with the widgets in the category
        popup = CategoryPopupMenu(self.quick_category)
        reg = self.widget_registry.model()
        i = index(self.widget_registry.categories(), category,
                  predicate=lambda name, cat: cat.name == name)
        if i != -1:
            popup.setCategoryItem(reg.item(i))
            button = self.quick_category.buttonForAction(action)
            pos = popup_position_from_source(popup, button)
            action = popup.exec_(pos)
            if action is not None:
                self.on_tool_box_widget_activated(action)

    def set_scheme_margins_enabled(self, enabled):
        """Enable/disable the margins around the scheme document.
        """
        if self.__scheme_margins_enabled != enabled:
            self.__scheme_margins_enabled = enabled
            self.__update_scheme_margins()

    def scheme_margins_enabled(self):
        return self.__scheme_margins_enabled

    scheme_margins_enabled = Property(bool,
                                      fget=scheme_margins_enabled,
                                      fset=set_scheme_margins_enabled)

    def __update_scheme_margins(self):
        """Update the margins around the scheme document.
        """
        enabled = self.__scheme_margins_enabled
        self.__dummy_top_toolbar.setVisible(enabled)
        self.__dummy_bottom_toolbar.setVisible(enabled)
        central = self.centralWidget()

        margin = 20 if enabled else 0

        if self.dockWidgetArea(self.dock_widget) == Qt.LeftDockWidgetArea:
            margins = (margin / 2, 0, margin, 0)
        else:
            margins = (margin, 0, margin / 2, 0)

        central.layout().setContentsMargins(*margins)

    #################
    # Action handlers
    #################

#     def set_scheme(self, scheme):
    def setWorkflowModel(self, workflow):
        """
        Set `workflow` instance as the current displayed workflow.

        Ownership of the old workflow (if it exists is transfered to
        the caller).

        Parameters
        ----------
        workflow : Scheme
        """
        editor = self.scheme_widget

        manager = getattr(scheme, "signal_manager", None)
        if self.freeze_action.isChecked() and manager is not None:
            manager.pause()

        editor.setScheme(scheme)

    def scheme_properties_dialog(self):
        """Return an empty `SchemeInfo` dialog instance.
        """
        settings = QSettings()
        value_key = "schemeinfo/show-at-new-scheme"

        dialog = SchemeInfoDialog(self)

        dialog.setWindowTitle(self.tr("Workflow Info"))
        dialog.setFixedSize(725, 450)

        dialog.setShowAtNewScheme(
            settings.value(value_key, True, type=bool)
        )

        return dialog

    def show_scheme_properties(self):
        """
        Show current scheme properties.
        """
        settings = QSettings()
        value_key = "schemeinfo/show-at-new-scheme"

        current_doc = self.scheme_widget
        scheme = current_doc.scheme()
        dlg = self.scheme_properties_dialog()
        dlg.setAutoCommit(False)
        dlg.setScheme(scheme)
        status = dlg.exec_()

        if status == QDialog.Accepted:
            editor = dlg.editor
            stack = current_doc.undoStack()
            stack.beginMacro(self.tr("Change Info"))
            current_doc.setTitle(editor.title())
            current_doc.setDescription(editor.description())
            stack.endMacro()

            # Store the check state.
            settings.setValue(value_key, dlg.showAtNewScheme())
        return status

    def set_signal_freeze(self, freeze):
        scheme = self.scheme_widget.scheme()
        manager = getattr(scheme, "signal_manager", None)
        if manager is not None:
            if freeze:
                manager.pause()
            else:
                manager.resume()

    def open_canvas_settings(self):
        """Open canvas settings/preferences dialog
        """
        dlg = UserSettingsDialog(self)
        dlg.setWindowTitle(self.tr("Preferences"))
        dlg.show()
        status = dlg.exec_()
        if status == 0:
            self.__update_from_settings()

    def show_output_view(self):
        """Show a window with application output.
        """
        self.output_dock.show()

    def output_view(self):
        """Return the output text widget.
        """
        return self.output_dock.widget()

    def _on_dock_location_changed(self, location):
        """Location of the dock_widget has changed, fix the margins
        if necessary.

        """
        self.__update_scheme_margins()

    def set_tool_dock_expanded(self, expanded):
        """
        Set the dock widget expanded state.
        """
        self.dock_widget.setExpanded(expanded)

    def _on_tool_dock_expanded(self, expanded):
        """
        'dock_widget' widget was expanded/collapsed.
        """
        if expanded != self.toggle_tool_dock_expand.isChecked():
            self.toggle_tool_dock_expand.setChecked(expanded)

    def createPopupMenu(self):
        # Override the default context menu popup (we don't want the user to
        # be able to hide the tool dock widget).
        # # WHY?
        return None

    def event(self, event):
        if event.type() == QEvent.StatusTip and \
                isinstance(event, QuickHelpTipEvent):
            # Using singleShot to update the text browser.
            # If updating directly the application experiences strange random
            # segfaults (in ~StatusTipEvent in QTextLayout or event just normal
            # event loop), but only when the contents are larger then the
            # QTextBrowser's viewport.
            if event.priority() == QuickHelpTipEvent.Normal:
                QTimer.singleShot(0, partial(self.dock_help.showHelp,
                                             event.html()))
            elif event.priority() == QuickHelpTipEvent.Temporary:
                QTimer.singleShot(0, partial(self.dock_help.showHelp,
                                             event.html(), event.timeout()))
            elif event.priority() == QuickHelpTipEvent.Permanent:
                QTimer.singleShot(0, partial(self.dock_help.showPermanentHelp,
                                             event.html()))

            return True

        elif event.type() == QEvent.WhatsThisClicked:
            ref = event.href()
            url = QUrl(ref)

            if url.scheme() == "help" and url.authority() == "search":
                try:
                    url = self.help.search(url)
                except KeyError:
                    url = None
                    log.info("No help topic found for %r", url)

            if url:
                self.show_help(url)
            else:
                message_information(
                    self.tr("Sorry there is no documentation available for "
                            "this widget."),
                    parent=self)

            return True

        return QMainWindow.event(self, event)

    def show_help(self, url):
        """
        Show `url` in a help window.
        """
        log.info("Setting help to url: %r", url)
        if self.__open_in_external:
            url = QUrl(url)
            if not QDesktopServices.openUrl(url):
                # Try fixing some common problems.
                url = QUrl.fromUserInput(url.toString())
                # 'fromUserInput' includes possible fragment into the path
                # (which prevents it to open local files) so we reparse it
                # again.
                url = QUrl(url.toString())
                QDesktopServices.openUrl(url)
        else:
            self.help_view.load(QUrl(url))
            self.help_dock.show()
            self.help_dock.raise_()

    # Mac OS X
    if sys.platform == "darwin":
        def toggleMaximized(self):
            """Toggle normal/maximized window state.
            """
            if self.isMinimized():
                # Do nothing if window is minimized
                return

            if self.isMaximized():
                self.showNormal()
            else:
                self.showMaximized()

        def changeEvent(self, event):
            if event.type() == QEvent.WindowStateChange:
                # Can get 'Qt.WindowNoState' before the widget is fully
                # initialized
                if hasattr(self, "window_state"):
                    # Enable/disable window menu based on minimized state
                    self.window_menu.setEnabled(not self.isMinimized())

            QMainWindow.changeEvent(self, event)

    def sizeHint(self):
        """
        Reimplemented from QMainWindow.sizeHint
        """
        hint = QMainWindow.sizeHint(self)
        return hint.expandedTo(QSize(1024, 720))

    def tr(self, sourceText, disambiguation=None, n=-1):
        """
        Translate the string.
        """
        return six.text_type(QMainWindow.tr(self, sourceText, disambiguation, n))

    def __update_from_settings(self):
        settings = QSettings()
        settings.beginGroup("mainwindow")
        toolbox_floatable = settings.value("toolbox-dock-floatable",
                                           defaultValue=False,
                                           type=bool)

        features = self.dock_widget.features()
        features = updated_flags(features, QDockWidget.DockWidgetFloatable,
                                 toolbox_floatable)
        self.dock_widget.setFeatures(features)

        toolbox_exclusive = settings.value("toolbox-dock-exclusive",
                                           defaultValue=True,
                                           type=bool)
        self.widgets_tool_box.setExclusive(toolbox_exclusive)

        self.num_recent_schemes = settings.value("num-recent-schemes",
                                                 defaultValue=15,
                                                 type=int)

        settings.endGroup()
        settings.beginGroup("quickmenu")

        triggers = 0
        dbl_click = settings.value("trigger-on-double-click",
                                   defaultValue=True,
                                   type=bool)
        if dbl_click:
            triggers |= SchemeEditWidget.DoubleClicked

        right_click = settings.value("trigger-on-right-click",
                                    defaultValue=True,
                                    type=bool)
        if right_click:
            triggers |= SchemeEditWidget.RightClicked

        space_press = settings.value("trigger-on-space-key",
                                     defaultValue=True,
                                     type=bool)
        if space_press:
            triggers |= SchemeEditWidget.SpaceKey

        any_press = settings.value("trigger-on-any-key",
                                   defaultValue=False,
                                   type=bool)
        if any_press:
            triggers |= SchemeEditWidget.AnyKey

        self.scheme_widget.setQuickMenuTriggers(triggers)

        settings.endGroup()
        settings.beginGroup("schemeedit")
        show_channel_names = settings.value("show-channel-names",
                                            defaultValue=True,
                                            type=bool)
        self.scheme_widget.setChannelNamesVisible(show_channel_names)

        node_animations = settings.value("enable-node-animations",
                                         defaultValue=False,
                                         type=bool)
        self.scheme_widget.setNodeAnimationEnabled(node_animations)
        settings.endGroup()

        settings.beginGroup("output")
        stay_on_top = settings.value("stay-on-top", defaultValue=True,
                                     type=bool)
        if stay_on_top:
            self.output_dock.setFloatingWindowFlags(Qt.Tool)
        else:
            self.output_dock.setFloatingWindowFlags(Qt.Window)

        dockable = settings.value("dockable", defaultValue=True,
                                  type=bool)
        if dockable:
            self.output_dock.setAllowedAreas(Qt.BottomDockWidgetArea)
        else:
            self.output_dock.setAllowedAreas(Qt.NoDockWidgetArea)

        settings.endGroup()

        settings.beginGroup("help")
        stay_on_top = settings.value("stay-on-top", defaultValue=True,
                                     type=bool)
        if stay_on_top:
            self.help_dock.setFloatingWindowFlags(Qt.Tool)
        else:
            self.help_dock.setFloatingWindowFlags(Qt.Window)

        dockable = settings.value("dockable", defaultValue=False,
                                  type=bool)
        if dockable:
            self.help_dock.setAllowedAreas(Qt.LeftDockWidgetArea | \
                                           Qt.RightDockWidgetArea)
        else:
            self.help_dock.setAllowedAreas(Qt.NoDockWidgetArea)

        self.__open_in_external = \
            settings.value("open-in-external-browser", defaultValue=False,
                           type=bool)


def updated_flags(flags, mask, state):
    if state:
        flags |= mask
    else:
        flags &= ~mask
    return flags


def identity(item):
    return item


def index(sequence, *what, **kwargs):
    """index(sequence, what, [key=None, [predicate=None]])

    Return index of `what` in `sequence`.

    """
    what = what[0]
    key = kwargs.get("key", identity)
    predicate = kwargs.get("predicate", operator.eq)
    for i, item in enumerate(sequence):
        item_key = key(item)
        if predicate(what, item_key):
            return i
    raise ValueError("%r not in sequence" % what)
