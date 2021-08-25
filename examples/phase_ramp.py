import logging
import sys
sys.path.append('../')

from wieserlabsdds import WieserlabsClient

"""
This example ramps the phase of slot 0, channel 0 from 0deg to 180deg over the
span of ten microseconds at 1MHz.
To see the result, channel 1 has a constant 1MHz signal. On the oscilloscope,
connect both signals and either overlap them or place them below each other.
Set a marker at the first maximum of channel 1 and note the position of the signal
on channel 0 relative to the next maximum. Then set a marker at the last maximum
of channel 1 and see that the relative position to the next maximum on channel 1
has changed.

Not sure if continous phase ramps are ever going to be used, but there you go.
"""

client = WieserlabsClient("10.0.0.237", max_amp=17.38, loglevel=logging.INFO)
client.reset(0)
client.run(0)

client.phase_ramp(slot_index=0, channel=0,
    freq=1e6,
    amp=1,
    pstart=0, pend=180,
    tramp=10e-6,
    pstep=1e-0, is_filter=False)

# Single tone on channel 1 to see the ramp happening
client.single_tone(0, 1, 1e6, 1, 0)
client.wait_time(0, 1, 50e-6)
client.single_tone(0, 1, 1e6, 0)

client.run(0)
