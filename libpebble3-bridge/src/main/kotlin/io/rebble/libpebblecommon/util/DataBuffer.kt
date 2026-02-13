package io.rebble.libpebblecommon.util

import java.nio.ByteBuffer
import java.nio.ByteOrder

class DataBuffer {
    private val actualBuf: ByteBuffer

    constructor(size: Int) {
        actualBuf = ByteBuffer.allocate(size)
    }

    constructor(bytes: UByteArray) {
        actualBuf = ByteBuffer.wrap(bytes.toByteArray())
    }

    val length: Int
        get() = actualBuf.capacity()

    val readPosition: Int
        get() = actualBuf.position()

    val remaining: Int
        get() = actualBuf.remaining()

    fun putUShort(short: UShort) {
        actualBuf.putShort(short.toShort())
    }

    fun getUShort(): UShort = actualBuf.short.toUShort()

    fun putShort(short: Short) {
        actualBuf.putShort(short)
    }

    fun getShort(): Short = actualBuf.short

    fun putUByte(byte: UByte) {
        actualBuf.put(byte.toByte())
    }

    fun getUByte(): UByte = actualBuf.get().toUByte()

    fun putByte(byte: Byte) {
        actualBuf.put(byte)
    }

    fun getByte(): Byte = actualBuf.get()

    fun putBytes(bytes: UByteArray) {
        actualBuf.put(bytes.toByteArray())
    }

    fun getBytes(count: Int): UByteArray {
        val tBuf = ByteArray(count)
        actualBuf.get(tBuf)
        return tBuf.toUByteArray()
    }

    fun array(): UByteArray = actualBuf.array().toUByteArray()

    fun setEndian(endian: Endian) {
        when (endian) {
            Endian.Big -> actualBuf.order(ByteOrder.BIG_ENDIAN)
            Endian.Little -> actualBuf.order(ByteOrder.LITTLE_ENDIAN)
            Endian.Unspecified -> actualBuf.order(ByteOrder.BIG_ENDIAN)
        }
    }

    fun putUInt(uint: UInt) {
        actualBuf.putInt(uint.toInt())
    }

    fun getUInt(): UInt = actualBuf.int.toUInt()

    fun putInt(int: Int) {
        actualBuf.putInt(int)
    }

    fun getInt(): Int = actualBuf.int

    fun putULong(ulong: ULong) {
        actualBuf.putLong(ulong.toLong())
    }

    fun getULong(): ULong = actualBuf.long.toULong()

    fun rewind() {
        actualBuf.rewind()
    }
}

fun DataBuffer.putBytes(bytes: ByteArray) = putBytes(bytes.asUByteArray())
