import sys
sys.path.append('../')

from wieserlabsdds import WieserlabsClient, VoltageToOutputMap, OutputType

"""
Example usage of using the analog input on the DDS to modify a signal.
Here, we generate a single tone using the DDS and modify its frequency using
a signal from outside.

To test this example, connect channel 0 of slot 0 to an oscilloscope.
Into analog in channel 0 of slot 0, connect a signal generator with 50us pulse.
The shape can be e.g. a triangle that should at least have 1V upper and -1V lower level.
"""

# This maps -1V of the analog input to a frequency of 1MHz, +1V to 10MHz.
output_map = VoltageToOutputMap(VoltageToOutputMap.ChannelType.CH0_ONLY,
    OutputType.FREQUENCY,
    v1ch0=-1, out1=1e6,
    v2ch0=1, out2=2e6)

# Initialize and reset to start with a clean slate
client = WieserlabsClient("10.0.0.237", max_amp=17.38)
client.reset(0)
client.run(0)

# 1MHz should be possible to see on most oscilloscope. The DDS can't really
# generate less than 0.5MHz
client.single_tone(0, 0, 1e6, 1 ,0)

# Apply the amplitude modulation using the map above
client.analog_modulation(0, 0, output_map)

# We generate a short pulse on channel 2 which we can trigger on
client.wait_time(0, 1, 10e-6)
client.single_tone(0, 1, 1e6, 1 ,0)
client.wait_time(0, 1, 10e-6)
client.single_tone(0, 1, 1e6, 0 ,0)

# Run the commands that we sent to the DDS.
client.run(0)
