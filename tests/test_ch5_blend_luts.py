"""Bounded deterministic offline blend LUT generation."""
import unittest
from ch5_blend_luts import generate_blend_luts
class BlendLuts(unittest.TestCase):
 def test_bounds_endpoints_and_determinism(self):
  palette=[((i%32)<<10)|(((i//8)%32)<<5)|(i%32) for i in range(256)]
  nearest=bytes((i%255) for i in range(32768))
  groups,tables=generate_blend_luts(palette,nearest)
  self.assertEqual(len(groups),256)
  self.assertTrue(all(x<32 for x in groups))
  self.assertEqual(len(tables),16)
  self.assertTrue(all(len(t)==8192 for t in tables))
  self.assertTrue(all(x<255 for t in tables for x in t))
  for fg,p in enumerate(palette):
   self.assertEqual(tables[15][fg*32:fg*32+32],bytes([nearest[p]])*32)
  self.assertEqual((groups,tables),generate_blend_luts(palette,nearest))

 def test_reject_invalid_assets(self):
  with self.assertRaises(ValueError):generate_blend_luts([],bytes(32768))
  with self.assertRaises(ValueError):generate_blend_luts([32768]*256,bytes(32768))
  with self.assertRaises(ValueError):generate_blend_luts([0]*256,bytes([255])*32768)
