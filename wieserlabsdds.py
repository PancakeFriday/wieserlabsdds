import logging
import socket
import sys
import time
from enum import Enum

import numpy as np

def push_or_pop(list_to_check, value, action_on_pop):
    """ This function checks `list_to_check` if it contains `value`.
    If not, it inserts the value. If it does exist, it is removed and
    action_on_pop(value) is run"""

    if value in list_to_check:
        list_to_check.remove(value)
        action_on_pop(value)
    else:
        list_to_check.append(value)

class TriggerEvent(Enum):
    NONE                    = 0   # No event
    ALL_SPI_FIFOS_FLUSHED   = 2   # SPI FIFO into AD9910 empty on both channels
    BNC_IN_A_RISING         = 3   # Rising edge seen on BNC input A
    BNC_IN_A_FALLING        = 4   # Falling edge seen on BNC input A
    BNC_IN_A_LEVEL          = 5   # Level (low/high) seen on BNC input A
    BNC_IN_B_RISING         = 6   # Rising edge seen on BNC input B
    BNC_IN_B_FALLING        = 7   # Falling edge seen on BNC input B
    BNC_IN_B_LEVEL          = 8   # Level (low/high) seen on BNC input B
    BNC_IN_C_RISING         = 9   # Rising edge seen on BNC input C
    BNC_IN_C_FALLING        = 10  # Falling edge seen on BNC input C
    BNC_IN_C_LEVEL          = 11  # Level (low/high) seen on BNC input C
    BP_TRIG_A               = 15  # Backplane trigger A (available only in rack version)
    BP_TRIG_B               = 16  # Backplane trigger B (available only in rack version)
    SPI_FIFO_FLUSHED        = 32  # SAME CHANNEL, SPI FIFO into AD9910 empty; all SPI writes finished
    SPI_FIFO_EV0            = 33  # SAME CHANNEL, SPI FIFO write event 0 [not yet documented]
    SPI_FIFO_EV1            = 34  # SAME CHANNEL, SPI FIFO write event 1 [not yet documented]
    DROVER                  = 35  # SAME CHANNEL, AD9910 ramp complete (DROVER pin)
    RAM_SWP_OVR             = 36  # SAME CHANNEL, AD9910 RAM sweep over (RAM SWP OVR pin)
    O_SPI_FIFO_FLUSHED      = 48  # OTHER CHANNEL, SPI FIFO into AD9910 empty; all SPI writes finished
    O_SPI_FIFO_EV0          = 49  # OTHER CHANNEL, SPI FIFO write event 0 [not yet documented]
    O_SPI_FIFO_EV1          = 50  # OTHER CHANNEL, SPI FIFO write event 1 [not yet documented]
    O_DROVER                = 51  # OTHER CHANNEL, AD9910 ramp complete (DROVER pin)AD9910 RAM sweep over (RAM SWP OVR pin)
    O_RAM_SWP_OVR           = 52  # OTHER CHANNEL, AD9910 RAM sweep over (RAM SWP OVR pin)

class RamParameterType(Enum):
    FREQUENCY       = 0
    PHASE           = 1
    AMPLITUDE       = 2
    POLAR           = 3     # Phase and amplitude at the same time

class OutputType(Enum):
    AMPLITUDE       = 0
    PHASE           = 1
    FREQUENCY       = 2

# We need to convert a frequency to DDS compatible language
def freq_to_word(f):
    # f in Hz
    if f < 0 or f >= 1e9:
        logging.warning("freq needs to be in range [0,1e9)")
        num = 0
    num = round(2**32/1e9*f) & 0xffff_ffff

    return (f"{num:0{8}x}")

def amp_to_word(amp):
    # amplitude must be larger than 0 and can't be more than 0x3fff.
    # However it is given in percent, so 0x3fff is 100%.
    return f"{round(max(0, min(0x3fff, 0x3fff*amp))):0{4}x}"

def phase_to_word(phase):
    phase = phase%360
    p = round(2**16 * phase / 360)
    return (f"{p:0{4}x}")

def get_bit(v, index):
    return (v >> index) & 1

def set_bit(v, index, x):
    """Set the index:th bit of v to 1 if x is truthy, else to 0, and return the new value."""
    mask = 1 << index   # Compute mask, an integer with just bit 'index' set.
    v &= ~mask          # Clear the bit indicated by the mask (if x is False)
    if x:
        v |= mask         # If x was True, set the bit indicated by the mask.
    return v            # Return the result, we're done.

# This is the parent class for the four most important dcp instructinos
class MessageType:
    def __init__(self):
        pass

    def clean_msg(self, msg):
        msg = msg.strip()
        while msg.find("  ") != -1:
            msg = msg.replace("  ", " ")
        return msg

class CustomMessage(MessageType):
    def __init__(self, text):
        self.text = text

    def get_message(self):
        return self.text

class AuthenticateMessage(MessageType):
    def __init__(self, slot):
        self.slot = slot

    def get_message(self):
        return f"75f4a4e10dd4b6b{self.slot}"

class ResetMessage(MessageType):
    def __init__(self, channel=None):
        self.channel = channel if channel != None else ""

    def get_message(self):
        return self.clean_msg(f"dds {self.channel} reset")

class AD9910RegisterWriteMessage(MessageType):
    def __init__(self, channel, register_name, value):
        self.channel = channel
        self.register_name = register_name
        self.value = value

    def get_message(self):
        """ Gets the message of the register write command
        """
        return self.clean_msg(f"dcp {self.channel} spi:{self.register_name}={self.value}")

