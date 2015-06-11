"""
Orange Canvas Application

"""
import os
import io
import itertools
import operator
import time

from functools import partial
from collections import namedtuple

import six

from AnyQt.QtWidgets import (
    QApplication, QAction, QActionGroup, QMenu, QFileDialog, QMessageBox,
    QPlainTextEdit
)
from AnyQt.QtGui import QKeySequence, QDesktopServices
from AnyQt.QtCore import Qt, QUrl, QEvent, QObject
from AnyQt.QtCore import pyqtSignal as Signal, pyqtSlot as Slot

from ..gui import utils
from ..utils.qtcompat import qunwrap


class CanvasApplication(QApplication):
    fileOpenRequest = Signal(QUrl)

    def __init__(self, argv):
        if hasattr(Qt, "AA_EnableHighDpiScaling"):
            # Turn on HighDPI support when available
            QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
        QApplication.__init__(self, argv)
        self.setAttribute(Qt.AA_DontShowIconsInMenus, True)

    def event(self, event):
        if event.type() == QEvent.FileOpen:
            self.fileOpenRequest.emit(event.url())

        return QApplication.event(self, event)


_Action = namedtuple(
    "Action", ["name", "text", "role", ]
)


class Action(_Action):
    def __new__(cls, name, text, role):
        self = _Action.__new__(cls, name, text, role)
        return self


class ProxyAction(QAction):
    def __init__(self, sourceAction, parent):
        QAction.__init__(self, parent)
        self.__sourceAction = sourceAction  # type: QAction
        self.__sourceAction.installEventFilter(self)
        self.__sourceAction.destroyed.connect(self.__on_destroyed)
        self.__update()

    def eventFilter(self, receiver, event):
        if receiver is self.__sourceAction:
            if event.type() == QEvent.ActionChanged:
                self.__update()

        return QAction.eventFilter(self, receiver, event)

    def __update(self):
        if self.__sourceAction is not None:
            self.setText(self.__sourceAction.text())
            self.setIcon(self.__sourceAction.icon())
            self.setToolTip(self.__sourceAction.toolTip())
            self.setEnabled(self.__sourceAction.isEnabled())
            self.setCheckable(self.__sourceAction.isCheckable())
            self.setChecked(self.__sourceAction.isChecked())
        else:
            pass
    @Slot()
    def __on_destroyed(self, obj):
        self.__sourceAction = None
        self.__update()


