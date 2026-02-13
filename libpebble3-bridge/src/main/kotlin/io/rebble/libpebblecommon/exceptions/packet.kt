package io.rebble.libpebblecommon.exceptions

class PacketEncodeException(message: String?) : Exception(message)
class PacketDecodeException(message: String?, cause: Exception? = null) : Exception(message, cause)