package io.rebble.libpebblecommon.bridge

import io.rebble.libpebblecommon.packets.*
import io.rebble.libpebblecommon.protocolhelpers.PebblePacket
import io.rebble.libpebblecommon.protocolhelpers.ProtocolEndpoint
import io.rebble.libpebblecommon.structmapper.SUShort
import io.rebble.libpebblecommon.structmapper.StructMapper
import io.rebble.libpebblecommon.util.DataBuffer
import java.io.InputStream
import java.io.OutputStream
import java.net.Socket
import java.util.zip.ZipFile
import kotlin.system.exitProcess
import kotlin.uuid.Uuid

/**
 * libpebble3-bridge: A CLI tool that connects to QEMU Pebble emulator
 * and provides app installation and log streaming using the libpebble3 protocol layer.
 *
 * Communication with QEMU uses QemuSPP framing:
 *   [0xFEED][protocol=1][length][Pebble Protocol Packet][0xBEEF]
 *
 * Usage:
 *   libpebble3-bridge install <qemu_port> <pbw_path> <platform>
 *   libpebble3-bridge logs <qemu_port>
 *   libpebble3-bridge install-and-logs <qemu_port> <pbw_path> <platform>
 */

const val QEMU_HEADER = 0xFEED
const val QEMU_FOOTER = 0xBEEF
const val QEMU_PROTOCOL_SPP: UShort = 1u

fun main(args: Array<String>) {
    if (args.isEmpty()) {
        printUsage()
        exitProcess(1)
    }

    when (args[0]) {
        "install" -> {
            if (args.size < 4) {
                System.err.println("Usage: libpebble3-bridge install <qemu_port> <pbw_path> <platform>")
                exitProcess(1)
            }
            val port = args[1].toInt()
            val pbwPath = args[2]
            val platform = args[3]
            val bridge = QemuBridge(port)
            bridge.connect()
            bridge.negotiate()
            bridge.installApp(pbwPath, platform)
            bridge.disconnect()
        }
        "logs" -> {
            if (args.size < 2) {
                System.err.println("Usage: libpebble3-bridge logs <qemu_port>")
                exitProcess(1)
            }
            val port = args[1].toInt()
            val bridge = QemuBridge(port)
            bridge.connect()
            bridge.negotiate()
            bridge.streamLogs()
        }
        "install-and-logs" -> {
            if (args.size < 4) {
                System.err.println("Usage: libpebble3-bridge install-and-logs <qemu_port> <pbw_path> <platform>")
                exitProcess(1)
            }
            val port = args[1].toInt()
            val pbwPath = args[2]
            val platform = args[3]
            val bridge = QemuBridge(port)
            bridge.connect()
            bridge.negotiate()
            bridge.installApp(pbwPath, platform)
            bridge.streamLogs()
        }
        "ping" -> {
            if (args.size < 2) {
                System.err.println("Usage: libpebble3-bridge ping <qemu_port>")
                exitProcess(1)
            }
            val port = args[1].toInt()
            val bridge = QemuBridge(port)
            bridge.connect()
            bridge.negotiate()
            bridge.sendPing()
            bridge.disconnect()
        }
        else -> {
            printUsage()
            exitProcess(1)
        }
    }
}

fun printUsage() {
    System.err.println("""
        libpebble3-bridge - Pebble emulator bridge using libpebble3 protocol

        Commands:
          install <qemu_port> <pbw_path> <platform>    Install app on emulator
          logs <qemu_port>                               Stream logs from emulator
          install-and-logs <qemu_port> <pbw_path> <platform>  Install and stream logs
          ping <qemu_port>                               Test connectivity
    """.trimIndent())
}

class QemuBridge(private val port: Int) {
    private lateinit var socket: Socket
    private lateinit var input: InputStream
    private lateinit var output: OutputStream
    @Volatile private var connected = false

    fun connect() {
        System.err.println("[libpebble3-bridge] Connecting to QEMU on localhost:$port...")
        socket = Socket("localhost", port)
        socket.tcpNoDelay = true
        input = socket.getInputStream()
        output = socket.getOutputStream()
        connected = true
        System.err.println("[libpebble3-bridge] Connected.")
    }

    fun disconnect() {
        connected = false
        socket.close()
    }

