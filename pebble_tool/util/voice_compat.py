"""Let libpebble2 read the session setups of watch microphone streams.

mic_stream_to_phone_start() opens a VoiceControl session of a type libpebble2's VoiceControlCommand
doesn't know, and libpebble2 fails to decode it, warning on every stream, e.g. in `pebble logs`.
Importing this module puts a parser in front of VoiceControlCommand that leaves dictation, which
`pebble transcribe` answers, to it.
"""

from libpebble2.protocol.base import PebblePacket
from libpebble2.protocol.base.types import DEFAULT_ENDIANNESS, BinaryArray, Uint8, Uint32
from libpebble2.protocol.voice import VoiceControlCommand

SESSION_SETUP = 0x01
SESSION_TYPE_AUDIO_STREAM = 0x04


class AudioStreamSessionSetup(PebblePacket):
    """A VoiceControl message for a microphone stream; the phone (pypkjs) answers these."""
    class Meta:
        endpoint = 0x2af8
        endianness = '<'

    command = Uint8()
    flags = Uint32()
    payload = BinaryArray()

    @classmethod
    def parse(cls, message, default_endianness=DEFAULT_ENDIANNESS):
        # command u8, flags u32, then the session type.
        if len(message) > 5 and message[0] == SESSION_SETUP and message[5] == SESSION_TYPE_AUDIO_STREAM:
            return super(AudioStreamSessionSetup, cls).parse(message, default_endianness)
        return VoiceControlCommand.parse(message, default_endianness)
