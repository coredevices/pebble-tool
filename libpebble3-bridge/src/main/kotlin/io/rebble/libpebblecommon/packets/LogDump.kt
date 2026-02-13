package io.rebble.libpebblecommon.packets

import io.rebble.libpebblecommon.protocolhelpers.PacketRegistry
import io.rebble.libpebblecommon.protocolhelpers.PebblePacket
import io.rebble.libpebblecommon.protocolhelpers.ProtocolEndpoint
import io.rebble.libpebblecommon.structmapper.*

open class LogDump(val message: Message): PebblePacket(ProtocolEndpoint.LOG_DUMP) {
    val command = SUByte(m, message.value)

    init {
        type = command.get()
    }

    enum class Message(val value: UByte) {
        RequestLogDump(0x10u),
        LogLine(0x80u),
        Done(0x81u),
        NoLogs(0x82u)
    }

    class RequestLogDump(logGeneration: UByte, cookie: UInt): LogDump(Message.RequestLogDump) {
        val generation = SUByte(m, logGeneration)
        val cookie = SUInt(m, cookie)
    }

    sealed class ReceivedLogDumpMessage(message: Message): LogDump(message) {
        val cookie = SUInt(m)
    }

    class LogLine: ReceivedLogDumpMessage(Message.LogLine) {
        val timestamp = SUInt(m)
        val level = SUByte(m)
        val length = SUByte(m)
        val line = SUShort(m)
        val filename = SFixedString(m, 16)
        val messageText = SFixedString(m, 0)

        init {
            messageText.linkWithSize(length)
        }
    }

    class Done: ReceivedLogDumpMessage(Message.Done)

    class NoLogs: ReceivedLogDumpMessage(Message.NoLogs)
}

fun logDumpPacketsRegister() {
    PacketRegistry.register(ProtocolEndpoint.LOG_DUMP, LogDump.Message.NoLogs.value) {
        LogDump.NoLogs()
    }

    PacketRegistry.register(ProtocolEndpoint.LOG_DUMP, LogDump.Message.Done.value) {
        LogDump.Done()
    }

    PacketRegistry.register(ProtocolEndpoint.LOG_DUMP, LogDump.Message.LogLine.value) {
        LogDump.LogLine()
    }
}