class Document(QObject):
    """
    An abstract representation of a open document in an application.

    This class is used by the document controller open, save and keep
    track of documents in an application. It is also responsible for
    the creation/definition of GUI elements associated with a document
    type.
    """

    Type = namedtuple("Type", ["name", "mimetype", "extension"])

    #: Url associated with the document has changed.
    pathChanged = Signal(QUrl)
    #: Document title/name changed
    titleChanged = Signal(six.text_type)

    #: Document meta properties changed
    propertiesChanged = Signal(six.text_type)

    #: Widget for document editing/display has been created.
    viewCreated = Signal()

    def __init__(self, parent=None, **kwargs):
        QObject.__init__(self, parent, **kwargs)

        self.__url = None
        self.__title = ""
        self.__transient = False
        self.__modified = False
        self.__document_controller = None

        self.__action_close = QAction(
            self.tr("Close"), self,
            objectName="action-close",
            triggered=self.close,
            shortcut=QKeySequence.Close
        )

        self.__action_close_window = QAction(
            self.tr("Close Window"), self,
            objectName="action-close-window",
            triggered=self.close_window,
            shortcut=Qt.ShiftModifier | QKeySequence.Close
        )

        self.__action_save = QAction(
            self.tr("Save"), self,
            objectName="action-save",
            triggered=self.save,
            shortcut=QKeySequence.Save
        )

        self.__action_save_as = QAction(
            self.tr("Save As"), self,
            objectName="action-save-as",
            triggered=self.save_as,
            shortcut=QKeySequence.SaveAs
        )

        self.__action_revert = QAction(
            self.tr("Revert"), self,
            objectName="action-revert",
            triggered=self.revert
        )

    def widget(self):
        """
        Return a widget/view of the open document.

        Returns
        -------
        widget : QtGui.QWidget
        """
        raise NotImplementedError

    def documentController(self):
        """
        Return the DocumentControler instance which manages this document.

        Return None if the document is not associated with a controller

        Returns
        -------
        controller : Option[DocumentControler]
        """
        return self.__document_controller

    def _setDocumentController(self, controller):
        """
        Only the document controller should call this
        """
        self.__document_controller = controller

    @classmethod
    def documentType(self):
        """
        Return the associated document type

        Returns
        -------
        doctype : Document.Type
        """
        return None

    def path(self):
        """
        Return the url path associated with this document.
        """
        return QUrl(self.__url)

    def setPath(self, url):
        """
        Associate an url with this document.
        """
        url = QUrl(url)
        if self.__url != url:
            self.__url = url
            self.pathChanged.emit(url)

    def setTitle(self, title):
        """
        Set the document title (display name).
        """
        if self.__title != title:
            self.__title = title
            self.titleChanged.emit(title)

    def title(self):
        """
        Return the document title.
        """
        return self.__title

    def read(self, url, doctype=None):
        """
        Read and load a document from `url`.
        """
        raise NotImplementedError

    def write(self, url, doctype=None):
        """
        Save the document to an associated url.

        If no associated url is set a user is presented with a file
        dialog to select a file system path.

        This is the slot associated with the :gui:`Save` menu action.

        """
        if self.url():
            return self.write_to(self.url())
        else:
            return self.save_as()

    def saveAs(self):
        filename, fileformat = self.run_save_file_dialog()
        if filename:
            return self.write_to(filename, fileformat)
        else:
            return False

    def saveToPath(self, url):
        try:
            sucess = self.write(url, self.documentType())
        except Exception:
            self.report_save_error(url)
            return False

        if sucess:
            self.setPath(url)
        else:
            return True

    def writeToPath(self, url, doctype=None):
        raise NotImplementedError

    def isModified(self):
        return self.__modified

    def setModified(self, modified):
        if self.__modified != modified:
            self.__modified = bool(modified)
            self.modifiedChanged.emit(self.__modified)

    def isTransient(self):
        return self.__transient

    def close(self):
        if self.isModified() and not self.isTransient():
            title = self.title()
            url = self.path()
            if not title:
                if url:
                    title = os.path.basename(url)
                else:
                    title = "untitled"

            result = utils.message_question(
                self.tr("Do you want to save the changes you made "
                        "to document '%s'?" % title),
                self.tr("Save Changes?"),
                self.tr("Your changes will be lost if you do not save them."),
                buttons=(QMessageBox.Save | QMessageBox.Cancel |
                         QMessageBox.Discard),
                default_button=QMessageBox.Save,
                parent=self.widget()
            )
            if result == QMessageBox.Save:
                return self.save()
            elif result == QMessageBox.Discard:
                return True
            elif result == QMessageBox.Cancel:
                return False
        else:
            return True

    def closeWidget(self):
        """
        Attempt to close the associated widget/view.

        Return a boolean indicating if the widget accepted the
        close request.

        Returns
        -------
        accepted : bool
        """
        return self.widget().close()

    def activate(self):
        """
        Activate (show, raise and give focus) the associated widget/view.
        """
        widget = self.widget()
        if widget:
            widget = self.widget()
            widget.show()
            widget.raise_()
            widget.activateWindow()

    def saveFileDialog(self, ):
        dialog = QFileDialog(
            self.widget(),
            fileMode=QFileDialog.ExistingFile,
            acceptMode=QFileDialog.AcceptSave,
            windowTitle=self.tr("Save"))
        dialog.setDefaultSuffix(self.documentType().ext)
        # startdir = QDesktopServices.location(QDesktopServices.DocumentsLocation)
#         dialog.setDirectory(startdir)
        types = self.documentType()
        spec = types_to_filters(types)
        dialog.setFilters(";;".join(spec))
        # set current filter
        return dialog

    def runSaveFileDialog(self, ):
        dialog = self.saveFileDialog()
        dialog.show()
        dialog.done.connect(do_or_do_not_there_is_no_try)
