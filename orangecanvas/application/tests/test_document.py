import io

from PyQt4.QtGui import QTextEdit

from ...gui.test import QAppTestCase

from ..application import Document, DocumentController


class TextDocument(Document):

    def documentWidget(self):
        if self.__widget is None:
            self.__widget = QTextEdit()
        if self.__contents:
            self.__widget.setPlainText(self.__contents)
        return self.__widget

    def documentType(self):
        return Document.Type("Text", "text/plain", (".txt", ))

    def read(self, path, doctype=None):
        with io.open(path, "rt") as f:
            self.__contents = f.read()

        if self.__widget is not None:
            self.__widget.setPlainText(self.__contents)

    def write(self, path, doctype=None):
        if self.__widget is not None:
            contents = self.__widget.toPlainText()
        else:
            contents = self.__contents
        with io.open(path, "wt") as f:
            f.write(contents)


class RichTextDocument(Document):
    def documentWidget(self):
        if self.__widget is None:
            self.__widget = QTextEdit()
        if self.__contents:
            self.__widget.setHtml(self.__contents)
        return self.__widget

    def read(self, path, doctype=None):
        with io.open(path, "rt") as f:
            self.__contents = f.read()

        if self.__widget is not None:
            self.__widget.setHtml(self.__contents)

    def write(self, path, doctype=None):
        if self.__widget is not None:
            contents = self.__widget.toHtml()
        else:
            contents = self.__contents
        with io.open(path, "wt") as f:
            f.write(contents)


class TestDocumentControler(DocumentController):
    def documentTypes(self):
        return [Document.Type("Text", "text/plain", (".txt", )),
                Document.Type("Rich Text", "text/enriched", (".rtf", ))]


class TestDocument(QAppTestCase):
    def test(self):

        def record(*args):
            record.args = args
        doc = TextDocument()
        self.assert_(doc.documentType())
        doc.titleChanged.connect(record)
        doc.pathChanged.connect(record)
        doc.modifiedChanged.connect(record)

        doc.setTitle("Diabolical!")
        self.assertEqual(doc.title(), "Diabolical", )
        self.assertEqual(record.args, ("Diabolical", ))

        doc.setPath("/the/one/path/to/rule/them/all")
        self.assertEqual(doc.path(), "/the/one/path/to/rule/them/all")
        self.assertEqual(record.args, ("/the/one/path/to/rule/them/all",))

        doc.setModified(True)
        self.assertTrue(doc.isModified())
        self.assertEquals(record.args, (True,))

        doc = TextDocument()

        doc.read(io.BytesIO(b"What do your call a blue person anyway"))
        w = doc.widget()
        self.assert_(w.parent() is None)
        w.close()


class TestControler(QAppTestCase):
    def test(self):
        def record(*args):
            record.args = args