class DCPRegisterWriteMessage(MessageType):
    def __init__(self, channel, register_name, value):
        self.channel = channel
        self.register_name = register_name
        self.value = value

    def get_message(self):
        """ Gets the message of the dcp register write command
        """
        return self.clean_msg(f"dcp {self.channel} wr:{self.register_name}={self.value}")

class WaitMessage(MessageType):
    def __init__(self, channel, wait_time_string, wait_event_string):
        self.channel = channel
        self.wait_time_string = wait_time_string
        self.wait_event_string = wait_event_string

    def get_message(self):
        """ Gets the message of the wait command
        """
        return self.clean_msg(f"dcp {self.channel} wait:{self.wait_time_string}:{self.wait_event_string}")

class UpdateMessage(MessageType):
    def __init__(self, channel=None, update_type="u"):
        self.channel = None
        if channel in [0, 1]:
            self.channel = channel

        # For reference on the update_type, see the documentation (can be u,o,d,h,p,a,b,c)
        self.update_type = update_type

    def get_message(self):
        """ Gets the messaeg of the update command
        """
        channel_string = self.channel if self.channel != None else ""
        return self.clean_msg(f"dcp {channel_string} update:{self.update_type}")

class VoltageToOutputMap:
    """
    This class is used for analog modulation, where we have to solve a system of linear
    equations. Using this class, we can give starting conditions.

    This class solves the following equations for s0, s1 and offset:
    out1 = (v1ch0 * s0 + v1ch1 * s1) / 2**12 + offset
    out2 = (v2ch0 * s0 + v2ch1 * s1) / 2**12 + offset
    out3 = (v3ch0 * s0 + v3ch1 * s1) / 2**12 + offset

    When doing analog modulation, we map analog voltages to output values depending on the
    type we want to modulate. So frequencies are in Hz, phases in rad and amplitudes from 0 to 1.
    (Actually, these values are FTW, POW and ASF, you probably don't have to use this function,
    but refer to the AD9910 datasheet if you're curious. It is the result from the *_to_word functions)
    This means out[N] is the output value given a input voltage v[N]ch0 at channel 0 and v[N]ch1
    at channel 1. If we know we only modulate on one channel, the set of equations reduce to 2 and
    consequently, the values for the other channel, as well as the variants for the third
    equation are ignored and do not have to be given.

    CAREFUL: When setting the output type to frequency, make sure to give the maximum
    reachable frequency as one of the output values, otherwise the result may not be what
    you expect!
    """
    class ChannelType(Enum):
        CH0_ONLY  = 1
        CH1_ONLY  = 2
        BOTH      = 3

    def __init__(self, use_outputs, output_type,
        v1ch0=0, v1ch1=0, out1=0,
        v2ch0=0, v2ch1=0, out2=0,
        v3ch0=0, v3ch1=0, out3=0):

        if not isinstance(use_outputs, VoltageToOutputMap.ChannelType):
            logging.error("use_outputs needs to be of type VoltageToOutputMap.ChannelType!")
            return -1

        if not isinstance(output_type, OutputType):
            logging.error("output_type needs to be of type OutputType!")
            return -1

        if output_type == OutputType.FREQUENCY:
            num = max(out1, out2, out3 or 0)
            num = (round(2**32/1e9*num) & 0xffff_ffff)
            self.min_gain_setting = int(np.ceil(np.log2(num)) - 16)
            out_fct = lambda x: (round(2**32/1e9*x) & 0xffff_ffff) >> self.min_gain_setting
        elif output_type == OutputType.PHASE:
            out_fct = lambda x: round(2**16 * x / 360)
        elif output_type == OutputType.AMPLITUDE:
            # TODO: MAKE SURE THIS IS CORRECT!
            out_fct = lambda x: round(max(0, min(0x3fff, 0x3fff*x))) << 2
        self.output_type = output_type

        volt_fct = lambda x: x*2**15 if x < 0 else (x*(2**15-1))

        self.use_outputs = use_outputs

        self.out1 = out_fct(out1)
        self.out2 = out_fct(out2)
        self.out3 = out_fct(out3)
        self.v1ch0 = volt_fct(v1ch0)
        self.v2ch0 = volt_fct(v2ch0)
        self.v3ch0 = volt_fct(v3ch0)
        self.v1ch1 = volt_fct(v1ch1)
        self.v2ch1 = volt_fct(v2ch1)
        self.v3ch1 = volt_fct(v3ch1)

    def get_eqn_parameters(self):
        """
        In accordance to the class description, this function solves the linear equations.

        Return values
        =============
        s0, s1, offset
        """
        A = self.out1
        B = self.v1ch0
        C = self.v1ch1

        D = self.out2
        E = self.v2ch0
        F = self.v2ch1

        G = self.out2
        H = self.v2ch0
        I = self.v2ch1

        if self.use_outputs == VoltageToOutputMap.ChannelType.CH0_ONLY:
            # x1 = offset * 2**12
            # y1 = out1 * 2**12 = v1ch0 * x0 + x1
            # y2 = out2 * 2**12 = v2ch0 * x0 + x1
            y = np.array([A, D])*2**12
            p = np.array([[B, 1], [E, 1]])
            x0, x1 = np.linalg.solve(p, y)
            s0 = x0
            offset = x1 * 2**-12
            s1 = 0

        elif self.use_outputs == VoltageToOutputMap.ChannelType.CH1_ONLY:
            # x1 = offset * 2**12
            # y1 = out1 * 2**12 = v1ch1 * x0 + x1
            # y2 = out2 * 2**12 = v2ch1 * x0 + x1
            y = np.array([A, D])*2**12
            p = np.array([[C, 1], [F, 1]])
            x0, x1 = np.linalg.solve(p, y)
            s1 = x0
            offset = x1 * 2**-12
            s0 = 0
        else:
            # x2 = offset * 2**12
            # y1 = out1 * 2**12 = v1ch0 * x0 + v1ch1 * x1 + x2
            # y2 = out2 * 2**12 = v2ch0 * x0 + v2ch1 * x1 + x2
            # y3 = out3 * 2**12 = v3ch0 * x0 + v3ch1 * x1 + x2
            y = np.array([A, D, G]) * 2**12
            p = np.array([[B, C, 1], [E, F, 1], [G, H, 1]])
            x0, x1, x2 = np.linalg.solve(p, y)
            s0 = x0
            s1 = x1
            offset = x2 * 2**-12

        return s0, s1, offset