# 
#     def run_save_file_dialog(self):
#         if self.url().isValid():
#             start_dir = self.url()
#         else:
#             start_dir = QDesktopServices.storageLocation(
#                 QDesktopServices.DocumentsLocation)
#         types = self.document_type()
#         filter_spec = types_to_filters(types)
#         filename, selected_filter = QFileDialog.getSaveFileName(
#             self.widget(), "Save", start_dir, ";;".join(filter_spec))
# 
#         return filename, selected_filter

    def undoStack(self):
        """
        Return a QUndoStack for the document.

        The default implementation returns None.
        """
        return None

    def actions(self):
        """
        Return a list of actions supported by the document/editor.
        """
        return []

        return (("File", ("Save", "Save as...", "Close", "Workflow Info"),)
                ("Edit", ("Copy", "Cut", "Paste")),
                ("View", ("Expand Dock", "Display Margins",
                          "Zoom In", "Zoom Out",
                          "---[separator]---", "Output")),
                ("Tools", ("Annotate", "Align to grid", )))

    def eventFilter(self, receiver, event):
        if receiver is self.widget() and event.type() == QEvent.Close:
            event.setAccepted(self.close())
            return True
        else:
            return super(Document, self).eventFilter(receiver, event)


class TxtDocument(Document):
    def __init__(self, parent=None, **kwargs):
        super(TxtDocument, self).__init__(parent, **kwargs)
        self.__contents = ""
        self.__widget = None

    def open(self, url):
        filepath = str(url.toLocalFile())
        try:
            with io.open(filepath, "r") as f:
                txt = f.read()
        except (IOError, OSError) as err:
            utils.message_warning(
                self.tr("Could not open '{}'.".format(filepath)),
                title=self.tr("Error"),
                informative_text=os.strerror(err.errno)
            )
            return False
        else:
            self.__contents = txt

    def widget(self):
        if self.__widget is None:
            w = QPlainTextEdit()
            if self.__contents is not None:
                w.setPlainText(self.__contents)
            self.__widget = w
        return self.__widget


class DocumentController(QObject):
    """
    A controller/manager of open documents in an application.

    """
    documentOpened = Signal(Document)
    documentClosed = Signal(Document)

    #: The current topmost open document has changed.
    currentDocumentChanged = Signal(Document)

    def __init__(self, parent=None, **kwargs):
        self.__maxRecentCount = 10
        self.__lastDirectory = ""
        self.__documentTypes = [Document.Type("All", None, ".*")]
        self.__defaultDocumentClass = None
        self.__documents = []

        QObject.__init__(self, parent, **kwargs)

        self.__action_new = QAction(
            self.tr("New"), self,
            objectName="action-new",
            triggered=self.new,
            shortcut=QKeySequence.New,
        )

        self.__action_open = QAction(
            self.tr("Open"), self,
            objectName="action-open",
            triggered=self.open,
            shortcut=QKeySequence.Open
        )

        self.__recent_group = QActionGroup(self, checkable=False)

        self.__action_recent = QAction(
            self.tr("Open Recent"), self,
            objectName="action-open-recent",
        )

        self.__action_clear_recent = QAction(
            self.tr("Clear Recent"), self,
            objectName="action-clear-recent",
            triggered=self.clear_recent
        )

        self.__action_browse_recent = QAction(
            self.tr("Browse Recent"), self,
            objectName="action-browse-recent",
            triggered=self.browse_recent,
            shortcut=QKeySequence(
                Qt.ControlModifier | (Qt.ShiftModifier | Qt.Key_R)),
        )

        self.__recent_menu = QMenu()
        self.__recent_menu.addAction(self.__action_browse_recent)
        self.__recent_menu.addSeparator()
        self.__recent_menu.addAction(self.__action_clear_recent)

        self.__action_recent = QAction(
            self.tr("Recent"), self, objectName="action-recent"
        )
        self.__action_recent.setMenu(self.__recent_menu)

        self.__action_reload_last = QAction(
            self.tr("Reload Last"), self,
            objectName="action-reload-last",
            triggered=self.reload_last,
            shortcut=QKeySequence(Qt.ControlModifier | Qt.Key_R),
        )

        #: `Window` menu (in a OSX unified menu bar).
        self.__action_window = QAction(
            self.tr("Window"), self,
            objectName="action-window",
        )

    def setMaxRecentCount(self, count):
        """
        Set the maximum number of recent documents to keep track of.
        """
        if self.__maxRecentCount != count:
            self.__maxRecentCount = count
            del self.__recent_list[count:]

    def maxRecentCount(self):
        """
        Return the maximum number of recent documents.
        """
        return self.__maxRecentCount

    # Action getters
    # ?? def action(ActionType) -> QAction
    # ActionType = .New, .Open, .Recent, BrowseRecent, ...
    def actionNew(self):
        """Return the default 'New' document action.
        """
        return self.__action_new

    def actionOpen(self):
        """Return the default 'Open' document action.
        """
        return self.__action_open

    def recent_action(self):
        """
        Return an QAction (with a QMenu) of recent documents.
        """
        return self.__recent

    def clear_recent_action(self):
        """
        Return the 'Clear Recent' QAction.
        """
        return self.__clear_recent

    def browse_recent_action(self):
        """
        Return the 'Browse Recent' QAction.
        """
        return self.__browse_recent

    def reload_last_action(self):
        """
        Return the 'Reload Last' QAction.
        """
        return self.__reload_last

    def reload_last(self):
        """
        Reload the last saved document.
        """
        recent = self.recent_items()
        recent = sorted(recent, key=lambda item: item.time)
        if recent:
            url = recent[-1].url
            self.open_document(url)
            # What it should look like
