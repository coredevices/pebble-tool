package io.rebble.libpebblecommon.structmapper

import io.rebble.libpebblecommon.util.DataBuffer
import io.rebble.libpebblecommon.util.Endian

abstract class StructMappable(endianness: Endian = Endian.Unspecified) : Mappable(endianness) {
    val m = StructMapper(endianness = endianness, debugTag = this::class.simpleName)

    override fun toBytes(): UByteArray {
        return m.toBytes()
    }

    override fun fromBytes(bytes: DataBuffer) {
        m.fromBytes(bytes)
    }

    override val size get() = m.size
}