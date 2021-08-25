import logging
import sys
sys.path.append('../')

from wieserlabsdds import WieserlabsClient

"""
This example ramps the frequency of slot 0, channel 0 from 1MHz to 2MHz over the
span of one second. Connect to spectrum analyzer to see the result.
"""

client = WieserlabsClient("10.0.0.237", max_amp=17.38, loglevel=logging.INFO)
client.reset(0)
client.run(0)

client.frequency_ramp(slot_index=0, channel=0,
    fstart=1e6, fend=2e6,
    amp=1,
    phase=0,
    tramp=1,
    fstep=1, is_filter=False)

client.run(0)