#             loader = recent[-1].loader  # Use the associated document loader/type
#             # loader = self.last_used_type_for(url)
#             self.open_document(url, loader=loader)

    def window_action(self):
        # OSX style 'Window' menu bar action.
        return self.__window

    def documentTypes(self):
        return list(self.__documentTypes)

    def setDocumentTypes(self, types):
        self.__documentTypes = types

    def setDefaultDocumentType(self, type):
        self.__default_document_class = type

    def newDocument(self, doctype=None):
        if doctype is None:
            doctype = self.default_document_class()

        doc = doctype.create(self)
        self.addDocument(doc,)
        return doc

    def open(self):
        """
        """
        dialog = self.openFileDialog()
        dialog.show()

        def whendone(path, doctype, ):
            if path:
                doc = doctype.create(self)
                doc.read(path)
                doc.setPath(path)
                self.addDocument(doc)

        dialog.done.connect(whendone)

# 
#         types = self.document_types()
#         filename, filetype = self.run_open_file_dialog(types=types)
# 
#         if not filename:
#             return

        doc_class = self.document_class_for_url(filename)

        curr_doc = self.current_document()
        if type(curr_doc) is doc_class and curr_doc.is_transient():
            curr_doc.open(filename)
        else:
            self.open_document(filename)

    def openFileDialog(self, ):
        dialog = QFileDialog(
            fileMode=QFileDialog.AnyFile,
            acceptMode=QFileDialog.AcceptOpen,

        )
#         directory = QDesktopServices.storageLocation(
#             QDesktopServices.DocumentsLocation)
#         dialog.setDirectory(directory)
        doctypes = self.documentTypes()
        specs = types_to_filters(doctypes)
        dialog.setNameFilters(specs)
        dialog.selectNameFilter()
        return dialog

#     def run_open_file_dialog(self, types=None):
#         if types is None:
#             types = self.document_types()
#         if not types:
#             types = [file_format("All", "", "*")]
# 
#         if self.__lastDirectory:
#             start_dir = self.__lastDirectory
#         else:
#             start_dir = QDesktopServices.storageLocation(
#                 QDesktopServices.DocumentsLocation)
# 
#         filters = types_to_filters(types)
#         filters_spec = ";;".join(filters)
#         filename, selected_filter = QFileDialog.getOpenFileNameAndFilter(
#             None, "Open", start_dir, filter=filters_spec
#         )
#         if filters:
#             selected_filter = filters.index(selected_filter)
#             selected_filter = types[selected_filter]
#         else:
#             selected_filter = None
# 
#         return six.text_type(filename), selected_filter

    def defaultDocumentClass(self):
        if self.__default_document_class:
            return self.__default_document_class
        else:
            return None

    def documentClassForUrl(self, url):
        raise NotImplementedError

    def openDocument(self, url):
        """
        Open a new document for `url`
        """
        doc_class = self.documentClassForUrl(url)
        doc = doc_class.create(self)