    /**
     * Send a Pebble Protocol packet wrapped in QemuSPP framing
     */
    fun sendPacket(packet: PebblePacket) {
        val ppBytes = packet.serialize()
        sendRawPP(ppBytes)
    }

    fun sendRawPP(ppBytes: UByteArray) {
        // QemuSPP frame: [0xFEED][protocol=1][length][payload][0xBEEF]
        val length = ppBytes.size.toUShort()
        val frame = StructMapper()
        val sig = SUShort(frame, QEMU_HEADER.toUShort())
        val proto = SUShort(frame, QEMU_PROTOCOL_SPP)
        val len = SUShort(frame, length)
        val header = frame.toBytes()

        val footer = StructMapper()
        val foot = SUShort(footer, QEMU_FOOTER.toUShort())
        val footerBytes = footer.toBytes()

        val fullFrame = header.toByteArray() + ppBytes.toByteArray() + footerBytes.toByteArray()
        synchronized(output) {
            output.write(fullFrame)
            output.flush()
        }
    }

    /**
     * Read a QemuSPP frame from the socket and return the Pebble Protocol payload
     */
    fun readPacket(): PebblePacket? {
        val ppBytes = readRawPP() ?: return null
        return try {
            PebblePacket.deserialize(ppBytes)
        } catch (e: Exception) {
            System.err.println("[libpebble3-bridge] Failed to decode packet: ${e.message}")
            null
        }
    }

    fun readRawPP(): UByteArray? {
        // Read QemuSPP header: 2 (sig) + 2 (protocol) + 2 (length) = 6 bytes
        val headerBytes = readExact(6) ?: return null
        val headerBuf = DataBuffer(headerBytes.toUByteArray())
        val sig = headerBuf.getUShort()
        val proto = headerBuf.getUShort()
        val length = headerBuf.getUShort()

        if (sig != QEMU_HEADER.toUShort()) {
            System.err.println("[libpebble3-bridge] Invalid QEMU header: 0x${sig.toString(16)}")
            return null
        }

        if (proto != QEMU_PROTOCOL_SPP) {
            // Non-SPP protocol, skip payload + footer
            readExact(length.toInt() + 2)
            return null
        }

        // Read payload
        val payload = readExact(length.toInt()) ?: return null

        // Read footer
        val footerBytes = readExact(2) ?: return null

        return payload.toUByteArray()
    }

    private fun readExact(count: Int): ByteArray? {
        val buf = ByteArray(count)
        var offset = 0
        while (offset < count) {
            val read = input.read(buf, offset, count - offset)
            if (read < 0) return null
            offset += read
        }
        return buf
    }

