"""Lifetime and malformed-asset checks now run in the quant fixture, not obsolete DMLUT assets."""
from test_ch5_motion_cache import MotionQuant
class MotionLut(MotionQuant):
 pass
