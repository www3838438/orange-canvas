"""
"""

from ...gui import test
from ...registry.tests import small_testing_registry
from ...registry import InputSignal, OutputSignal

from .. import SchemeNode


class TestScheme(test.QAppTestCase):
    def test_node(self):
        """Test SchemeNode.
        """
        reg = small_testing_registry()
        one_desc = reg.widget("one")

        node = SchemeNode(one_desc)

        inputs = node.input_channels()
        self.assertSequenceEqual(inputs, one_desc.inputs)
        for ch in inputs:
            channel = node.input_channel(ch.name)
            self.assertIsInstance(channel, InputSignal)
            self.assertTrue(channel in inputs)
        self.assertRaises(ValueError, node.input_channel, "%%&&&$$()[()[")

        outputs = node.output_channels()
        self.assertSequenceEqual(outputs, one_desc.outputs)
        for ch in outputs:
            channel = node.output_channel(ch.name)
            self.assertIsInstance(channel, OutputSignal)
            self.assertTrue(channel in outputs)
        self.assertRaises(ValueError, node.output_channel, "%%&&&$$()[()[")
