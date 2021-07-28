import socket
import sys
import time

# We need to convert a frequency to DDS compatible language
def freq_to_word(f):
    # f in Hz
    if f < 0 or f >= 1e9:
        print("freq needs to be in range [0,1e9)")
        num = 0
    num = int(2**32/1e9*f) & 0xffff_ffff

    padding = 10
    return (f"{num:#0{padding}x}")[2:]

# Calculate the amplitude into the DDS language
# Both values are given in dBm
# amp_max is the amplitude that is set on the front panel of the DDS
# using a screw driver. The value can be read out using a spectrum analyzer
def amp_to_word(amp, amp_max):
    if amp > amp_max:
        print("Amplitude needs to be in range [-inf, amp_max]")
        return "3fff"
    asf = int(10**((amp-amp_max)/20) * (2**14-1))
    return (f"{asf:#0{6}x}")[2:]

def phase_to_word(phase):
    phase = phase%360
    p = int(0xffff * phase / 360)
    return (f"{p:#0{6}x}")[2:]

def set_bit(v, index, x):
    """Set the index:th bit of v to 1 if x is truthy, else to 0, and return the new value."""
    mask = 1 << index   # Compute mask, an integer with just bit 'index' set.
    v &= ~mask          # Clear the bit indicated by the mask (if x is False)
    if x:
        v |= mask         # If x was True, set the bit indicated by the mask.
    return v            # Return the result, we're done.

