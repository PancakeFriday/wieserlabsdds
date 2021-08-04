import socket
import sys
import time
from enum import Enum

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

# We need to convert a frequency to DDS compatible language
def freq_to_word(f):
    # f in Hz
    if f < 0 or f >= 1e9:
        print("freq needs to be in range [0,1e9)")
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
        return self.clean_msg(f"dcp {self.channel} wr:{self.register_name}={self.register_value}")

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
        self.channel = channel or ""
        # For reference on the update_type, see the documentation (can be u,o,d,h,p,a,b,c)
        self.update_type = update_type

    def get_message(self):
        """ Gets the messaeg of the update command
        """
        return self.clean_msg(f"dcp {self.channel} update:{self.update_type}")

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

        self.slots = {}
        for i in range(0, 1):
            self.slots[i] = WieserlabsSlot(i)

        self._connect_all_slots()
        for slot in self.slots:
            self._reset_cfr(slot)

    def _validate_slot_channel(self, slot=None, channel=None):
        if channel != None:
            if channel != 0 and channel != 1:
                print("[ERROR]: Invalid channel number")
                return -1

        if slot != None:
            if slot < 0 or slot > 5:
                print("[ERROR]: Invalid slot value")
                return -1

        return 1

    def _send_receive(self, slot_index, msg):
        """ Send a message and receive the answer, the board should give an OK if the command worked,
            else it will print an error message (except for the authentication)
        """
        if msg.strip() == "":
            print("[WARNING]: Trying to send empty message!")
            return

        print(f"\nSending message to slot {slot_index}:")
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
        format_msg(msg)


        socket = self.slots[slot_index].socket
        socket.sendall(bytes(msg+"\n", 'ascii'))
        data = socket.recv(1024)

        msg = data.decode('ascii').strip()
        print(f"Response:")
        format_msg(msg)
        if "error" in msg.lower():
            # TODO ?
            raise ValueError()

    def _set_CFR_bit(self, slot, channel, cfr_number, bit_number, bit_value, send=False):
        """
        This is a super-super low-level function and should only be called by
        someone who knows what they are doing! Anyways, this sets bits in the
        control function registers of the AD9910, which are documented in the
        AD9910 datasheet.

        If send=False, we will only change the register stored in this program.
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

        self._cfr_regs[cfr_number-1] = set_bit( self._cfr_regs[cfr_number-1],
                                                bit_number,
                                                bit_value)

        if send:
            val = f"{self._cfr_regs[cfr_number-1]:#0{10}x}"
            msg = AD9910RegisterWriteMessage(channel, f"CFR{cfr_number}", val)
            # msg = f"dcp {channel} spi:CFR{cfr_number}={val}"
            self.push_message(slot, msg)

    def _reset_cfr(self, slot):
        self._cfr_regs = [0x00410002, 0x004008C0]
        for cfr_number in range(2):
            for channel in range(2):
                val = f"{self._cfr_regs[cfr_number]:#0{10}x}"
                msg = AD9910RegisterWriteMessage(channel, f"CFR{cfr_number+1}", val)
                # msg = f"dcp {channel} spi:CFR{cfr_number+1}={val}"
                self.push_message(slot, msg)
        self.run(slot)

    def _connect_all_slots(self):
        """ Connect to port 2600n, where n is card number (0 here = first card) """
        for slot in self.slots.values():
            server_address = (self.ip_address, 26000 + slot.index)
            print(f"connecting to {server_address[0]} port {server_address[1]}")
            slot.socket.connect(server_address)
            print("Connected")

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

    def push_update(self, slot_index, channel):
        """ Update the DDS, so that the changes take effect
        """
        msg = UpdateMessage(channel, "u")
        self.push_message(slot_index, msg)

    def push_message(self, slot_index, msg):
        if not isinstance(msg, MessageType):
            print("[ERROR]: Received an unidentified message! Ignoring call to push_message.")
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
        client._set_CFR_bit(slot_index, channel, 2, 24, 1)
        # and ramp control is off
        client._set_CFR_bit(slot_index, channel, 2, 19, 0, send=True)

        # Generate the command
        # cmd = self._freq_command(channel, freq, amp, phase%360)
        reg_value = self._get_stp0_value(freq, amp, phase%360)

        # Push the command + update, in order to activate the DDS for this
        # single tone
        msg = AD9910RegisterWriteMessage(channel, "stp0", reg_value)
        self.push_message(slot_index, msg)

    def frequency_ramp(self, slot, channel, fstart, fend, amp,
        phase, tramp_ns, fstep):

        if fstart == fend:
            print('[ERROR]: fstart and fend cannot be the same!')
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
        t_step_ns = fstep / abs(fstart - fend) * tramp_ns
        # DDS clock runs at 1/4 * f_SYSCLK, so 250MHz
        time_in_dds_clock = int(t_step_ns/4)

        if time_in_dds_clock > 0xffff:
            print("[ERROR]: Either tramp_ns is too big or fstep.")
            return

        DRL = f"0x{up_ramp_limit}{down_ramp_limit}"
        DRSS = f"0x{freq_to_word(fstep)}{freq_to_word(fstep)}"
        DRR = f"0x{int(time_in_dds_clock):0{4}x}{int(time_in_dds_clock):0{4}x}"

        # The following command is only needed to set the amplitude and phase
        self.single_tone(slot, channel, 0, amp, phase)

        self._clear_ramp_accumulator(slot, channel)

        self._set_CFR_bit(slot, channel, 2, 19, 1) # enable ramp
        self._set_CFR_bit(slot, channel, 2, 20, 0) # set ramp to be a frequency ramp
        self._set_CFR_bit(slot, channel, 2, 21, 0, send=True) # set ramp to be a frequency ramp

        drl_msg = AD9910RegisterWriteMessage(channel, "DRL", DRL)
        drss_msg = AD9910RegisterWriteMessage(channel, "DRSS", DRSS)
        drr_msg = AD9910RegisterWriteMessage(channel, "DRR", DRR)

        # Due to the bug above, we only drive "upward ramps".
        # However in order to drive an upward ramp, we have to first
        # pretend that we are doing a downward ramp. This won't matter,
        # because directly after, we will do the actual upward ramp.
        # More fun!
        self.push_message(slot, drl_msg)
        self.push_message(slot, drss_msg)
        self.push_message(slot, drr_msg)
        self.push_message(slot, UpdateMessage(channel, "u-d"))
        self.push_message(slot, UpdateMessage(channel, "u+d"))

    def _clear_ramp_accumulator(self, slot, channel):
        # Clear accumulator
        self._set_CFR_bit(slot, channel, 1, 12, 1, send=True)
        self.push_update(slot, channel)
        self._set_CFR_bit(slot, channel, 1, 12, 0, send=True)
        self.push_update(slot, channel)

    def phase_ramp(self, slot, channel, freq, amp, pstart,
        pend, tramp_ns, pstep, keep_amplitude_for_hack=True):
        """
        Start a phase ramp.

        Parameters
        ==========
        `slot`: Which card to talk to.
        `channel`: Which channel to talk to.
        `freq`: Frequency during the phase ramp.
        `amp`: Amplitude during the phase ramp.
        `pstart`: Start value of the phase ramp.
        `pend`: End value of the phase ramp.
        `tramp_ns`: Ramp duration in nanoseconds.
        `pstep`: Step length for phase ramp (in general, you probably want this to be small).
        `keep_amplitude_for_hack`: See notes.

        Notes
        =====
        The variables `tramp_ns` and `pstep` are both used to calculate the time
        after which the phase is increased by `pstep`. The formula for this is:
        $t_step_ns = pstep * tramp_ns / |pstart - pend|$.
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

        if do_ramp_down:
            # https://ez.analog.com/dds/f/q-a/28177/ad9910-amplitude-drg-falling-ramp-starting-at-upper-limit
            self.phase_ramp(slot, channel, freq, int(keep_amplitude_for_hack) * amp,
                0, pstart, 4, pstart)
        else:
            # Clear accumulator before running the ramp
            self._clear_ramp_accumulator(slot, channel)


        if norm_pstart == norm_pend:
            print("[ERROR]: pstart and pend cannot be the same!")
            return -1

        # We have to give the time after which to increase the phase
        # by the pstep
        t_step_ns = pstep / abs(pstart - pend) * tramp_ns
        # DDS clock runs at 1/4 * f_SYSCLK, so 250MHz
        time_in_dds_clock = int(t_step_ns/4)

        if time_in_dds_clock > 0xffff:
            print("[ERROR]: Either tramp_ns is too big or pstep.")
            return

        phase_step_format = f"{round(pstep*2**29/45):0{8}x}"

        DRL = f"0x{up_ramp_limit:0{8}x}{down_ramp_limit:0{8}x}"
        DRSS = f"0x{phase_step_format}{phase_step_format}"
        DRR = f"0x{int(time_in_dds_clock):0{4}x}{int(time_in_dds_clock):0{4}x}"

        # The following command is only needed to set the frequency and amplitude
        self.single_tone(slot, channel, freq, amp, 0)

        self._set_CFR_bit(slot, channel, 2, 19, 1) # enable ramp
        self._set_CFR_bit(slot, channel, 2, 20, 1) # set ramp to be a phase ramp
        self._set_CFR_bit(slot, channel, 2, 21, 0, send=True) # set ramp to be a phase ramp

        drl_msg = AD9910RegisterWriteMessage(channel, "DRL", DRL)
        drss_msg = AD9910RegisterWriteMessage(channel, "DRSS", DRSS)
        drr_msg = AD9910RegisterWriteMessage(channel, "DRR", DRR)

        self.push_message(slot, drl_msg)
        self.push_message(slot, drss_msg)
        self.push_message(slot, drr_msg)

        if do_ramp_down:
            # Yes, we have to separate it.
            self.push_message(slot, UpdateMessage(channel, f"u"))
            self.push_message(slot, UpdateMessage(channel, f"-d"))
        else:
            self.push_message(slot, UpdateMessage(channel, f"u-d"))
            self.push_message(slot, UpdateMessage(channel, f"+d"))

    def amplitude_ramp(self, slot, channel, freq, astart, aend,
        phase, tramp_ns, astep):
        """
        Start a phase ramp.

        Parameters
        ==========
        `slot`: Which card to talk to.
        `channel`: Which channel to talk to.
        `freq`: Frequency during the amplitude ramp.
        `astart`: Start value of the amplitude ramp.
        `aend`: Start value of the amplitude ramp.
        `phase`: Phase during the amplitude ramp.
        `tramp_ns`: Ramp duration in nanoseconds.
        `astep`: Step length for amplitude ramp (in general, you probably want this to be small).

        Notes
        =====
        The variables `tramp_ns` and `pstep` are both used to calculate the time
        after which the phase is increased by `pstep`. The formula for this is:
        $t_step_ns = astep * tramp_ns / |astart - aend|$.
        The resulting value cannot exceed 0xffff. If it does, we won't do the ramp
        and instead print an error.
        """

        # Here's a list of hacks we have to do to make everything work!
        # The digital ramp generator behaves really annoying.
        # 1. When ramping up to a amplitude, then trying to ramp up again, it won't work.
        #    Solution: It works, when we clear the DRCTL pin (by sending update:-d). Then we can do update:+d

        up_ramp_limit = round(max(astart, aend, 0) * 2**32)
        down_ramp_limit = round(min(astart, aend, 1) * 2**32)

        do_ramp_down = astart > aend

        if do_ramp_down:
            # https://ez.analog.com/dds/f/q-a/28177/ad9910-amplitude-drg-falling-ramp-starting-at-upper-limit
            self.amplitude_ramp(slot, channel, freq, 0, astart, phase, 4, astart)
        else:
            # Clear accumulator before running the ramp
            self._clear_ramp_accumulator(slot, channel)


        if astart == aend:
            print("[ERROR]: astart and aend cannot be the same!")
            return -1

        # We have to give the time after which to increase the amp
        # by the pstep
        t_step_ns = astep / abs(astart - aend) * tramp_ns
        # DDS clock runs at 1/4 * f_SYSCLK, so 250MHz
        time_in_dds_clock = int(t_step_ns/4)

        if time_in_dds_clock > 0xffff:
            print("[ERROR]: Either tramp_ns is too big or astep.")
            return

        amp_step_format = f"{round(astep*2**32):0{8}x}"

        DRL = f"0x{up_ramp_limit:0{8}x}{down_ramp_limit:0{8}x}"
        DRSS = f"0x{amp_step_format}{amp_step_format}"
        DRR = f"0x{int(time_in_dds_clock):0{4}x}{int(time_in_dds_clock):0{4}x}"

        # The following command is only needed to set the frequency and amplitude
        self.single_tone(slot, channel, freq, 0, phase)

        self._set_CFR_bit(slot, channel, 2, 19, 1) # enable ramp
        self._set_CFR_bit(slot, channel, 2, 20, 0) # set ramp to be a phase ramp
        self._set_CFR_bit(slot, channel, 2, 21, 1, send=True) # set ramp to be a phase ramp

        drl_msg = AD9910RegisterWriteMessage(channel, "DRL", DRL)
        drss_msg = AD9910RegisterWriteMessage(channel, "DRSS", DRSS)
        drr_msg = AD9910RegisterWriteMessage(channel, "DRR", DRR)

        self.push_message(slot, drl_msg)
        self.push_message(slot, drss_msg)
        self.push_message(slot, drr_msg)

        if do_ramp_down:
            # Yes, we have to separate it.
            self.push_message(slot, UpdateMessage(channel, f"u"))
            self.push_message(slot, UpdateMessage(channel, f"-d"))
        else:
            self.push_message(slot, UpdateMessage(channel, f"u-d"))
            self.push_message(slot, UpdateMessage(channel, f"+d"))

    def wait_time(self, slot_index, channel, t_ns):
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
        msg_stack = self.slots[slot_index].message_stack
        if len(msg_stack) > 0 and not isinstance(msg_stack[-1], UpdateMessage):
            self.push_update(slot_index, channel)

        msg = WaitMessage(channel, time_string, "")
        self.push_message(slot_index, msg)

    def wait_trigger(self, slot_index, channel, trigger_events, timeout_ns=-1):
        if type(trigger_events) != list:
            trigger_events = [trigger_events]

        for ev in trigger_events:
            if type(ev) != TriggerEvent:
                print("[ERROR] Didn't receive a valid TriggerEvent, abort!")
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

    def run(self, slot_index, no_update=False):
        slot = self.slots[slot_index]

        if not no_update:
            # Add an update, just to be sure
            last_msg = self.slots[slot_index].message_stack[-1]
            if not isinstance(last_msg, UpdateMessage):
                update_msg = UpdateMessage()
                self.push_message(slot_index, update_msg)

        payload = "\n".join([v.get_message() for v in slot.message_stack])
        self._send_receive(slot_index, payload)
        slot.message_stack.clear()

