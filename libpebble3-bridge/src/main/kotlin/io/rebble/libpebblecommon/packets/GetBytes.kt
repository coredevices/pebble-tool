package io.rebble.libpebblecommon.packets

import io.rebble.libpebblecommon.protocolhelpers.PacketRegistry
import io.rebble.libpebblecommon.protocolhelpers.PebblePacket
import io.rebble.libpebblecommon.protocolhelpers.ProtocolEndpoint
import io.rebble.libpebblecommon.structmapper.SBytes
import io.rebble.libpebblecommon.structmapper.SUByte
import io.rebble.libpebblecommon.structmapper.SUInt

sealed class GetBytesOutgoingPacket(command: GetBytesCommand, transactionId: UByte) :
    PebblePacket(ProtocolEndpoint.GET_BYTES) {

    val command = SUByte(m, command.value)
    val transactionId = SUByte(m, transactionId)
}

/**
 * public static final byte CMD_ID_REQUEST_CORE_DUMP = 0;
 *     public static final byte CMD_ID_REQUEST_PFS_FILE = 3;
 *     /** Only return a core dump if it is unread */
 *     public static final byte CMD_ID_REQUEST_NEW_CORE_DUMP = 5;
 */
enum class GetBytesCommand(val value: UByte) {
    REQUEST_CORE_DUMP(0u),
    REQUEST_PFS_FILE(3u),
    REQUEST_NEW_CORE_DUMP(5u),
}

class GetBytesCoreDump(unread: Boolean, transactionId: UByte) : GetBytesOutgoingPacket(
    command = when (unread) {
        true -> GetBytesCommand.REQUEST_NEW_CORE_DUMP
        false -> GetBytesCommand.REQUEST_CORE_DUMP
    },
    transactionId = transactionId,
)

enum class GetBytesResponse(val value: UByte) {
    IMAGE_INFO(1u),
    IMAGE_DATA(2u),
}

enum class GetBytesError(val code: UByte) {
    NO_ERROR(0x00u),
    MALFORMED_REQUEST(0x01u),
    ALREADY_IN_PROGRESS(0x02u),
    IMAGE_DOES_NOT_EXIST(0x03u),
    IMAGE_CORRUPT(0x04u),
    UNKNOWN_ERROR(0x99u)
    ;

    companion object {
        fun fromCode(code: UByte): GetBytesError =
            entries.firstOrNull { it.code == code } ?: UNKNOWN_ERROR
    }
}

sealed class GetBytesInboundMessage : PebblePacket(ProtocolEndpoint.GET_BYTES) {
    class GetBytesImageInfo : GetBytesInboundMessage() {
        val response = SUByte(m)
        val transactionId = SUByte(m)
        val errorCode = SUByte(m)
        val numBytes = SUInt(m)
    }

    class GetBytesImageData : GetBytesInboundMessage() {
        val response = SUByte(m)
        val transactionId = SUByte(m)
        val offset = SUInt(m)
        val data = SBytes(m, allRemainingBytes = true)
    }
}

fun getBytesIncomingPacketsRegister() {
    PacketRegistry.register(
        ProtocolEndpoint.GET_BYTES,
        GetBytesResponse.IMAGE_INFO.value,
    ) { GetBytesInboundMessage.GetBytesImageInfo() }

    PacketRegistry.register(
        ProtocolEndpoint.GET_BYTES,
        GetBytesResponse.IMAGE_DATA.value,
    ) { GetBytesInboundMessage.GetBytesImageData() }
}


