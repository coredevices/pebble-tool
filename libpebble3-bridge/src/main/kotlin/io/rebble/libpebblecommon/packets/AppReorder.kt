package io.rebble.libpebblecommon.packets

import io.rebble.libpebblecommon.protocolhelpers.PacketRegistry
import io.rebble.libpebblecommon.protocolhelpers.PebblePacket
import io.rebble.libpebblecommon.protocolhelpers.ProtocolEndpoint
import io.rebble.libpebblecommon.structmapper.*
import kotlin.uuid.Uuid

class AppReorderResult() :
    PebblePacket(ProtocolEndpoint.APP_REORDER) {
    /**
     * Result code. See [AppOrderResultCode].
     */
    val status = SUByte(m)

}

sealed class AppReorderOutgoingPacket(type: AppReorderType) :
    PebblePacket(ProtocolEndpoint.APP_REORDER) {

    /**
     * Packet type. See [AppReorderType].
     */
    val command = SUByte(m, type.value)
}

enum class AppOrderResultCode(val value: UByte) {
    SUCCESS(0x01u),
    FAILED(0x02u),
    INVALID(0x03u),
    RETRY(0x04u);

    companion object {
        fun fromByte(value: UByte): AppOrderResultCode {
            return values().firstOrNull { it.value == value } ?: error("Unknown result: $value")
        }
    }
}


/**
 * Packet sent from the watch when user opens an app that is not in the watch storage.
 */
class AppReorderRequest(
    appList: List<Uuid>
) : AppReorderOutgoingPacket(AppReorderType.REORDER_APPS) {
    val appCount = SByte(m, appList.size.toByte())

    val appList = SFixedList<SUUID>(
        mapper = m,
        count = appList.size,
        default = appList.map { SUUID(StructMapper(), it) },
        itemFactory = {
            SUUID(
                StructMapper()
            )
        })
}

enum class AppReorderType(val value: UByte) {
    REORDER_APPS(0x01u)
}


fun appReorderIncomingRegister() {
    PacketRegistry.register(
        ProtocolEndpoint.APP_REORDER,
    ) { AppReorderResult() }
}