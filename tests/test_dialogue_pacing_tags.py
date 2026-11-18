"""Tests for inline Say dialogue pacing tag preservation ({nw}, {w}, {fast}, {cps})."""

import unittest

import compile_script


class DialoguePacingTagsTests(unittest.TestCase):
    def test_nw_and_wait_preserved_outside_quotes(self) -> None:
        encoded = compile_script._encode_dialogue_tags("{i}(Yes!){/i}{w=0.5}{nw}", quoted=True)
        self.assertEqual(encoded, '"(Yes!)"\x02\x25\x01')

        encoded_wait75 = compile_script._encode_dialogue_tags(
            'So that\'s why--"{space=5000}{w=0.75}{nw}', quoted=True
        )
        self.assertEqual(encoded_wait75, '"So that\'s why--""\x02\x28\x01')

    def test_fast_split_points(self) -> None:
        trailing = compile_script._encode_dialogue_tags("A literature club!{fast}", quoted=True)
        self.assertEqual(trailing, '"A literature club!"\x03')

        mid = compile_script._encode_dialogue_tags(
            "But I really appreciate you understan{fast}[gtext]{nw}", quoted=True
        )
        self.assertEqual(mid, '"But I really appreciate you understan\x03[gtext]"\x01')

    def test_cps_fixed_and_multiplier(self) -> None:
        slow = compile_script._encode_dialogue_tags("{cps=30}.......Sayo--{/cps}{nw}", quoted=True)
        self.assertEqual(slow, '".......Sayo--"\x04\x21\x01')

        fast = compile_script._encode_dialogue_tags("{cps=150}Yuri...?{/cps}{nw}", quoted=True)
        self.assertEqual(fast, '"Yuri...?"\x04\x26\x01')

        mult = compile_script._encode_dialogue_tags("{cps=*2}How?{/cps}{nw}", quoted=True)
        self.assertEqual(mult, '"How?"\x04\x28\x01')

    def test_literal_double_brace_and_non_say_strip(self) -> None:
        say = compile_script._encode_dialogue_tags("{{hello}}{nw}", quoted=False)
        self.assertEqual(say, "{hello}}\x01")
        self.assertEqual(compile_script._strip_text_tags("{{i}}{nw}"), "{i}}")


if __name__ == "__main__":
    unittest.main()