    /**
     * Perform the Pebble Protocol version negotiation handshake.
     *
     * The firmware sends PhoneVersionRequest during boot, but by the time
     * the bridge connects (after boot), the request is already lost (QEMU TCP
     * discards data when nobody is connected). So we:
     * 1. Drain any queued packets from the firmware
     * 2. Send PhoneVersionResponse proactively
     * 3. Send WatchVersionRequest and wait for WatchVersionResponse
     */
    fun negotiate() {
        System.err.println("[libpebble3-bridge] Starting protocol negotiation...")

        // Brief pause to let firmware settle, then drain queued packets
        Thread.sleep(500)
        socket.soTimeout = 500
        var drainCount = 0
        try {
            while (true) {
                val packet = readPacket()
                if (packet != null) {
                    drainCount++
                    System.err.println("[libpebble3-bridge] Drained: endpoint=${packet.endpoint}")
                    // If we catch the PhoneVersionRequest, great
                    if (packet.endpoint == ProtocolEndpoint.PHONE_VERSION) {
                        System.err.println("[libpebble3-bridge] Got PhoneVersionRequest!")
                    }
                    // Respond to PING immediately
                    if (packet is PingPong) {
                        respondToPing(packet)
                    }
                } else {
                    break
                }
            }
        } catch (_: java.net.SocketTimeoutException) {
            // Expected - no more data queued
        }
        System.err.println("[libpebble3-bridge] Drained $drainCount queued packets")

        // Reset to blocking mode
        socket.soTimeout = 0

        // Send PhoneVersionResponse proactively (matching libpebble2's values)
        val phoneVersionResponse = PhoneAppVersion.AppVersionResponse(
            protocolVersion = UInt.MAX_VALUE,
            sessionCaps = 0x80000000u,
            platformFlags = PhoneAppVersion.PlatformFlag.makeFlags(
                PhoneAppVersion.OSType.Linux,
                listOf(
                    PhoneAppVersion.PlatformFlag.SMS,
                    PhoneAppVersion.PlatformFlag.GPS,
                    PhoneAppVersion.PlatformFlag.BTLE
                )
            ),
            responseVersion = 2u,
            majorVersion = 4u,
            minorVersion = 4u,
            bugfixVersion = 2u,
            protocolCaps = UByteArray(8) { 0xFFu }
        )
        sendPacket(phoneVersionResponse)
        System.err.println("[libpebble3-bridge] Sent PhoneVersionResponse")

        // Send WatchVersionRequest
        val watchVersionReq = WatchVersion.WatchVersionRequest()
        sendPacket(watchVersionReq)
        System.err.println("[libpebble3-bridge] Sent WatchVersionRequest")

        // Wait for WatchVersionResponse (handle other packets while waiting)
        val timeout = java.lang.System.currentTimeMillis() + 10_000
        socket.soTimeout = 10_000
        while (java.lang.System.currentTimeMillis() < timeout) {
            try {
                val packet = readPacket()
                if (packet != null) {
                    if (packet.endpoint == ProtocolEndpoint.WATCH_VERSION) {
                        if (packet is WatchVersion.WatchVersionResponse) {
                            System.err.println("[libpebble3-bridge] Watch firmware: ${packet.running.versionTag.get()}")
                        }
                        System.err.println("[libpebble3-bridge] Negotiation complete.")
                        socket.soTimeout = 0
                        return
                    }
                    // Handle PING during negotiation
                    if (packet is PingPong) {
                        respondToPing(packet)
                    }
                    System.err.println("[libpebble3-bridge] During negotiation: endpoint=${packet.endpoint}")
                }
            } catch (_: java.net.SocketTimeoutException) {
                break
            }
        }
        socket.soTimeout = 0
        System.err.println("[libpebble3-bridge] WARNING: No WatchVersionResponse received, continuing...")
    }

    /**
     * Respond to a PING packet with PONG.
     */
    private fun respondToPing(ping: PingPong) {
        val pong = PingPong.Pong(cookie = ping.cookie.get())
        sendPacket(pong)
    }