class WieserlabsClient:
    def __init__(self, ip_address, max_amp):
        """
        This is a client written for the Wieserlabs DDS rack.
        It is a very versatile hardware and this is an attempt at covering at least the very basics.

        Before using this class, make sure to calibrate the output level of the DDS!
        Use the calibrate_amplitudes function (which is global in this file).
        This sets a maximum amplitude, single tone on all DDSs, which you can read out using a spectrum analyzer.
        The output amplitude can be changed using the potentiometer on the front panel of the slots. Set this
        to a preferred value and note down the peak amplitude. This is the value given into max_amp in dBm.
        """
        self.ip_address = ip_address
        self.max_amp = max_amp

        # We will store a message stack that holds all instructions.
        # As soon as we call the run() function, the message stack is flushed
        # to the dcp, where the commands are processed and run.
        # If the exact start time is important, consider including a trigger
        # event at the beginning.
        self.message_stack = []
        # We have a message stack for each slot in the DDS rack
        for slot in range(6):
            self.message_stack.append([])

        # Create a TCP/IP socket for each slot
        self.sockets = {}
        for slot in range(6):
            self.sockets[slot] = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

        self._connect_all_slots()
        for slot in range(6):
            self._reset_cfr(slot)

    def _validate_slot_channel(self, slot, channel):
        if channel != 0 and channel != 1:
            print("[ERROR]: Invalid channel number")
            return -1

        if slot < 0 or slot > 5:
            print("[ERROR]: Invalid slot value")
            return -1

        return 1

    def _send_receive(self, slot, msg):
        """ Send a message and receive the answer, the board should give an OK if the command worked,
            else it will print an error message (except for the authentication)
        """
        print("-------------")
        print(f"Sending msg {msg}")
        self.sockets[slot].sendall(bytes(msg+"\n", 'ascii'))
        data = self.sockets[slot].recv(1024)

        msg = data.decode('ascii').strip()
        print(msg)
        if "error" in msg.lower():
            # TODO ?
            raise ValueError()

    def _set_CFR_bit(self, slot, channel, cfr_number, bit_number, bit_value):
        """
        This is a super-super low-level function and should only be called by
        someone who knows what they are doing! Anyways, this sets bits in the
        control function registers of the AD9910, which are documented in the
        AD9910 datasheet.
        """
        if self._validate_slot_channel(slot, channel) == -1:
            return -1

        if cfr_number != 1 and cfr_number != 2:
            print("[ERROR]: Invalid value for cfr_number!")
            return -1

        if bit_number < 0 or bit_number > 31:
            print("[ERROR]: Invalid value for bit_number!")
            return -1

        if bit_value != 0 and bit_value != 1:
            print("[ERROR]: Invalid value for bit_value!")
            return -1

        print(hex(self._cfr_regs[cfr_number-1]))
        self._cfr_regs[cfr_number-1] = set_bit( self._cfr_regs[cfr_number-1],
                                                bit_number,
                                                bit_value)

        val = f"{self._cfr_regs[cfr_number-1]:#0{10}x}"
        msg = f"dcp {channel} spi:CFR{cfr_number}={val}"
        self.push_message(slot, msg)

    def _reset_cfr(self, slot):
        self._cfr_regs = [0x00410002, 0x004008C0]
        for cfr_number in range(2):
            for channel in range(2):
                val = f"{self._cfr_regs[cfr_number]:#0{10}x}"
                msg = f"dcp {channel} spi:CFR{cfr_number+1}={val}"
                self.push_message(slot, msg)
        self.run(slot)

    def _connect_all_slots(self):
        """ Connect to port 2600n, where n is card number (0 here = first card) """
        for i, sock in self.sockets.items():
            server_address = (self.ip_address, 26000 + i)
            print(f"connecting to {server_address[0]} port {server_address[1]}")
            sock.connect(server_address)
            print("Connected")

            self._authenticate(i)

    def push_update(self, slot, channel):
        """ Update the DDS, so that the changes take effect
        """
        self.push_message(slot, self._update_command(channel))

    def _authenticate(self, slot):
        """Send in the authentication string. The last character is the card number"""
        assert(slot < 16)
        self.push_message(slot, f"75f4a4e10dd4b6b{slot}")
        self.run(slot)

    def _reset_command(self):
        return f"dds reset"

    def _update_command(self, channel):
        return f"dcp {channel} update:u"

    def _freq_command(self, channel, freq, amp, phase):
        """ Generate the command to set the frequency
            Parameters:
                channel: 0 or 1, the channel on the slot
                freq: Frequency in Hz
                amp: The amplitude in dBm
        """

        amp_w = amp_to_word(amp, self.max_amp)
        phase_w = phase_to_word(phase)
        freq_w = freq_to_word(freq)
        return f"dcp {channel} spi:stp0=0x{amp_w}_{phase_w}_{freq_w}"

    def push_message(self, slot, cmd):
        self.message_stack[slot].append(cmd)

    def reset(self, slot):
        """Reset the dds"""
        cmd = self._reset_command()
        self.push_message(slot, cmd)

    def single_tone(self, slot, channel, freq, amp, phase=0):
        """ Generate a single tone
            Parameters:
                slot: 0..5, the hardware slot used in the rack
                channel: 0 or 1, the channel on the slot
                freq: Frequency in Hz
                amp: The amplitude in dBm
                phase: The phase of the note in degrees (0..360)
        """

        # Make sure single tone amplitude control is on
        client._set_CFR_bit(slot, channel, 2, 24, 1)

        # Generate the command
        cmd = self._freq_command(channel, freq, amp, phase%360)

        # Push the command + update, in order to activate the DDS for this
        # single tone
        self.push_message(slot, cmd)
        self.push_update(slot, channel)

    def run(self, slot):
        msg = "\n".join(self.message_stack[slot])
        self._send_receive(slot, msg)
        self.message_stack[slot].clear()

client = WieserlabsClient("10.0.0.237", max_amp=17.38)
client.reset(1)
client.single_tone(slot=0, channel=0, freq=1e6, amp=1, phase=0)
client.single_tone(slot=0, channel=1, freq=1e6, amp=1, phase=180)
client.run(0)

