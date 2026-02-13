package io.rebble.libpebblecommon.packets

import io.rebble.libpebblecommon.protocolhelpers.PacketRegistry
import io.rebble.libpebblecommon.protocolhelpers.PebblePacket
import io.rebble.libpebblecommon.protocolhelpers.ProtocolEndpoint
import io.rebble.libpebblecommon.structmapper.*

class AppLogShippingControlMessage(enable: Boolean) : PebblePacket(ProtocolEndpoint.APP_LOGS) {
    private val enable = SBoolean(m, enable)
}

class AppLogReceivedMessage() : PebblePacket(ProtocolEndpoint.APP_LOGS) {
    val uuid = SUUID(m)
    val timestamp = SUInt(m)
    val level = SUByte(m)
    val messageLength = SUByte(m)
    val lineNumber = SUShort(m)
    val filename = SFixedString(m, 16)
    val message = SFixedString(m, 0)

    init {
        message.linkWithSize(messageLength)
    }
}

fun appLogPacketsRegister() {
    PacketRegistry.register(ProtocolEndpoint.APP_LOGS) { AppLogReceivedMessage() }
}