#         print(doc, url)
        if doc.read(url, doctype=doc_class):
            self.addDocument(doc)
            return True
        else:
            return False

    def addDocument(self, document):
        """
        Add a document instance to this controller.
        """
        if document in self.__documents:
            raise ValueError("Document was already added.")

        self.__documents.append(document)
        document._set_document_controller(self)
        if document.path():
            self.record_recent(
                recent_item(document.title(), document.url(), time.now())
            )

        self.document_added.emit(document)

    def closeAll(self):
        """
        Close all open documents.

        Return True if all documents accepted the close, otherwise
        return False.
        """
        for doc in self.__documents:
            if not doc.close():
                return False
        else:
            QApplication.instance().closeAllWindows()
            return True

    def currentDocument(self):
        """
        Return the current (top most) document.
        """
        window = QApplication.activeWindow()
        for doc in self.documents():
            # TODO: Search up to parent window (dialog) chain.
            if doc.widget() is window:
                return doc
        else:
            return None

    #: Current (top most active) document has changed.
#     currentDocumentChanged = Signal(QWidget)

    def documents(self):
        """
        Return a list of all documents.
        """
        return list(self.__documents)

    def has_modified_documents(self):
        """
        Return True if any document is in a modified state.
        """
        return any(doc.is_modified() for doc in self.__documents)

    def recent_items(self):
        """
        Return a list of recently open items.
        """
        return list(self.__recent)

    def noteRecent(self, item):
        if not item.url():
            return
        if not item.display_name:
            display_name = os.path.basename(item.url())

        path = os.path.realpath(item.url())
        path = os.path.abspath(path)
        path = os.path.normpath(path)

        # find an item with the same path if it exists
        existing = fn.find(self.recent_actions,
                           pred=lambda ac: qunwrap(ac.data()).url == path)
        if existing:
            action = existing.val
            self.__recent.pop(qunwrap(action.data()))
            # remove from group for later re-insertion
            self.__recent_group.removeAction(action)
            self.__recent_menu.removeAction(action)

        else:
            # TODO: use QFileIconProvider to get the file icon
            action = QAction(
                display_name, self, toolTip=item.url,
            )

            action.triggered.connect(lambda: self.open_document(path))

        action.setData(item)
        actions = filter(
            lambda a: recent_item.isinstance(qunwrap(a.data())),
            self.__recent_menu.actions()
        )

        begin = fn.find(fn.pairs(actions),
                        lambda pair: pair[0] is self.__recent_begin)
        if begin:
            _, first = begin.val
        else:
            first = None

        self.__recent_menu.insertAction(first, action)
        self.__recent_group.addAction(action)
        self.__recent.insert(0, item)

    def clearRecent(self):
        """
        Clear the list of recently opened items.
        """
        actions = self.__recent_menu.actions()
        actions = [action for action in actions
                   if isinstance(qunwrap(action.data()), recent_item)]

        for action in actions:
            self.__recent_menu.removeAction(action)

        self.__recent = []

    def browseRecent(self):
        """
        Open dialog with recently opened items.
        """
        raise NotImplementedError

    def saveState(self):
        state = (
            1,
            ("recent-documents",
             [(recent.url, recent.title)
              for recent in self.recent_documents()]),
        )
        # TODO: associate window state for the document.

        return state

    def restoreState(self, state):
        ver, rest = state
        if ver == 1:
            _, recent = rest
            self.__recent = [recent_item(url, title)
                             for url, title in recent]


recent_item = namedtuple(
    "recent_item",
    ["title",
     "url"],
)


file_format = namedtuple(
    "file_format",
    ["name",
     "mimetype",
     "extension"]
)


def types_to_filters(types):
    return ["{} (*.{})".format(t.name, t.extension)
            for t in types]


class fn(object):
    Some = namedtuple("Some", ["val"])

    @staticmethod
    def index_in(el, sequence):
        return fn.index(partial(operator.eq, el), sequence)

    @staticmethod
    def index(pred, sequence):
        try:
            return fn.Some(next(i for i, v in enumerate(sequence) if pred(v)))
        except StopIteration:
            return None

    @staticmethod
    def find(pred, sequence):
        try:
            return fn.Some(next(v for v in sequence if pred(v)))
        except StopIteration:
            return None

    @staticmethod
    def pairs(sequence):
        s1, s2 = itertools.tee(sequence)
        try:
            next(s2)
        except StopIteration:
            return zip((), ())
        else:
            return zip(s1, s2)