    /**
     * Install a PBW app on the emulator using the modern (fw >= 3) install flow:
     * 1. Parse app metadata from binary header
     * 2. Insert metadata into BlobDB App database
     * 3. Send AppRunStateStart to trigger app launch
     * 4. Wait for AppFetchRequest from watch (contains app_id for PutBytes)
     * 5. Send AppFetchResponse(START)
     * 6. PutBytes binary (and resources/worker if present) using watch-assigned app_id
     */
    fun installApp(pbwPath: String, platform: String) {
        System.err.println("[libpebble3-bridge] Installing app from $pbwPath for platform $platform...")

        val zipFile = ZipFile(pbwPath)

        // Read the app binary from the PBW
        val binaryEntry = zipFile.getEntry("$platform/pebble-app.bin")
            ?: throw RuntimeException("No binary found for platform $platform in PBW")
        val appBinary = zipFile.getInputStream(binaryEntry).readBytes()

        // Read app resources if present
        val resourceEntry = zipFile.getEntry("$platform/app_resources.pbpack")
        val appResources = resourceEntry?.let { zipFile.getInputStream(it).readBytes() }

        // Read worker if present
        val workerEntry = zipFile.getEntry("$platform/pebble-worker.bin")
        val workerBinary = workerEntry?.let { zipFile.getInputStream(it).readBytes() }

        // Parse app metadata from binary header
        val meta = parseAppHeader(appBinary)
        System.err.println("[libpebble3-bridge] App: ${meta.appName} UUID: ${meta.uuid}")

        // Enable app log shipping
        enableAppLogShipping()

        // Step 1: Insert app metadata into BlobDB
        insertAppIntoBlobDB(meta)

        // Step 2: Send AppRunStateStart to trigger the app
        System.err.println("[libpebble3-bridge] Sending AppRunStateStart...")
        val startMsg = AppRunStateMessage.AppRunStateStart(meta.uuid)
        sendPacket(startMsg)

        // Step 3: Wait for AppFetchRequest from the watch
        System.err.println("[libpebble3-bridge] Waiting for AppFetchRequest...")
        var appId: UInt = 1u
        val fetchTimeout = java.lang.System.currentTimeMillis() + 15_000
        while (java.lang.System.currentTimeMillis() < fetchTimeout) {
            val packet = readPacket() ?: continue
            if (packet is AppFetchRequest) {
                appId = packet.appId.get()
                val fetchUuid = packet.uuid.get()
                System.err.println("[libpebble3-bridge] AppFetchRequest: uuid=$fetchUuid, appId=$appId")

                // Step 4: Send AppFetchResponse(START)
                val fetchResponse = AppFetchResponse(AppFetchResponseStatus.START)
                sendPacket(fetchResponse)
                System.err.println("[libpebble3-bridge] Sent AppFetchResponse(START)")
                break
            }
            handleBackgroundPacket(packet)
        }

        // Step 5: PutBytes - send binary using watch-assigned app_id
        System.err.println("[libpebble3-bridge] Sending app binary (${appBinary.size} bytes, appId=$appId)...")
        putBytesTransfer(appBinary.toUByteArray(), ObjectType.APP_EXECUTABLE, appId)

        // Send resources if present
        if (appResources != null) {
            System.err.println("[libpebble3-bridge] Sending app resources (${appResources.size} bytes)...")
            putBytesTransfer(appResources.toUByteArray(), ObjectType.APP_RESOURCE, appId)
        }

        // Send worker if present
        if (workerBinary != null) {
            System.err.println("[libpebble3-bridge] Sending worker binary (${workerBinary.size} bytes)...")
            putBytesTransfer(workerBinary.toUByteArray(), ObjectType.WORKER, appId)
        }

        println("App install succeeded.")
        System.err.println("[libpebble3-bridge] App installed successfully.")
        zipFile.close()
    }

    /**
     * Parse the Pebble app binary header to extract metadata.
     */
    private data class AppHeader(
        val uuid: Uuid,
        val appName: String,
        val flags: UInt,
        val iconResourceId: UInt,
        val appVersionMajor: UByte,
        val appVersionMinor: UByte,
        val sdkVersionMajor: UByte,
        val sdkVersionMinor: UByte,
    )

    private fun parseAppHeader(binary: ByteArray): AppHeader {
        val buf = java.nio.ByteBuffer.wrap(binary).order(java.nio.ByteOrder.LITTLE_ENDIAN)
        // Header layout (little-endian):
        // 0-7: sentinel "PBLAPP\0\0" (8 bytes)
        // 8-9: struct version major, minor
        // 10-11: sdk version major, minor
        // 12-13: app version major, minor
        // 14-15: size (uint16)
        // 16-19: offset (uint32)
        // 20-23: crc (uint32)
        // 24-55: app name (32 bytes, null-terminated)
        // 56-87: company name (32 bytes, null-terminated)
        // 88-91: icon resource id (uint32)
        // 92-95: symbol table addr (uint32)
        // 96-99: flags (uint32)
        // 100-103: num relocation entries (uint32)
        // 104-119: uuid (16 bytes, big-endian)

        val sdkVersionMajor = binary[10].toUByte()
        val sdkVersionMinor = binary[11].toUByte()
        val appVersionMajor = binary[12].toUByte()
        val appVersionMinor = binary[13].toUByte()

        val appNameBytes = binary.sliceArray(24 until 56)
        val appName = String(appNameBytes).trimEnd('\u0000')

        buf.position(88)
        val iconResourceId = buf.int.toUInt()
        buf.position(96)
        val flags = buf.int.toUInt()

        val uuid = Uuid.fromByteArray(binary.sliceArray(104 until 120))

        return AppHeader(
            uuid = uuid,
            appName = appName,
            flags = flags,
            iconResourceId = iconResourceId,
            appVersionMajor = appVersionMajor,
            appVersionMinor = appVersionMinor,
            sdkVersionMajor = sdkVersionMajor,
            sdkVersionMinor = sdkVersionMinor,
        )
    }

