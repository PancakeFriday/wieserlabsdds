# Wieserlabs DDS python library

This packages was programmed for the [WL-FlexDDS-NG](hhttps://www.wieserlabs.com/products/radio-frequency-generators/WL-FlexDDS-NGttp:// "WL-FlexDDS-NG"). For usage, see the examples in the examples folder. Most common use-cases should be:
 - Generation of a single tone
 - Generation of frequency / intensity / phase ramps
 - Arbitrary function generator -like functionality using the RAM of the AD9910 (although it only has enough memory for 1024 datapoints)

## Fundamentals

The user of this library doesn't have to know much about the internals of the hardware and the processing. However it is still good to have an overview, just to be safe. For more detailed information, please refer to the [WL-FlexDDS-NG manual](http://https://www.wieserlabs.com/products/radio-frequency/flexdds-ng/FlexDDS-NG_Manual.pdf "WL-FlexDDS-NG manual") and the [AD9910 manual](https://www.analog.com/media/en/technical-documentation/data-sheets/AD9910.pdfhttp:// "AD9910 manual") for even more insight.

When calling a function of this library, the program generates a series of commands, which are sent to the DDS processor. These are in most cases configuration commands for the internal registers of the AD9910 (e.g. setting a frequency and amplitude), as well as setting operating modes etc. Some commands are processed on a higher level, these are for example wait commands.
When calling the constructor of the WieserlabsClient, you can pass in the optional loglevel parameter. If it is set to logging.DEBUG, it is possible to see the commands sent to the DDS.

## API

The most important member functions of WieserlabsClient are documented in the following. The parameters slot_index and channel are not documented, as they are the same for all functions. The slot refers to the card number in the WL-FlexDDS-NG (0..5) and the channel to either 0 or 1, which is the channel on the card.

#### push_update(slot_index, channel, update_type)

Use this function to force an update, such that changes made to configuration take effect. This normally does not have to be called by the user. If it does have to be called, it might be indicative of a bug in the code.

`update_type (optional):` The update types are documented in the WL-FlexDDS-NG manual on page 17

#### push_message(slot_index, msg)

Send a message to the DDS rack.

`msg:` Has to be of type `MessageType`. The child classes should be self-explanatory. If not, messages can further be identified in the WL-FlexDDS-NG manual from page 15 onwards.

#### reset(slot_index)

Reset the DDS slot to the default state. Should be done once after initialization.

#### single_tone(slot_index, channel, freq, amp, phase)

Generate a single tone of frequency `freq`, amplitude `amp` and phase `phase`. Note that phase 0 does not mean that the generated function starts on the zero crossing (as would be expected for a sine). However the phase is simply the relation between the two channels. They are always in a defined state relative to each other, however the exact relation has to be found by calibrating the outputs beforehand.

#### frequency_ramp(slot_index, channel, fstart, fend, amp, phase, tramp, fstep, is_filter)

Generate a frequency ramp.

`fstart:` The starting frequency of the ramp
`fend:` The final frequency of the ramp
`amp:` The amplitude during the ramp
`phase:` The phase during the ramp
`tramp:` The time how long the ramp is going to take
`fstep:` The stepsize of the frequency during the ramp. If the ramp does not work, this value might either be too small or too big.
`is_filter:` If this value evaluates to True, then the ramp is not exectued, however it is programmed and can then be later used in memory mode to e.g. drive two ramps at the same time.

#### amplitude_ramp(slot_index, channel, freq, astart, aend, phase, tramp, fstep, is_filter)

Generate an amplitude ramp

`freq:` The frequency during the ramp
`astart:` The starting amplitude of the ramp
`aend:` The final amplitude of the ramp
`phase:` The phase during the ramp
`tramp:` The time how long the ramp is going to take
`fstep:` The stepsize of the frequency during the ramp. If the ramp does not work, this value might either be too small or too big.
`is_filter:` If this value evaluates to True, then the ramp is not exectued, however it is programmed and can then be later used in memory mode to e.g. drive two ramps at the same time.

#### phase_ramp(slot_index, channel, freq, amp, pstart, pend, tramp, fstep, is_filter)

Generate an amplitude ramp

`freq:` The frequency during the ramp
`amp:` The amplitude during the ramp
`pstart:` The starting phase of the ramp
`pend:` The final phase of the ramp
`tramp:` The time how long the ramp is going to take
`fstep:` The stepsize of the frequency during the ramp. If the ramp does not work, this value might either be too small or too big.
`is_filter:` If this value evaluates to True, then the ramp is not exectued, however it is programmed and can then be later used in memory mode to e.g. drive two ramps at the same time.

#### wait_time(slot_index, channel, t)

The next instruction on the given `channel` will we paused until the time `t` has passed.

#### wait_trigger(slot_index, channel, trigger_events, timeout)

The next instruction on the given `channel` will we paused until the `trigger_events` have happened or the timeout has occured.

`trigger_events:` Has to be of type TriggerEvent. See help(TriggerEvent) for a list of possible trigger events. This parameter can either be a list or a single event.
`timeout:` The amount of time to wait for the trigger. The default value `-1` waits forever.

#### from_memory(slot_index, channel, param_type, storage, freq, amp, phase, tramp, ramp_filter)

Load a signal from the internal AD9910 memory. One of the `frequency`, `amplitude` or `phase` parameters are loaded from the `storage` parameter and played back during `tramp`.

`param_type:` Needs to be of type `RamParameterType` and defines the type of parameter that is defined in the `storage`.
`storage:` A list (or an object that can be cast to a list) of values (e.g. frequencies). Can be no longer than 1024 (at the moment no longer than 512, due to the hardware crashing for high values).
`freq:` The frequency during playback
`amp:` The amplitude during playback
`phase:` The phase during playback
`tramp:` The time over which the playback occurs
`ramp_filter:` If a ramp was previously loaded with `is_filter=True`, then this value can be set and defines the type of ramp that was previously defined. Needs to be of type `RamParameterType`.

#### analog_modulation(slot_index, channel, voltage_to_output_map)

Generate a modulated signal based on an analog input. Hereby it is best to have the analog signal go from -1V to +1V amplitude. Moreover, the signal that is modulated has to be defined beforehand, for example call `single_tone` before calling this function.

`voltage_to_output_map:` Has to be of type VoltageToOutputMap. The constructor takes the parameter `use_outputs`, which defines if the modulated signal is generated from channel 0, channel 1 or both channels. In the case of both channels all of the next nine parameters have to be provided. `output_type` defines the type of output, similar to `param_type` in `from_memory`, however this time it has to be of type `VoltageToOutputMap.ChannelType`. The values `vXchY` refer to the voltage of parameter number X of channel Y. So the first parameter of channel 0 is `v1ch0`. Similarly, `outX` refer to the output of parameter number X. Therefore we can create a map, e.g. voltage -1 in channel 0 refers to 50% amplitude, voltage 1V refers to 80% amplitude, then `v1ch0=-1`, `out1=0.5` and `v2ch0=1`, `out2=0.8`.

#### run(slot_index, no_update)

Start the playback of the commands programmed into the DDS.

`no_update:` Wether or not to send an update before running. This should be `False` in pretty much every application.