class WieserlabsSlot:
    """
    A slot in the Wieserlab FlexDDS-NG holds 2 channels and also has some trigger inputs etc.
    We can talk to each sort via their own TCP port.

    This class should not have any methods and is only a storage for variables!
    """
    def __init__(self, index):
        """ index is the slot number that it appears in (looking at the front panel, the 0th
        slot is all the way on the left, 5 all the way on the right)"""
        self.index = index
        self.message_stack = []
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

        # The control function register is saved on the DDS, however we need to have
        # a local copy since we have to write it completely if we want to change
        # some bits. We have two CFRs for each channel
        self.cfr_regs = [[0x00410002, 0x004008C0], [0x00410002, 0x004008C0]]

        # I initially wanted to create the list as follows:
        # self.cfr_regs = [[0x00410002, 0x004008C0]] * 2

        # However then I found out, that using the asterisk operator creates multiple references
        # to the same list!
        # >>> a = 1
        # >>> b = 2
        # >>> v = [[a,b]] * 2
        # >>> v
        # [[1, 2], [1, 2]]
        # >>> v[0][0] = 3
        # >>> v
        # [[3, 2], [3, 2]]
        # For whomever it may be useful.


        # When changing settings of registers, we have to send an update for the
        # changes to take effect. However we don't want to spam update events
        # all over the place. Therefore, we use this list and only update,
        # whenever a register is overwritten. For example, we can write
        # into the stp0 register once and don't update (meaning we update
        # on the call of the run() function), but if we update it a second time
        # before calling run(), we have to send an update first!
        # When there are still registers to be updated when we call the
        # run() function, we run the update command as well.
        # We have a separate update queue for each channel
        self._update_queue = [[], []]