client = WieserlabsClient("10.0.0.237", max_amp=17.38)
client.reset(0)
client.run(0)

client.single_tone(0, 0, 1e6, 0.1, 0)
client.single_tone(0, 1, 1e6, 0.1, 0)
client.wait_time(0, 0, 1e9)
client.wait_time(0, 1, 1e9)

sweep_time = 0.5e9

from random import random
previous_amp = 0.1
previous_f = 1e6
previous_phase = 0
for i in range(30):
    r = random()
    if r < 0.33333:
        random_amp = random() * 0.2
        print(f"amp -> {random_amp}")
        client.amplitude_ramp(0, 0, previous_f, previous_amp, random_amp, previous_phase, sweep_time, 1e-8)
        previous_amp = random_amp
    elif r < 0.666666:
        random_f = random()*2e6 + 1e6
        print(f"f -> {random_f}")
        client.frequency_ramp(0, 0, previous_f, random_f, previous_amp, previous_phase, sweep_time, 1)
        client.frequency_ramp(0, 1, previous_f, random_f, 0.1, 0, sweep_time, 1)
        previous_f = random_f
    else:
        random_phase = random() * 360
        print(f"phase -> {random_phase}")
        client.phase_ramp(0, 0, previous_f, previous_amp, previous_phase, random_phase, sweep_time, 1e-5)
        previous_phase = random_phase
    client.wait_time(0, 0, sweep_time + 1e9)
    client.wait_time(0, 1, sweep_time + 1e9)


client.run(0)