    /**
     * Insert app metadata into BlobDB App database.
     */
    private fun insertAppIntoBlobDB(meta: AppHeader) {
        System.err.println("[libpebble3-bridge] Inserting app into BlobDB...")

        val appMetadata = io.rebble.libpebblecommon.packets.blobdb.AppMetadata(
            uuid = meta.uuid,
            flags = meta.flags,
            icon = meta.iconResourceId,
            appVersionMajor = meta.appVersionMajor,
            appVersionMinor = meta.appVersionMinor,
            sdkVersionMajor = meta.sdkVersionMajor,
            sdkVersionMinor = meta.sdkVersionMinor,
            appFaceBgColor = 0u,
            appFaceTemplateId = 0u,
            appName = meta.appName,
        )

        // Serialize the app metadata as the BlobDB value
        val metadataBytes = appMetadata.toBytes()

        // Key is the UUID bytes
        val uuidBytes = meta.uuid.toByteArray().toUByteArray()

        // Create BlobDB insert command
        val token = (java.lang.System.currentTimeMillis() % 65536).toUShort()
        val insertCmd = io.rebble.libpebblecommon.packets.blobdb.BlobCommand.InsertCommand(
            token = token,
            database = coredev.BlobDatabase.App,
            key = uuidBytes,
            value = metadataBytes,
        )
        sendPacket(insertCmd)

        // Wait for BlobDB response
        val timeout = java.lang.System.currentTimeMillis() + 10_000
        while (java.lang.System.currentTimeMillis() < timeout) {
            val packet = readPacket() ?: continue
            if (packet is io.rebble.libpebblecommon.packets.blobdb.BlobResponse) {
                val status = packet.responseValue
                System.err.println("[libpebble3-bridge] BlobDB response: $status")
                if (status != io.rebble.libpebblecommon.packets.blobdb.BlobResponse.BlobStatus.Success) {
                    System.err.println("[libpebble3-bridge] WARNING: BlobDB insert returned $status (continuing anyway)")
                }
                return
            }
            handleBackgroundPacket(packet)
        }
        System.err.println("[libpebble3-bridge] WARNING: No BlobDB response received (continuing)")
    }

    /**
     * Transfer data via PutBytes protocol (init -> put chunks -> commit -> install).
     * Uses STM32 CRC-32 algorithm (not standard IEEE CRC-32).
     */
    private fun putBytesTransfer(data: UByteArray, objectType: ObjectType, appId: UInt) {
        // Send PutBytesAppInit
        val initPacket = PutBytesAppInit(
            objectSize = data.size.toUInt(),
            objectType = objectType,
            appId = appId
        )
        sendPacket(initPacket)

        // Wait for ACK with cookie
        val initResponse = waitForPutBytesResponse()
            ?: throw RuntimeException("No response to PutBytes init")
        if (!initResponse.isAck) {
            throw RuntimeException("PutBytes init NACK'd")
        }
        val cookie = initResponse.cookie.get()
        System.err.println("[libpebble3-bridge] PutBytes init OK, cookie=$cookie")

        // Send data in chunks
        val chunkSize = 2000
        var offset = 0
        while (offset < data.size) {
            val end = minOf(offset + chunkSize, data.size)
            val chunk = data.sliceArray(offset until end)
            val putPacket = PutBytesPut(cookie, chunk)
            sendPacket(putPacket)

            val putResponse = waitForPutBytesResponse()
                ?: throw RuntimeException("No response to PutBytes put at offset $offset")
            if (!putResponse.isAck) {
                throw RuntimeException("PutBytes put NACK'd at offset $offset")
            }
            offset = end
        }

        // Commit with STM32 CRC-32
        val crcValue = stm32Crc32(data.toByteArray())
        val commitPacket = PutBytesCommit(cookie, crcValue)
        sendPacket(commitPacket)

        val commitResponse = waitForPutBytesResponse()
            ?: throw RuntimeException("No response to PutBytes commit")
        if (!commitResponse.isAck) {
            throw RuntimeException("PutBytes commit NACK'd")
        }

        // Install
        val installPacket = PutBytesInstall(cookie)
        sendPacket(installPacket)

        val installResponse = waitForPutBytesResponse()
            ?: throw RuntimeException("No response to PutBytes install")
        if (!installResponse.isAck) {
            throw RuntimeException("PutBytes install NACK'd")
        }
        System.err.println("[libpebble3-bridge] PutBytes transfer complete for $objectType")
    }