class WieserlabsClient:
    def __init__(self, ip_address, max_amp, loglevel):
        """
        This is a client written for the Wieserlabs DDS rack.
        It is a very versatile hardware and this is an attempt at covering at least the very basics.

        Before using this class, make sure to calibrate the output level of the DDS!
        Use the calibrate_amplitudes function (which is global in this file).
        This sets a maximum amplitude, single tone on all DDSs, which you can read out using a spectrum analyzer.
        The output amplitude can be changed using the potentiometer on the front panel of the slots. Set this
        to a preferred value and note down the peak amplitude. This is the value given into max_amp in dBm.
        """
        logging.root.level = loglevel
        
        self.ip_address = ip_address
        self.max_amp = max_amp

        self.slots = {}
        for i in range(0, 6):
            self.slots[i] = WieserlabsSlot(i)

        self._connect_all_slots()
        for slot in self.slots:
            self._reset_cfr(slot)

    def _validate_slot_channel(self, slot=None, channel=None):
        if channel != None:
            if channel != 0 and channel != 1:
                logging.error("Invalid channel number")
                return -1

        if slot != None:
            if slot < 0 or slot > 5:
                logging.error("Invalid slot value")
                return -1

        return 1

    def _send_receive(self, slot_index, msg):
        """ Send a message and receive the answer, the board should give an OK if the command worked,
            else it will print an error message (except for the authentication)
        """
        if msg.strip() == "":
            logging.warning("Trying to send empty message!")
            return

        logging.debug(f"\nSending message to slot {slot_index}:")
        # Format the message for pretty debugging here
        def format_msg(msg):
            debug_msg = ""
            maxchars = -1
            for i, line in enumerate(msg.split("\n")):
                maxchars = max(len(line), maxchars)
                line = f"|{i+1:>4}| {line}\n"
                debug_msg += line
            sep = "-"*(maxchars+7)
            debug_msg = f"{sep}\n{debug_msg}{sep}"
            print(debug_msg)

        if logging.root.level <= logging.DEBUG:
            format_msg(msg)


        socket = self.slots[slot_index].socket
        socket.sendall(bytes(msg+"\n", 'ascii'))
        data = socket.recv(1024)

        msg = data.decode('ascii').strip()
        logging.debug(f"Response:")
        if logging.root.level <= logging.DEBUG:
            format_msg(msg)

        if "error" in msg.lower():
            # TODO ?
            raise ValueError("TODO: IMPLEMENT ERROR MESSAGE")

    def _set_CFR_bit(self, slot_index, channel, cfr_number, bit_number, bit_value, send=False):
        """
        This is a super-super low-level function and should only be called by
        someone who knows what they are doing! Anyways, this sets bits in the
        control function registers of the AD9910, which are documented in the
        AD9910 datasheet.

        If send=False, we will only change the register stored in this program.
        """
        slot = self.slots[slot_index]

        if self._validate_slot_channel(slot_index, channel) == -1:
            return -1

        if cfr_number != 1 and cfr_number != 2:
            logging.error("Invalid value for cfr_number!")
            return -1

        if bit_number < 0 or bit_number > 31:
            logging.error("Invalid value for bit_number!")
            return -1

        if bit_value != 0 and bit_value != 1:
            logging.error("Invalid value for bit_value!")
            raise ValueError()
            return -1

        slot.cfr_regs[channel][cfr_number-1] = set_bit( slot.cfr_regs[channel][cfr_number-1],
                                                bit_number,
                                                bit_value)

        if send:
            val = f"{slot.cfr_regs[channel][cfr_number-1]:#0{10}x}"
            msg_stack = self.slots[slot_index].message_stack

            msg = AD9910RegisterWriteMessage(channel, f"CFR{cfr_number}", val)
            self.push_message(slot_index, msg)

    def _reset_cfr(self, slot_index):
        slot = self.slots[slot_index]

        slot.cfr_regs = [[0x00410002, 0x004008C0], [0x00410002, 0x004008C0]]
        for cfr_number in range(2):
            for channel in range(2):
                val = f"{slot.cfr_regs[channel][cfr_number]:#0{10}x}"
                msg = AD9910RegisterWriteMessage(channel, f"CFR{cfr_number+1}", val)
                self.push_message(slot_index, msg)

        self.run(slot_index)

    def _connect_all_slots(self):
        """ Connect to port 2600n, where n is card number (0 here = first card) """
        for slot in self.slots.values():
            server_address = (self.ip_address, 26000 + slot.index)
            logging.info(f"connecting to {server_address[0]} port {server_address[1]}")
            slot.socket.connect(server_address)
            logging.info("Connected")

            self._authenticate(slot.index)

    def _authenticate(self, slot_index):
        """Send in the authentication string. The last character is the card number"""
        assert(slot_index < 16)
        self.push_message(slot_index, AuthenticateMessage(slot_index))
        self.run(slot_index, no_update=True)

    def _get_stp0_value(self, freq, amp, phase):
        """ Generate the command to set the frequency
            Parameters:
                channel: 0 or 1, the channel on the slot
                freq: Frequency in Hz
                amp: The amplitude in dBm
        """

        amp_w = amp_to_word(amp)
        phase_w = phase_to_word(phase)
        freq_w = freq_to_word(freq)
        return f"0x{amp_w}_{phase_w}_{freq_w}"

    def push_update(self, slot_index, channel, update_type="u"):
        """
        Update the DDS, so that the changes take effect
        This function checks if the last message of this channel was an update.
        If it was, it won't push an update.
        """
        msg_stack = self.slots[slot_index].message_stack
        for msg in reversed(msg_stack):
            if not isinstance(msg, UpdateMessage):
                if msg.channel == None or msg.channel == channel:
                    # The last message in the stack for this channel (or both)
                    # is not an update, therefore we need to insert one
                    break
            else:
                if msg.channel != None and msg.channel != channel:
                    # Modify the last update to span both channels
                    msg.channel = None
                    return

                if msg.channel == None or msg.channel == channel:
                    # There is an update acting on both channels or this channel
                    return

        msg = UpdateMessage(channel)
        self.push_message(slot_index, msg)

    def push_message(self, slot_index, msg):
        if not isinstance(msg, MessageType):
            logging.error("Received an unidentified message! Ignoring call to push_message.")
            return -1

        slot = self.slots[slot_index]

        # If we are sending a register message, we need to check if we have to update first.
        # if isinstance(msg, DCPRegisterWriteMessage) or isinstance(msg, AD9910RegisterWriteMessage):
        #     push_or_pop(slot._update_queue[msg.channel],
        #                 msg.register_name,
        #                 lambda _: slot.message_stack.append(UpdateMessage(msg.channel, "u")))

        slot.message_stack.append(msg)

    def reset(self, slot_index, channel=None):
        """Reset the dds"""
        msg = ResetMessage()
        self.push_message(slot_index, msg)

    def single_tone(self, slot_index, channel, freq, amp, phase=0):
        """ Generate a single tone
            Parameters:
                slot: 0..5, the hardware slot used in the rack
                channel: 0 or 1, the channel on the slot
                freq: Frequency in Hz
                amp: The amplitude in dBm
                phase: The phase of the note in degrees (0..360)
        """

        # Make sure single tone amplitude control is on
        self._set_CFR_bit(slot_index, channel, 2, 24, 1)
        # and parallel data port is disabled
        self._set_CFR_bit(slot_index, channel, 2, 4, 0)
        # and ramp control is off
        self._set_CFR_bit(slot_index, channel, 2, 19, 0, send=True)

        # Generate the command
        # cmd = self._freq_command(channel, freq, amp, phase%360)
        reg_value = self._get_stp0_value(freq, amp, phase%360)

        # Push the command + update, in order to activate the DDS for this
        # single tone
        msg = AD9910RegisterWriteMessage(channel, "stp0", reg_value)
        self.push_message(slot_index, msg)

    def frequency_ramp(self, slot_index, channel, fstart, fend, amp,
        phase, tramp, fstep, is_filter=False):

        if fstart == fend:
            logging.error('fstart and fend cannot be the same!')
            return -1

        # Buckle up, here's a fun (hardware-side) bug for frequency ramps.
        # If we're driving a downward ramp, it only works when no-dwell is set to true.
        # Meaning that after the ramp is finished, we don't stay at the destination,
        # but jump to the inverse limit (downward ramp means we stay at the upward limit).
        # However, if we set no-dwell to false, the ramp doesn't work! We just jump
        # directly to the destination.
        # Now for the fun part: In order to drive the downward ramp and stay at that
        # value, we can instead drive an upward ramp from SYSCLK-fstart to SYSCLK-fend.
        # This mirrors the frequency around 500MHz and actually drives a downward ramp
        # from fstart to fend. Fun times indeed!
        if fend < fstart:
            fstart = 1e9 - fstart
            fend = 1e9 - fend

        up_ramp_limit = freq_to_word(max(fstart, fend))
        down_ramp_limit  = freq_to_word(min(fstart, fend))

        # We have to give the time after which to increase the frequency
        # by the fstep
        t_step_ns = fstep / abs(fstart - fend) * tramp * 1e9
        # DDS clock runs at 1/4 * f_SYSCLK, so 250MHz
        time_in_dds_clock = int(t_step_ns/4)

        if time_in_dds_clock > 0xffff:
            logging.error("Either tramp is too big or fstep.")
            return

        DRL = f"0x{up_ramp_limit}{down_ramp_limit}"
        DRSS = f"0x{freq_to_word(fstep)}{freq_to_word(fstep)}"
        DRR = f"0x{int(time_in_dds_clock):0{4}x}{int(time_in_dds_clock):0{4}x}"

        if not is_filter:
            # The following command is only needed to set the amplitude and phase
            self.single_tone(slot_index, channel, 0, amp, phase)

        self._clear_ramp_accumulator(slot_index, channel)

        if not is_filter:
            self._set_CFR_bit(slot_index, channel, 2, 19, 1) # enable ramp
            self._set_CFR_bit(slot_index, channel, 2, 20, 0) # set ramp to be a frequency ramp
            self._set_CFR_bit(slot_index, channel, 2, 21, 0, send=True) # set ramp to be a frequency ramp

        drl_msg = AD9910RegisterWriteMessage(channel, "DRL", DRL)
        drss_msg = AD9910RegisterWriteMessage(channel, "DRSS", DRSS)
        drr_msg = AD9910RegisterWriteMessage(channel, "DRR", DRR)

        # Due to the bug above, we only drive "upward ramps".
        # However in order to drive an upward ramp, we have to first
        # pretend that we are doing a downward ramp. This won't matter,
        # because directly after, we will do the actual upward ramp.
        # More fun!
        self.push_message(slot_index, drl_msg)
        self.push_message(slot_index, drss_msg)
        self.push_message(slot_index, drr_msg)

        if not is_filter:
            self.push_message(slot_index, UpdateMessage(channel, "u-d"))
            self.push_message(slot_index, UpdateMessage(channel, "u+d"))

    def _clear_ramp_accumulator(self, slot_index, channel):
        # Clear accumulator
        self._set_CFR_bit(slot_index, channel, 1, 12, 1, send=True)
        self.push_update(slot_index, channel)
        self._set_CFR_bit(slot_index, channel, 1, 12, 0, send=True)
        self.push_update(slot_index, channel)

    def phase_ramp(self, slot_index, channel, freq, amp, pstart,
        pend, tramp, pstep, keep_amplitude_for_hack=True, is_filter=False):
        """
        Start a phase ramp.

        Parameters
        ==========
        `slot_index`: Which card to talk to.
        `channel`: Which channel to talk to.
        `freq`: Frequency during the phase ramp.
        `amp`: Amplitude during the phase ramp.
        `pstart`: Start value of the phase ramp.
        `pend`: End value of the phase ramp.
        `tramp`: Ramp duration in nanoseconds.
        `pstep`: Step length for phase ramp (in general, you probably want this to be small).
        `keep_amplitude_for_hack`: See notes.

        Notes
        =====
        The variables `tramp` and `pstep` are both used to calculate the time
        after which the phase is increased by `pstep`. The formula for this is:
        $t_step_ns = pstep * tramp / |pstart - pend| * 1e9$.
        The resulting value cannot exceed 0xffff. If it does, we won't do the ramp
        and instead print an error.

        The variable `keep_amplitude_for_hack` exists, because on the AD9910,
        it is not possible to drive a downward ramp without driving an upward
        ramp first. Therefore, we do exactly that, drive an upward ramp first, to the
        starting point of the downward ramp. If `keep_amplitude_for_hack` is
        False, the upward ramp has amplitude zero, otherwise the amplitude during the
        upward ramp is simply `amplitude`.
        """

        # Here's a list of hacks we have to do to make everything work!
        # The digital ramp generator behaves really annoying.
        # 1. When ramping up to a phase, then trying to ramp up again, it won't work.
        #    Solution: It works, when we clear the DRCTL pin (by sending update:-d). Then we can do update:+d

        norm_pstart = (pstart%360) / 360
        norm_pend = (pend%360) / 360
        up_ramp_limit = round(max(norm_pstart, norm_pend) * 2**32)
        down_ramp_limit = round(min(norm_pstart, norm_pend) * 2**32)

        do_ramp_down = pstart > pend

        if not is_filter:
            if do_ramp_down:
                # https://ez.analog.com/dds/f/q-a/28177/ad9910-amplitude-drg-falling-ramp-starting-at-upper-limit
                self.phase_ramp(slot_index, channel, freq, int(keep_amplitude_for_hack) * amp,
                    0, pstart, 4, pstart)
            else:
                # Clear accumulator before running the ramp
                self._clear_ramp_accumulator(slot_index, channel)

        if norm_pstart == norm_pend:
            logging.error("pstart and pend cannot be the same!")
            return -1

        # We have to give the time after which to increase the phase
        # by the pstep
        t_step_ns = pstep / abs(pstart - pend) * tramp * 1e9
        # DDS clock runs at 1/4 * f_SYSCLK, so 250MHz
        time_in_dds_clock = int(t_step_ns/4)

        if time_in_dds_clock > 0xffff:
            logging.error("Either tramp_ns is too big or pstep.")
            return

        phase_step_format = f"{round(pstep*2**29/45):0{8}x}"

        DRL = f"0x{up_ramp_limit:0{8}x}{down_ramp_limit:0{8}x}"
        DRSS = f"0x{phase_step_format}{phase_step_format}"
        DRR = f"0x{int(time_in_dds_clock):0{4}x}{int(time_in_dds_clock):0{4}x}"

        if not is_filter:
            # The following command is only needed to set the frequency and amplitude
            self.single_tone(slot_index, channel, freq, amp, 0)

            self._set_CFR_bit(slot_index, channel, 2, 19, 1) # enable ramp
            self._set_CFR_bit(slot_index, channel, 2, 20, 1) # set ramp to be a phase ramp
            self._set_CFR_bit(slot_index, channel, 2, 21, 0, send=True) # set ramp to be a phase ramp

        drl_msg = AD9910RegisterWriteMessage(channel, "DRL", DRL)
        drss_msg = AD9910RegisterWriteMessage(channel, "DRSS", DRSS)
        drr_msg = AD9910RegisterWriteMessage(channel, "DRR", DRR)

        self.push_message(slot_index, drl_msg)
        self.push_message(slot_index, drss_msg)
        self.push_message(slot_index, drr_msg)

        if not is_filter:
            if do_ramp_down:
                # Yes, we have to separate it.
                self.push_message(slot_index, UpdateMessage(channel, f"u"))
                self.push_message(slot_index, UpdateMessage(channel, f"-d"))
            else:
                self.push_message(slot_index, UpdateMessage(channel, f"u-d"))
                self.push_message(slot_index, UpdateMessage(channel, f"+d"))

    def amplitude_ramp(self, slot_index, channel, freq, astart, aend,
        phase, tramp, astep, is_filter=False):
        """
        Start a phase ramp.

        Parameters
        ==========
        `slot_index`: Which card to talk to.
        `channel`: Which channel to talk to.
        `freq`: Frequency during the amplitude ramp.
        `astart`: Start value of the amplitude ramp.
        `aend`: Start value of the amplitude ramp.
        `phase`: Phase during the amplitude ramp.
        `tramp`: Ramp duration in nanoseconds.
        `astep`: Step length for amplitude ramp (in general, you probably want this to be small).

        Notes
        =====
        The variables `tramp` and `pstep` are both used to calculate the time
        after which the phase is increased by `pstep`. The formula for this is:
        $t_step_ns = astep * tramp / |astart - aend| * 1e9$.
        The resulting value cannot exceed 0xffff. If it does, we won't do the ramp
        and instead print an error.
        """

        # Here's a list of hacks we have to do to make everything work!
        # The digital ramp generator behaves really annoying.
        # 1. When ramping up to a amplitude, then trying to ramp up again, it won't work.
        #    Solution: It works, when we clear the DRCTL pin (by sending update:-d). Then we can do update:+d

        up_ramp_limit = round(max(astart, aend, 0) * (2**32-1))
        down_ramp_limit = round(min(astart, aend, 1) * (2**32-1))

        do_ramp_down = astart > aend

        if not is_filter:
            if do_ramp_down:
                # https://ez.analog.com/dds/f/q-a/28177/ad9910-amplitude-drg-falling-ramp-starting-at-upper-limit
                self.amplitude_ramp(slot_index, channel, freq, 0, astart, phase, 4, astart)
            else:
                # Clear accumulator before running the ramp
                self._clear_ramp_accumulator(slot_index, channel)


        if astart == aend:
            logging.error("astart and aend cannot be the same!")
            return -1

        # We have to give the time after which to increase the amp
        # by the pstep
        t_step_ns = astep / abs(astart - aend) * tramp * 1e9
        # DDS clock runs at 1/4 * f_SYSCLK, so 250MHz
        time_in_dds_clock = int(t_step_ns/4)

        if time_in_dds_clock > 0xffff:
            logging.error("Either tramp is too big or astep.")
            return

        amp_step_format = f"{round(astep*2**32):0{8}x}"

        DRL = f"0x{up_ramp_limit:0{8}x}{down_ramp_limit:0{8}x}"
        DRSS = f"0x{amp_step_format}{amp_step_format}"
        DRR = f"0x{int(time_in_dds_clock):0{4}x}{int(time_in_dds_clock):0{4}x}"

        if not is_filter:
            # The following command is only needed to set the frequency and phase
            self.single_tone(slot_index, channel, freq, 0, phase)

            self._set_CFR_bit(slot_index, channel, 2, 19, 1) # enable ramp
            self._set_CFR_bit(slot_index, channel, 2, 20, 0) # set ramp to be a phase ramp
            self._set_CFR_bit(slot_index, channel, 2, 21, 1, send=True) # set ramp to be a phase ramp

        drl_msg = AD9910RegisterWriteMessage(channel, "DRL", DRL)
        drss_msg = AD9910RegisterWriteMessage(channel, "DRSS", DRSS)
        drr_msg = AD9910RegisterWriteMessage(channel, "DRR", DRR)

        self.push_message(slot_index, drl_msg)
        self.push_message(slot_index, drss_msg)
        self.push_message(slot_index, drr_msg)

        if not is_filter:
            if do_ramp_down:
                # Yes, we have to separate it.
                self.push_message(slot_index, UpdateMessage(channel, f"u"))
                self.push_message(slot_index, UpdateMessage(channel, f"-d"))
            else:
                self.push_message(slot_index, UpdateMessage(channel, f"u-d"))
                self.push_message(slot_index, UpdateMessage(channel, f"+d"))

    def wait_time(self, slot_index, channel, t):
        t_ns = t * 1e9

        if t_ns <= 134 * 1e6:
            # For times less than 134ms, we can use the high resolution mode
            val = round(t_ns / 8)
            time_string = f"{val}h"
        else:
            val = round(t_ns / 1024)
            time_string = f"{val}"

        # For wait events, we always want to push an update to write the
        # registers! If we don't, the chip just waits without anything new
        # happening. It is however quite unintuitive, so we don't do it.
        # (Imaging the scenario: Setting a frequency, waiting, setting a different
        # frequency. Without the update, the chip does nothing, waits, then
        # sets the new frequency. The old frequency is never set).
        self.push_update(slot_index, channel)

        msg = WaitMessage(channel, time_string, "")
        self.push_message(slot_index, msg)

    def wait_trigger(self, slot_index, channel, trigger_events, timeout=-1):
        timeout_ns = timeout * 1e9

        if type(trigger_events) != list:
            trigger_events = [trigger_events]

        for ev in trigger_events:
            if type(ev) != TriggerEvent:
                logging.error("Didn't receive a valid TriggerEvent, abort!")
                return -1

        if timeout_ns > 0:
            if timeout_ns <= 134 * 1e6:
                # For times less than 134ms, we can use the high resolution mode
                val = round(timeout_ns / 8)
                time_string = f"{val}h"
            else:
                val = round(timeout_ns / 1024)
                time_string = f"{val}"
        else:
            time_string = ""

        trig_string = ",".join([str(ev.value) for ev in trigger_events])

        # See wait_time for why we are pushing an update here
        msg_stack = self.slots[slot_index].message_stack
        if len(msg_stack) > 0 and not isinstance(msg_stack[-1], UpdateMessage):
            self.push_update(slot_index, channel)

        msg = WaitMessage(channel, time_string, trig_string)
        self.push_message(slot_index, msg)

    def from_memory(self, slot_index, channel, param_type, storage,
        freq, amp, phase, tramp, ramp_filter=None):
        """
        Store waveforms in the RAM of the AD9910.

        Parameters:
        ===========
        param_type: Needs to be of RamParameterType. We can only store one parameter type
                    into the RAM at the same time.
        storage:    A list of parameter type (e.g. frequencies). Cannot be larger than 1024.
        """

        if not isinstance(param_type, RamParameterType):
            logging.error("param_type is not of type RamParameterType!")
            return -1

        try:
            storage = list(storage)
        except:
            logging.error("Cannot cast storage to a list!")

        if not isinstance(storage, list):
            logging.error("storage is not a list!")
            return -1

        if ramp_filter != None and not isinstance(ramp_filter, RamParameterType):
            logging.error("ramp_filter needs to be of type RamParameterType!")
            return -1

        # Have to invert the list because playback is back to front
        storage = storage[::-1]

        if len(storage) == 0:
            logging.error("storage is empty!")
            return -1
        elif len(storage) > 512:
            logging.error("We should be able to store 1024 values, however it seems \
            that we overflow the CPU or memory of the Wieserlabs DDS? Anyhow,\
            don't go above 512. If you have to, come back here and figure out\
            why it doesn't work (I found inconsistencies above 900, but 1024\
            definitely doesn't work)")
            return -1

        for s in storage:
            try:
                float(s)
            except:
                logging.error("something in storage can't be cast to float!")
                return -1

        retrv_freq = lambda x, shift: round(2**32/1e9*x) & 0xffff_ffff << shift
        retrv_phase = lambda x, shift: round(2**16 * (x%360) / 360) << shift
        retrv_amp = lambda x, shift: round(max(0, min(0x3fff, 0x3fff*x))) << shift
        if param_type == RamParameterType.FREQUENCY:
            retrv_fct = lambda x: retrv_freq(x, 0)
        elif param_type == RamParameterType.PHASE:
            retrv_fct = lambda x: retrv_phase(x, 16)
        elif param_type == RamParameterType.AMPLITUDE:
            retrv_fct = lambda x: retrv_amp(x, 18)
        elif param_type == RamParameterType.POLAR:
            logging.warning("This feature is not implemented yet!")
            return -1

        # Program freq, amp, phase
        val = f"0x{retrv_freq(freq, 0):0{8}x}"
        self.push_message(slot_index, AD9910RegisterWriteMessage(channel, "FTW", val))
        val = f"0x{retrv_amp(amp, 2):0{8}x}"
        self.push_message(slot_index, AD9910RegisterWriteMessage(channel, "ASF", val))
        val = f"0x{retrv_phase(phase, 0):0{4}x}"
        self.push_message(slot_index, AD9910RegisterWriteMessage(channel, "POW", val))
        # --------------------------------------------

        # Program the parameters of the RAM playback ----
        t_step = tramp / len(storage)

        step_rate = min(round((t_step * 1e9 / 4)), 0xffff) << 40
        end_idx = len(storage) << 30
        start_idx = 0 << 14
        no_dwell = 0 << 5
        ram_mode_control = 1 # ramp-up

        ram_register_fmt = step_rate | end_idx | start_idx | no_dwell | ram_mode_control
        msg = f"0x{ram_register_fmt:0{16}x}"
        self.push_message(slot_index, AD9910RegisterWriteMessage(channel, "stp0", msg))
        # ----------------------------------------------

        self.push_update(slot_index, channel, "=1p")
        self.push_update(slot_index, channel, "=0p")

        self._set_CFR_bit(slot_index, channel, 1, 29, get_bit(param_type.value, 0)) # set output type
        self._set_CFR_bit(slot_index, channel, 1, 30, get_bit(param_type.value, 1)) # set output type
        self._set_CFR_bit(slot_index, channel, 1, 31, 1, send=True) # enable RAM

        if ramp_filter != None:
            self._set_CFR_bit(slot_index, channel, 2, 19, 1) # enable ramp
            self._set_CFR_bit(slot_index, channel, 2, 20, get_bit(ramp_filter.value, 0)) # set ramp type
            self._set_CFR_bit(slot_index, channel, 2, 21, get_bit(ramp_filter.value, 1), send=True) # set ramp type

        self.push_message(slot_index, AD9910RegisterWriteMessage(channel, "RAMB", "0:c"))
        last_index = len(storage) // 2 - 1
        for i in range(len(storage) // 2):
            # We can store two values at the same time, therefore we retrieve two values from the storage
            first = retrv_fct(storage[i*2])
            second = retrv_fct(storage[i*2+1])
            val = f"0x{first:0{8}x}_{second:0{8}x}"

            if i != last_index:
                self.push_message(slot_index, AD9910RegisterWriteMessage(channel, "RAM64C", f"{val}:c"))
            else:
                if len(storage)%2 == 0:
                    self.push_message(slot_index, AD9910RegisterWriteMessage(channel, "RAM64E", f"{val}"))
                else:
                    # If we have an uneven number of values in storage, the last is actually the second
                    # to last, since we rounded the length down
                    self.push_message(slot_index, AD9910RegisterWriteMessage(channel, "RAM64C", f"{val}:c"))

                    last = retrv_fct(storage[-1])
                    val = f"0x{last:0{8}x}"
                    self.push_message(slot_index, AD9910RegisterWriteMessage(channel, "RAM64E", f"{val}"))
        self.push_update(slot_index, channel)

    def analog_modulation(self, slot_index, channel,
        voltage_to_output_map):
        """
        Do an analog modulation from an input that we define in voltage_to_output_map.

        We get the output type of the modulation from voltage_to_output_map

        Parameters:
        ===========
        voltage_to_output_map: The parameter is of type VoltageToOutputMap.
                               Using this, we define which voltage from the
                               analog input maps to the amplitude/frequency/phase on the
                               output.
        """

        if not isinstance(voltage_to_output_map, VoltageToOutputMap):
            logging.error("voltage_to_output_map needs to be of type VoltageToOutputMap!")

        s0, s1, offset = voltage_to_output_map.get_eqn_parameters()

        # if we are doing frequency modulation, we need to set the frequency gain
        # on the AD9910, since the analog input is 16bit, while the frequency
        # range is covered by 32bits.
        if voltage_to_output_map.output_type == OutputType.FREQUENCY:
            gain = voltage_to_output_map.min_gain_setting
            for i in range(4):
                self._set_CFR_bit(slot_index, channel, 2, i, get_bit(gain, i))
        # Make sure that the parallel data port is enabled (meaning, that the
        # AD9910 reads the analog input)
        self._set_CFR_bit(slot_index, channel, 2, 4, 1, send=True)

        msg_s0 = DCPRegisterWriteMessage(channel, "AM_S0", hex(round(s0)))
        msg_s1 = DCPRegisterWriteMessage(channel, "AM_S1", hex(round(s1)))

        # We set O0 and O1 to zero. These are supposed to correct for errors
        # in the DAC. However I don't know how to estimate these values
        # and I will assume for now, that it's not necessary.
        msg_offset_0 = DCPRegisterWriteMessage(channel, "AM_O0", 0)
        msg_offset_1 = DCPRegisterWriteMessage(channel, "AM_O1", 0)

        msg_offset_glob = DCPRegisterWriteMessage(channel, "AM_O", hex(round(offset)))

        # am_cfg is a 32bit register, however we only use the first two bits.
        # For amplitude modulation, these are 00
        # For phase modulation, these are 01
        # For frequency modulation, these are 10
        am_cfg = hex(set_bit(voltage_to_output_map.output_type.value, 29, 1))
        msg_mod_type = DCPRegisterWriteMessage(channel, "AM_CFG", am_cfg)

        # Push all messages that we just generated
        for m in [msg_s0, msg_s1, msg_offset_0, msg_offset_1, msg_offset_glob, msg_mod_type]:
            self.push_message(slot_index, m)

        # Force an update such that the changes are effective
        self.push_update(slot_index, channel)

    def run(self, slot_index, no_update=False):
        slot = self.slots[slot_index]

        if not no_update:
            # Add an update, just to be sure
            last_msg = (self.slots[slot_index].message_stack or [None])[-1]

            if not isinstance(last_msg, UpdateMessage):
                update_msg = UpdateMessage()
                self.push_message(slot_index, update_msg)
            else:
                # Sometimes, the last update is channel-specific, but we definitely
                # want to update all channels!
                last_msg.channel = ""

        payload = "\n".join([v.get_message() for v in slot.message_stack])
        self._send_receive(slot_index, payload)
        slot.message_stack.clear()
