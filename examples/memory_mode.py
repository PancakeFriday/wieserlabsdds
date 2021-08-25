import logging
import sys
sys.path.append('../')

from wieserlabsdds import WieserlabsClient, RamParameterType, OutputType

"""
This example ramps the frequency of slot 0, channel 0 from 1MHz to 2MHz over the
span of one second. Connect to spectrum analyzer to see the result.
"""

client = WieserlabsClient("10.0.0.237", max_amp=17.38, loglevel=logging.INFO)
client.reset(0)
client.run(0)


# Generate a "trigger" on channel 1
client.wait_time(0, 1, 75e-6)
client.single_tone(0, 1, 1e6, 1 ,0)
client.wait_time(0, 1, 2e-6)
client.single_tone(0, 1, 1e6, 0 ,0)

import numpy as np

# Generate a frequency ramp. By using the memory mode, we can run a ramp while
# simultaneously playing data from the memory. If we do not wish to play a ramp,
# we simply omit ramp_filter in client.from_memory (or set it to None)
client.frequency_ramp(slot_index=0, channel=0,
    fstart=1e6, fend=4e6,
    amp=1,
    phase=0,
    tramp=50e-6,
    fstep=400, is_filter=True)

xfine = np.linspace(0, np.pi, 100)
yfine = np.sin(xfine)
client.from_memory(slot_index=0, channel=0,
    param_type=RamParameterType.AMPLITUDE,
    storage=yfine,
    freq=1e6,
    amp=1,
    phase=0,
    tramp=50e-6,
    ramp_filter=RamParameterType.FREQUENCY)

client.run(0)