    /**
     * STM32 CRC-32 implementation (different from standard IEEE CRC-32).
     * Uses polynomial 0x04C11DB7, processes data in 32-bit words.
     */
    private fun stm32Crc32(data: ByteArray): UInt {
        var crc = 0xFFFFFFFFu
        val poly = 0x04C11DB7u

        // Process in 32-bit words (little-endian byte order)
        val wordCount = (data.size + 3) / 4
        for (i in 0 until wordCount) {
            val offset = i * 4
            var word = 0u
            for (b in 0 until 4) {
                if (offset + b < data.size) {
                    word = word or (data[offset + b].toUByte().toUInt() shl (b * 8))
                }
            }

            crc = crc xor word
            for (bit in 0 until 32) {
                crc = if ((crc and 0x80000000u) != 0u) {
                    (crc shl 1) xor poly
                } else {
                    crc shl 1
                }
            }
        }

        return crc and 0xFFFFFFFFu
    }

    private fun waitForPutBytesResponse(): PutBytesResponse? {
        val timeout = java.lang.System.currentTimeMillis() + 20_000
        while (java.lang.System.currentTimeMillis() < timeout) {
            val packet = readPacket() ?: continue
            if (packet is PutBytesResponse) {
                return packet
            }
            handleBackgroundPacket(packet)
        }
        return null
    }

    private fun handleBackgroundPacket(packet: PebblePacket) {
        when (packet.endpoint) {
            ProtocolEndpoint.APP_LOGS -> {
                if (packet is AppLogReceivedMessage) {
                    val timestamp = java.time.LocalTime.now().toString().substring(0, 8)
                    println("[$timestamp] ${packet.filename.get()}:${packet.lineNumber.get()}> ${packet.message.get()}")
                }
            }
            ProtocolEndpoint.PING -> {
                if (packet is PingPong) {
                    respondToPing(packet)
                }
            }
            else -> {
                System.err.println("[libpebble3-bridge] Background packet: ${packet.endpoint}")
            }
        }
    }

    /**
     * Enable app log shipping on the watch
     */
    private fun enableAppLogShipping() {
        val logControlPacket = AppLogShippingControlMessage(true)
        sendPacket(logControlPacket)
        System.err.println("[libpebble3-bridge] Enabled app log shipping")
    }

    /**
     * Stream logs from the emulator
     */
    fun streamLogs() {
        System.err.println("[libpebble3-bridge] Streaming logs (Ctrl+C to stop)...")

        // Enable log shipping
        enableAppLogShipping()

        // Read packets forever
        Runtime.getRuntime().addShutdownHook(Thread {
            System.err.println("\n[libpebble3-bridge] Shutting down...")
            connected = false
            try { socket.close() } catch (_: Exception) {}
        })

        while (connected) {
            try {
                val packet = readPacket() ?: continue
                when (packet.endpoint) {
                    ProtocolEndpoint.APP_LOGS -> {
                        if (packet is AppLogReceivedMessage) {
                            val timestamp = java.time.LocalTime.now().toString().substring(0, 8)
                            println("[$timestamp] ${packet.filename.get()}:${packet.lineNumber.get()}> ${packet.message.get()}")
                            java.lang.System.out.flush()
                        }
                    }
                    ProtocolEndpoint.LOGS -> {
                        System.err.println("[libpebble3-bridge] System log received")
                    }
                    else -> {
                        // Silently ignore other packets during log streaming
                    }
                }
            } catch (e: Exception) {
                if (connected) {
                    System.err.println("[libpebble3-bridge] Error reading: ${e.message}")
                }
                break
            }
        }
    }

    fun sendPing() {
        System.err.println("[libpebble3-bridge] Sending ping...")
        val ping = PingPong.Ping(1337u)
        sendPacket(ping)

        val timeout = java.lang.System.currentTimeMillis() + 5_000
        while (java.lang.System.currentTimeMillis() < timeout) {
            val packet = readPacket() ?: continue
            if (packet.endpoint == ProtocolEndpoint.PING) {
                println("Pong received!")
                return
            }
        }
        System.err.println("[libpebble3-bridge] No pong received.")
    }
}
