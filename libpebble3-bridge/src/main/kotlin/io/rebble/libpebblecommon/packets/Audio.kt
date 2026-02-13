package io.rebble.libpebblecommon.packets

import io.rebble.libpebblecommon.protocolhelpers.PacketRegistry
import io.rebble.libpebblecommon.protocolhelpers.PebblePacket
import io.rebble.libpebblecommon.protocolhelpers.ProtocolEndpoint
import io.rebble.libpebblecommon.structmapper.*
import io.rebble.libpebblecommon.util.Endian

/**
 * Audio streaming packet. Little endian.
 */
sealed class AudioStream(command: Command, sessionId: UShort = 0u) : PebblePacket(ProtocolEndpoint.AUDIO_STREAMING) {
    val command = SUByte(m, command.value)
    val sessionId = SUShort(m, sessionId, endianness = Endian.Little)

    class EncoderFrame : StructMappable() {
        val data = SUnboundBytes(m)
    }

    class DataTransfer : AudioStream(AudioStream.Command.DataTransfer) {
        val frameCount = SUByte(m)
        val frames = SFixedList(m, 0) {
            EncoderFrame()
        }
        init {
            frames.linkWithCount(frameCount)
        }
    }

    class StopTransfer(sessionId: UShort = 0u) : AudioStream(AudioStream.Command.StopTransfer, sessionId)

    enum class Command(val value: UByte) {
        DataTransfer(0x02u),
        StopTransfer(0x03u)
    }
}

fun audioStreamPacketsRegister() {
    PacketRegistry.register(ProtocolEndpoint.AUDIO_STREAMING, AudioStream.Command.DataTransfer.value) {
        AudioStream.DataTransfer()
    }
    PacketRegistry.register(ProtocolEndpoint.AUDIO_STREAMING, AudioStream.Command.StopTransfer.value) {
        AudioStream.StopTransfer()
    }
}