# inputs = [
#     "dcp 0 spi:stp0=0x3fff000001cfc183",
#     "dcp update:u!",
# ]
#
# import time
# client._send_receive(1, "dds reset")
# time.sleep(1)
# for i in inputs:
#     client._send_receive(1, i)

# print(client._send_receive(0, "dcp 0 spi:stp0=0x0000000001cac083"))
# client._update(0, 0)
# client._send_receive(0, "dcp start")
# client.single_tone(0, 0, 7e6, 1)
# print(amp_to_word(-34, 2))

# Use this function in combination with one of the lists below to send a sequence to the DDS
def run_commands(commands):
    for c in commands:
        send_receive(c)

# Simply set the frequency to 107 MHz
# set_freq = [
#     freq_command(channel=1, freq=10e6, amp=1),
#     "dcp update:u"
# ]
#
# # Do an amplitude modulation (needs input on the analog channel, e.g. square fn with peak to peak = 400mV)
# amp_mod = [
#     freq_command(channel=0, freq=50e6), # Set frequency
#     "dcp 0 spi:CFR1=0b00000000_01000001_00000000_00000000", # sinc filter and sine output
#     "dcp 0 spi:CFR2=0b00000000_00000000_00000000_01010000", # enable parallel data port
#     "dcp 0 wr:AM_S0=0x1000", # set scale factor S0
#     "dcp 0 wr:AM_O0=0", # Set offset O0
#     "dcp 0 wr:AM_O=0x8000", # set global offset O
#     "dcp 0 wr:AM_CFG=0x2000_0000", # choose amplitude modulation, flush coeff
#     "dcp update:u"
# ]
#
# # Do a phase modulation. Also needs an input on the analog channel
# phase_mod = [
#     freq_command(channel=0, freq=11.7e6), # Set frequency
#     "dcp spi:CFR1=0b01000001_00000000_00000000", # sinc filter and sine output
#     "dcp 0 spi:CFR2=0b00000000_00000000_01010000", # enable parallel data port
#     "dcp 0 wr:AM_S0=0x1000", # set scale factor S0
#     "dcp 0 wr:AM_O0=0", # Set offset O0
#     "dcp 0 wr:AM_O=0x8000", # set global offset O
#     "dcp 0 wr:AM_CFG=0x2000_0001", # choose phase modulation, flush coeff
#     "dcp update:u"
# ]
#
# # Do a frequency modulation. Also needs an input on the analog channel
# freq_mod = [
#     freq_command(channel=0, freq=0), # Set frequency
#     "dcp 0 spi:CFR1=0b01000001_00000000_00000000", # sinc filter and sine output
#     "dcp 0 spi:CFR2=0b00000000_00000000_01011100", # enable parallel data port
#     "dcp 0 wr:AM_S0=0x51f", # set scale factor S0
#     "dcp 0 wr:AM_O0=0", # Set offset O0
#     "dcp 0 wr:AM_O=0x51eb", # set global offset O
#     "dcp 0 wr:AM_CFG=0x2000_0002", # choose frequency modulation, flush coeff
#     "dcp update:u"
# ]
#
# # Create a ramp
# ramp = [
#     "dcp 0 wr:CFG_BNC_A=0x300", # turn on BNC connector
#     "dcp spi:CFR2=0x80", # Set matched latency and ramp destination frequency
#     "dcp spi:DRL=0x%s%s"%(freq_to_word(30e6), freq_to_word(1e6)), # ramp limits high and low
#     "dcp spi:DRSS=0x0000000d0066666a", # ramp step size
#     "dcp spi:DRR=0x00960096", # ramp rate
#     "dcp spi:CFR2=0x80080", # enable ramp generator
#     "dcp update:u+d", # drive upward ramp
#     "dcp wait:3000000:", # wait
#     "dcp 0 wr:CFG_BNC_A=0x200", # turn off BNC connector
#     "dcp update:-d", # drive downward ramp
# ]

# Actually send commands to the DDS
# authenticate(0)
# run_commands(set_freq)
