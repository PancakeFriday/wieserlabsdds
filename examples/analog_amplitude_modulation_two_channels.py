import sys
sys.path.append('../')

from wieserlabsdds import WieserlabsClient, VoltageToOutputMap, OutputType

"""
Example usage of using the analog input on the DDS to modify a signal.
This example shows how it is possible to use two analog inputs, in order
to set the modulation.

To test this example, connect channel 0 of slot 0 to an oscilloscope.
Into analog in channel 0 of slot 0, connect a signal generator with 50us pulse.
Into analog in channel 1 of slot 0, connect a signal generator with 25us pulse.
Select the shape for both channels to be a triangle with -1V to 1V amplitude
"""

# This maps -1V of the analog input to an amplitude of 30%, +1V to 100%.
output_map = VoltageToOutputMap(VoltageToOutputMap.ChannelType.BOTH,
    OutputType.AMPLITUDE,
    v1ch0=-1, v1ch1=-1, out1=0,
    v2ch0=0, v2ch1=0.5, out2=0.5,
    v3ch0=1, v3ch1=1, out3=1)

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
