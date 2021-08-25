import logging
import sys
sys.path.append('../')

from wieserlabsdds import WieserlabsClient

"""
This example ramps the amplitude of slot 0, channel 0 from 0% to 100% over the
span of one second at 1MHz. Connect to spectrum analyzer to see the result.
"""

client = WieserlabsClient("10.0.0.237", max_amp=17.38, loglevel=logging.INFO)
client.reset(0)
client.run(0)

client.amplitude_ramp(slot_index=0, channel=0,
    freq=1e6,
    astart=0, aend=1,
    phase=0,
    tramp=1,
    astep=1e-5, is_filter=False)

client.run(0)
