import unittest

from bamboozle.translate import Bamboozletranslate


class TestBamboozletranslate(unittest.TestCase):
    def setUp(self):
        self.translate = Bamboozletranslate(
            {"creampie": "internal_fluid_state", "gangbang": "multi_agent_collision"}
        )

    def test_forward(self):
        text = "She loves a good creampie and gangbang."
        expected = "She loves a good internal_fluid_state and multi_agent_collision."
        self.assertEqual(self.translate.forward(text), expected)

    def test_reverse(self):
        text = "She loves a good internal_fluid_state and multi_agent_collision."
        expected = "She loves a good creampie and gangbang."
        self.assertEqual(self.translate.reverse(text), expected)


if __name__ == "__main__":
    unittest.main()
