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
import java.util.Base64
import java.util.zip.ZipFile
import kotlin.system.exitProcess
import kotlin.uuid.Uuid

/**
 * libpebble3-bridge: A CLI tool that connects to QEMU Pebble emulator
 * and provides app installation, log streaming, screenshot capture,
 * emulator control, data logging, and PebbleKit JS support using the
 * libpebble3 protocol layer.
 *
 * Communication with QEMU uses QemuSPP framing:
 *   [0xFEED][protocol][length][payload][0xBEEF]
 *
 * Protocol types:
 *   1 = SPP (Pebble Protocol)
 *   2 = Tap, 3 = BluetoothConnection, 4 = Compass, 5 = Battery,
 *   6 = Accel, 7 = Vibration, 8 = Button, 9 = TimeFormat,
 *   10 = TimelinePeek, 11 = ContentSize
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
            requireArgs(args, 4, "install <qemu_port> <pbw_path> <platform>")
            val bridge = QemuBridge(args[1].toInt())
            bridge.connect()
            bridge.negotiate()
            bridge.installApp(args[2], args[3])
            bridge.disconnect()
        }
        "logs" -> {
            requireArgs(args, 2, "logs <qemu_port>")
            val bridge = QemuBridge(args[1].toInt())
            bridge.connect()
            bridge.negotiate()
            bridge.streamLogs()
        }
        "install-and-logs" -> {
            requireArgs(args, 4, "install-and-logs <qemu_port> <pbw_path> <platform>")
            val bridge = QemuBridge(args[1].toInt())
            bridge.connect()
            bridge.negotiate()
            bridge.installApp(args[2], args[3])
            bridge.streamLogsWithPKJS(args[2], args[3])
        }
        "ping" -> {
            requireArgs(args, 2, "ping <qemu_port>")
            val bridge = QemuBridge(args[1].toInt())
            bridge.connect()
            bridge.negotiate()
            bridge.sendPing()
            bridge.disconnect()
        }
        "screenshot" -> {
            requireArgs(args, 2, "screenshot <qemu_port>")
            val bridge = QemuBridge(args[1].toInt())
            bridge.connect()
            bridge.negotiate()
            bridge.takeScreenshot()
            bridge.disconnect()
        }
        // --- QEMU Emulator Control Commands (no negotiation needed) ---
        "emu-tap" -> {
            requireArgs(args, 4, "emu-tap <qemu_port> <axis:0-2> <direction:+1/-1>")
            val bridge = QemuBridge(args[1].toInt())
            bridge.connect()
            bridge.emuTap(args[2].toInt(), args[3].toInt())
            bridge.disconnect()
        }
        "emu-button" -> {
            requireArgs(args, 3, "emu-button <qemu_port> <state_bitmask>")
            val bridge = QemuBridge(args[1].toInt())
            bridge.connect()
            bridge.emuButton(args[2].toInt())
            bridge.disconnect()
        }
        "emu-battery" -> {
            requireArgs(args, 4, "emu-battery <qemu_port> <percent:0-100> <charging:0/1>")
            val bridge = QemuBridge(args[1].toInt())
            bridge.connect()
            bridge.emuBattery(args[2].toInt(), args[3] == "1")
            bridge.disconnect()
        }
        "emu-bt-connection" -> {
            requireArgs(args, 3, "emu-bt-connection <qemu_port> <connected:0/1>")
            val bridge = QemuBridge(args[1].toInt())
            bridge.connect()
            bridge.emuBluetoothConnection(args[2] == "1")
            bridge.disconnect()
        }
        "emu-compass" -> {
            requireArgs(args, 4, "emu-compass <qemu_port> <heading_raw> <calibration:0-2>")
            val bridge = QemuBridge(args[1].toInt())
            bridge.connect()
            bridge.emuCompass(args[2].toInt(), args[3].toInt())
            bridge.disconnect()
        }
        "emu-time-format" -> {
            requireArgs(args, 3, "emu-time-format <qemu_port> <is24hour:0/1>")
            val bridge = QemuBridge(args[1].toInt())
            bridge.connect()
            bridge.emuTimeFormat(args[2] == "1")
            bridge.disconnect()
        }
        "emu-set-timeline-peek" -> {
            requireArgs(args, 3, "emu-set-timeline-peek <qemu_port> <enabled:0/1>")
            val bridge = QemuBridge(args[1].toInt())
            bridge.connect()
            bridge.emuTimelinePeek(args[2] == "1")
            bridge.disconnect()
        }
        "emu-set-content-size" -> {
            requireArgs(args, 3, "emu-set-content-size <qemu_port> <size:0-3>")
            val bridge = QemuBridge(args[1].toInt())
            bridge.connect()
            bridge.emuContentSize(args[2].toInt())
            bridge.disconnect()
        }
        "emu-accel" -> {
            // emu-accel <port> <count> <x1,y1,z1> [x2,y2,z2] ...
            requireArgs(args, 4, "emu-accel <qemu_port> <count> <x,y,z> ...")
            val bridge = QemuBridge(args[1].toInt())
            bridge.connect()
            val samples = mutableListOf<Triple<Short, Short, Short>>()
            for (i in 3 until args.size) {
                val parts = args[i].split(",")
                samples.add(Triple(parts[0].toShort(), parts[1].toShort(), parts[2].toShort()))
            }
            bridge.emuAccel(samples)
            bridge.disconnect()
        }
        // --- Data Logging Commands ---
        "data-logging-list" -> {
            requireArgs(args, 2, "data-logging-list <qemu_port>")
            val bridge = QemuBridge(args[1].toInt())
            bridge.connect()
            bridge.negotiate()
            bridge.dataLoggingList()
            bridge.disconnect()
        }
        "data-logging-get-send-enabled" -> {
            requireArgs(args, 2, "data-logging-get-send-enabled <qemu_port>")
            val bridge = QemuBridge(args[1].toInt())
            bridge.connect()
            bridge.negotiate()
            bridge.dataLoggingGetSendEnabled()
            bridge.disconnect()
        }
        "data-logging-set-send-enabled" -> {
            requireArgs(args, 3, "data-logging-set-send-enabled <qemu_port> <0/1>")
            val bridge = QemuBridge(args[1].toInt())
            bridge.connect()
            bridge.negotiate()
            bridge.dataLoggingSetSendEnabled(args[2] == "1")
            bridge.disconnect()
        }
        else -> {
            printUsage()
            exitProcess(1)
        }
    }
}

fun requireArgs(args: Array<String>, min: Int, usage: String) {
    if (args.size < min) {
        System.err.println("Usage: libpebble3-bridge $usage")
        exitProcess(1)
    }
}

fun printUsage() {
    System.err.println("""
        libpebble3-bridge - Pebble emulator bridge using libpebble3 protocol

        Protocol Commands (require negotiation):
          install <port> <pbw> <platform>              Install app
          install-and-logs <port> <pbw> <platform>     Install app and stream logs (with PKJS)
          logs <port>                                    Stream logs
          ping <port>                                    Test connectivity
          screenshot <port>                              Capture screenshot (JSON on stdout)
          data-logging-list <port>                       List data logging sessions (JSON)
          data-logging-get-send-enabled <port>           Check send enabled (JSON)
          data-logging-set-send-enabled <port> <0/1>     Set send enabled

        QEMU Control Commands (no negotiation):
          emu-tap <port> <axis:0-2> <dir:+1/-1>         Emulate tap
          emu-button <port> <state_bitmask>              Press buttons
          emu-battery <port> <pct:0-100> <charging:0/1>  Set battery
          emu-bt-connection <port> <connected:0/1>       Set BT connection
          emu-compass <port> <heading_raw> <calib:0-2>   Set compass
          emu-time-format <port> <is24h:0/1>             Set time format
          emu-set-timeline-peek <port> <enabled:0/1>     Set timeline peek
          emu-set-content-size <port> <size:0-3>          Set content size
          emu-accel <port> <count> <x,y,z> ...            Send accel samples
    """.trimIndent())
}

class QemuBridge(private val port: Int) {
    private lateinit var socket: Socket
    private lateinit var input: InputStream
    private lateinit var output: OutputStream
    @Volatile private var connected = false

    fun connect() {
        System.err.println("[bridge] Connecting to QEMU on localhost:$port...")
        socket = Socket("localhost", port)
        socket.tcpNoDelay = true
        input = socket.getInputStream()
        output = socket.getOutputStream()
        connected = true
        System.err.println("[bridge] Connected.")
    }

    fun disconnect() {
        connected = false
        socket.close()
    }

    // ====================================================================
    // Pebble Protocol (SPP framed) send/receive
    // ====================================================================

    fun sendPacket(packet: PebblePacket) {
        val ppBytes = packet.serialize()
        sendRawPP(ppBytes)
    }

    fun sendRawPP(ppBytes: UByteArray) {
        val length = ppBytes.size.toUShort()
        val frame = StructMapper()
        SUShort(frame, QEMU_HEADER.toUShort())
        SUShort(frame, QEMU_PROTOCOL_SPP)
        SUShort(frame, length)
        val header = frame.toBytes()

        val footer = StructMapper()
        SUShort(footer, QEMU_FOOTER.toUShort())
        val footerBytes = footer.toBytes()

        val fullFrame = header.toByteArray() + ppBytes.toByteArray() + footerBytes.toByteArray()
        synchronized(output) {
            output.write(fullFrame)
            output.flush()
        }
    }

    fun readPacket(): PebblePacket? {
        val ppBytes = readRawPP() ?: return null
        return try {
            PebblePacket.deserialize(ppBytes)
        } catch (e: Exception) {
            System.err.println("[bridge] Failed to decode packet: ${e.message}")
            null
        }
    }

    fun readRawPP(): UByteArray? {
        val headerBytes = readExact(6) ?: return null
        val headerBuf = DataBuffer(headerBytes.toUByteArray())
        val sig = headerBuf.getUShort()
        val proto = headerBuf.getUShort()
        val length = headerBuf.getUShort()

        if (sig != QEMU_HEADER.toUShort()) {
            System.err.println("[bridge] Invalid QEMU header: 0x${sig.toString(16)}")
            return null
        }

        if (proto != QEMU_PROTOCOL_SPP) {
            // Non-SPP protocol, skip payload + footer
            readExact(length.toInt() + 2)
            return null
        }

        val payload = readExact(length.toInt()) ?: return null
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

    // ====================================================================
    // QEMU Control Packet send (non-SPP protocols)
    // ====================================================================

    fun sendQemuControl(protocol: UShort, payload: ByteArray) {
        val frame = StructMapper()
        SUShort(frame, QEMU_HEADER.toUShort())
        SUShort(frame, protocol)
        SUShort(frame, payload.size.toUShort())
        val header = frame.toBytes()

        val footer = StructMapper()
        SUShort(footer, QEMU_FOOTER.toUShort())
        val footerBytes = footer.toBytes()

        val fullFrame = header.toByteArray() + payload + footerBytes.toByteArray()
        synchronized(output) {
            output.write(fullFrame)
            output.flush()
        }
    }

    // ====================================================================
    // Protocol negotiation
    // ====================================================================

    fun negotiate() {
        System.err.println("[bridge] Starting protocol negotiation...")

        Thread.sleep(500)
        socket.soTimeout = 500
        var drainCount = 0
        try {
            while (true) {
                val packet = readPacket()
                if (packet != null) {
                    drainCount++
                    System.err.println("[bridge] Drained: endpoint=${packet.endpoint}")
                    if (packet.endpoint == ProtocolEndpoint.PHONE_VERSION) {
                        System.err.println("[bridge] Got PhoneVersionRequest!")
                    }
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
        System.err.println("[bridge] Drained $drainCount queued packets")
        socket.soTimeout = 0

        // Send PhoneVersionResponse proactively
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
        System.err.println("[bridge] Sent PhoneVersionResponse")

        // Send WatchVersionRequest
        sendPacket(WatchVersion.WatchVersionRequest())
        System.err.println("[bridge] Sent WatchVersionRequest")

        // Wait for WatchVersionResponse
        val timeout = java.lang.System.currentTimeMillis() + 10_000
        socket.soTimeout = 10_000
        while (java.lang.System.currentTimeMillis() < timeout) {
            try {
                val packet = readPacket()
                if (packet != null) {
                    if (packet.endpoint == ProtocolEndpoint.WATCH_VERSION) {
                        if (packet is WatchVersion.WatchVersionResponse) {
                            System.err.println("[bridge] Watch firmware: ${packet.running.versionTag.get()}")
                        }
                        System.err.println("[bridge] Negotiation complete.")
                        socket.soTimeout = 0
                        return
                    }
                    if (packet is PingPong) {
                        respondToPing(packet)
                    }
                    System.err.println("[bridge] During negotiation: endpoint=${packet.endpoint}")
                }
            } catch (_: java.net.SocketTimeoutException) {
                break
            }
        }
        socket.soTimeout = 0
        System.err.println("[bridge] WARNING: No WatchVersionResponse received, continuing...")
    }

    private fun respondToPing(ping: PingPong) {
        val pong = PingPong.Pong(cookie = ping.cookie.get())
        sendPacket(pong)
    }

    // ====================================================================
    // Ping
    // ====================================================================

    fun sendPing() {
        System.err.println("[bridge] Sending ping...")
        val ping = PingPong.Ping(1337u)
        sendPacket(ping)

        val timeout = java.lang.System.currentTimeMillis() + 5_000
        while (java.lang.System.currentTimeMillis() < timeout) {
            val packet = readPacket() ?: continue
            if (packet.endpoint == ProtocolEndpoint.PING) {
                println("Pong!")
                return
            }
        }
        System.err.println("[bridge] No pong received.")
    }

    // ====================================================================
    // App Install (modern BlobDB + AppFetch + PutBytes flow)
    // ====================================================================

    fun installApp(pbwPath: String, platform: String) {
        System.err.println("[bridge] Installing app from $pbwPath for platform $platform...")

        val zipFile = ZipFile(pbwPath)

        val binaryEntry = zipFile.getEntry("$platform/pebble-app.bin")
            ?: throw RuntimeException("No binary found for platform $platform in PBW")
        val appBinary = zipFile.getInputStream(binaryEntry).readBytes()

        val resourceEntry = zipFile.getEntry("$platform/app_resources.pbpack")
        val appResources = resourceEntry?.let { zipFile.getInputStream(it).readBytes() }

        val workerEntry = zipFile.getEntry("$platform/pebble-worker.bin")
        val workerBinary = workerEntry?.let { zipFile.getInputStream(it).readBytes() }

        val meta = parseAppHeader(appBinary)
        System.err.println("[bridge] App: ${meta.appName} UUID: ${meta.uuid}")

        enableAppLogShipping()
        insertAppIntoBlobDB(meta)

        // Send AppRunStateStart
        System.err.println("[bridge] Sending AppRunStateStart...")
        sendPacket(AppRunStateMessage.AppRunStateStart(meta.uuid))

        // Wait for AppFetchRequest
        System.err.println("[bridge] Waiting for AppFetchRequest...")
        var appId: UInt = 1u
        val fetchTimeout = java.lang.System.currentTimeMillis() + 15_000
        while (java.lang.System.currentTimeMillis() < fetchTimeout) {
            val packet = readPacket() ?: continue
            if (packet is AppFetchRequest) {
                appId = packet.appId.get()
                System.err.println("[bridge] AppFetchRequest: appId=$appId")
                sendPacket(AppFetchResponse(AppFetchResponseStatus.START))
                System.err.println("[bridge] Sent AppFetchResponse(START)")
                break
            }
            handleBackgroundPacket(packet)
        }

        // PutBytes transfers
        System.err.println("[bridge] Sending app binary (${appBinary.size} bytes, appId=$appId)...")
        putBytesTransfer(appBinary.toUByteArray(), ObjectType.APP_EXECUTABLE, appId)

        if (appResources != null) {
            System.err.println("[bridge] Sending app resources (${appResources.size} bytes)...")
            putBytesTransfer(appResources.toUByteArray(), ObjectType.APP_RESOURCE, appId)
        }

        if (workerBinary != null) {
            System.err.println("[bridge] Sending worker binary (${workerBinary.size} bytes)...")
            putBytesTransfer(workerBinary.toUByteArray(), ObjectType.WORKER, appId)
        }

        println("App install succeeded.")
        zipFile.close()
    }

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

        return AppHeader(uuid, appName, flags, iconResourceId, appVersionMajor, appVersionMinor, sdkVersionMajor, sdkVersionMinor)
    }

    private fun insertAppIntoBlobDB(meta: AppHeader) {
        System.err.println("[bridge] Inserting app into BlobDB...")
        val appMetadata = io.rebble.libpebblecommon.packets.blobdb.AppMetadata(
            uuid = meta.uuid, flags = meta.flags, icon = meta.iconResourceId,
            appVersionMajor = meta.appVersionMajor, appVersionMinor = meta.appVersionMinor,
            sdkVersionMajor = meta.sdkVersionMajor, sdkVersionMinor = meta.sdkVersionMinor,
            appFaceBgColor = 0u, appFaceTemplateId = 0u, appName = meta.appName,
        )
        val metadataBytes = appMetadata.toBytes()
        val uuidBytes = meta.uuid.toByteArray().toUByteArray()
        val token = (java.lang.System.currentTimeMillis() % 65536).toUShort()
        val insertCmd = io.rebble.libpebblecommon.packets.blobdb.BlobCommand.InsertCommand(
            token = token, database = coredev.BlobDatabase.App,
            key = uuidBytes, value = metadataBytes,
        )
        sendPacket(insertCmd)

        val timeout = java.lang.System.currentTimeMillis() + 10_000
        while (java.lang.System.currentTimeMillis() < timeout) {
            val packet = readPacket() ?: continue
            if (packet is io.rebble.libpebblecommon.packets.blobdb.BlobResponse) {
                System.err.println("[bridge] BlobDB response: ${packet.responseValue}")
                return
            }
            handleBackgroundPacket(packet)
        }
        System.err.println("[bridge] WARNING: No BlobDB response received (continuing)")
    }

    private fun putBytesTransfer(data: UByteArray, objectType: ObjectType, appId: UInt) {
        val initPacket = PutBytesAppInit(objectSize = data.size.toUInt(), objectType = objectType, appId = appId)
        sendPacket(initPacket)

        val initResponse = waitForPutBytesResponse() ?: throw RuntimeException("No response to PutBytes init")
        if (!initResponse.isAck) throw RuntimeException("PutBytes init NACK'd")
        val cookie = initResponse.cookie.get()
        System.err.println("[bridge] PutBytes init OK, cookie=$cookie")

        val chunkSize = 2000
        var offset = 0
        while (offset < data.size) {
            val end = minOf(offset + chunkSize, data.size)
            val chunk = data.sliceArray(offset until end)
            sendPacket(PutBytesPut(cookie, chunk))
            val putResponse = waitForPutBytesResponse() ?: throw RuntimeException("No response to PutBytes put at offset $offset")
            if (!putResponse.isAck) throw RuntimeException("PutBytes put NACK'd at offset $offset")
            offset = end
        }

        sendPacket(PutBytesCommit(cookie, stm32Crc32(data.toByteArray())))
        val commitResponse = waitForPutBytesResponse() ?: throw RuntimeException("No response to PutBytes commit")
        if (!commitResponse.isAck) throw RuntimeException("PutBytes commit NACK'd")

        sendPacket(PutBytesInstall(cookie))
        val installResponse = waitForPutBytesResponse() ?: throw RuntimeException("No response to PutBytes install")
        if (!installResponse.isAck) throw RuntimeException("PutBytes install NACK'd")
        System.err.println("[bridge] PutBytes transfer complete for $objectType")
    }

    private fun stm32Crc32(data: ByteArray): UInt {
        var crc = 0xFFFFFFFFu
        val poly = 0x04C11DB7u
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
                crc = if ((crc and 0x80000000u) != 0u) (crc shl 1) xor poly else crc shl 1
            }
        }
        return crc and 0xFFFFFFFFu
    }

    private fun waitForPutBytesResponse(): PutBytesResponse? {
        val timeout = java.lang.System.currentTimeMillis() + 20_000
        while (java.lang.System.currentTimeMillis() < timeout) {
            val packet = readPacket() ?: continue
            if (packet is PutBytesResponse) return packet
            handleBackgroundPacket(packet)
        }
        return null
    }

    private fun handleBackgroundPacket(packet: PebblePacket) {
        when (packet.endpoint) {
            ProtocolEndpoint.APP_LOGS -> {
                if (packet is AppLogReceivedMessage) {
                    val ts = java.time.LocalTime.now().toString().substring(0, 8)
                    println("[$ts] ${packet.filename.get()}:${packet.lineNumber.get()}> ${packet.message.get()}")
                }
            }
            ProtocolEndpoint.PING -> {
                if (packet is PingPong) respondToPing(packet)
            }
            else -> {
                System.err.println("[bridge] Background packet: ${packet.endpoint}")
            }
        }
    }

    private fun enableAppLogShipping() {
        sendPacket(AppLogShippingControlMessage(true))
        System.err.println("[bridge] Enabled app log shipping")
    }

    // ====================================================================
    // Log Streaming
    // ====================================================================

    fun streamLogs() {
        System.err.println("[bridge] Streaming logs (Ctrl+C to stop)...")
        enableAppLogShipping()

        Runtime.getRuntime().addShutdownHook(Thread {
            System.err.println("\n[bridge] Shutting down...")
            connected = false
            try { socket.close() } catch (_: Exception) {}
        })

        while (connected) {
            try {
                val packet = readPacket() ?: continue
                when (packet.endpoint) {
                    ProtocolEndpoint.APP_LOGS -> {
                        if (packet is AppLogReceivedMessage) {
                            val ts = java.time.LocalTime.now().toString().substring(0, 8)
                            println("[$ts] ${packet.filename.get()}:${packet.lineNumber.get()}> ${packet.message.get()}")
                            java.lang.System.out.flush()
                        }
                    }
                    ProtocolEndpoint.PING -> {
                        if (packet is PingPong) respondToPing(packet)
                    }
                    else -> {}
                }
            } catch (e: Exception) {
                if (connected) System.err.println("[bridge] Error reading: ${e.message}")
                break
            }
        }
    }

    /**
     * Stream logs with PKJS support. Extracts pebble-js-app.js from PBW
     * and runs it using Picaros (Boa engine), handling AppMessage bidirectionally.
     */
    fun streamLogsWithPKJS(pbwPath: String, platform: String) {
        System.err.println("[bridge] Streaming logs with PKJS support (Ctrl+C to stop)...")
        enableAppLogShipping()

        // Try to extract PKJS from PBW
        var pkjs: PebbleJS? = null
        try {
            val zipFile = ZipFile(pbwPath)
            val jsEntry = zipFile.getEntry("pebble-js-app.js")
                ?: zipFile.getEntry("$platform/pebble-js-app.js")
            if (jsEntry != null) {
                val jsSource = zipFile.getInputStream(jsEntry).bufferedReader().readText()
                val meta = parseAppHeader(
                    zipFile.getInputStream(zipFile.getEntry("$platform/pebble-app.bin")).readBytes()
                )
                pkjs = PebbleJS(this, jsSource, meta.uuid)
                pkjs.start()
                System.err.println("[bridge] PKJS runtime started for ${meta.appName}")
            } else {
                System.err.println("[bridge] No pebble-js-app.js found in PBW, PKJS disabled")
            }
            zipFile.close()
        } catch (e: Exception) {
            System.err.println("[bridge] Failed to initialize PKJS: ${e.message}")
        }

        Runtime.getRuntime().addShutdownHook(Thread {
            System.err.println("\n[bridge] Shutting down...")
            connected = false
            pkjs?.stop()
            try { socket.close() } catch (_: Exception) {}
        })

        // Event loop: handle logs, AppMessages, PKJS events
        socket.soTimeout = 200
        while (connected) {
            try {
                val packet = readPacket()
                if (packet != null) {
                    when (packet.endpoint) {
                        ProtocolEndpoint.APP_LOGS -> {
                            if (packet is AppLogReceivedMessage) {
                                val ts = java.time.LocalTime.now().toString().substring(0, 8)
                                println("[$ts] ${packet.filename.get()}:${packet.lineNumber.get()}> ${packet.message.get()}")
                                java.lang.System.out.flush()
                            }
                        }
                        ProtocolEndpoint.APP_MESSAGE -> {
                            if (packet is AppMessage.AppMessagePush) {
                                // ACK the message from watch
                                sendPacket(AppMessage.AppMessageACK(packet.transactionId.get()))
                                // Forward to PKJS
                                pkjs?.handleAppMessage(packet)
                            }
                        }
                        ProtocolEndpoint.PING -> {
                            if (packet is PingPong) respondToPing(packet)
                        }
                        else -> {}
                    }
                }
            } catch (_: java.net.SocketTimeoutException) {
                // No data available - process PKJS pending events
                pkjs?.processPendingEvents()
            } catch (e: Exception) {
                if (connected) System.err.println("[bridge] Error reading: ${e.message}")
                break
            }
        }
    }

    // ====================================================================
    // Screenshot
    // ====================================================================

    fun takeScreenshot() {
        System.err.println("[bridge] Taking screenshot...")
        sendPacket(ScreenshotRequest())

        // Read first response - it's the header
        // ScreenshotData is registered as universal decoder, so we get raw data
        val firstPacket = readPacket()
        if (firstPacket == null || firstPacket !is ScreenshotData) {
            System.err.println("[bridge] No screenshot response received")
            exitProcess(1)
        }

        // Parse header from the raw data field
        val rawData = firstPacket.data.get()
        if (rawData.isEmpty()) {
            System.err.println("[bridge] Empty screenshot response")
            exitProcess(1)
        }

        val responseCode = rawData[0]
        if (responseCode != 0u.toUByte()) {
            System.err.println("[bridge] Screenshot error: code=$responseCode")
            exitProcess(1)
        }

        // Parse header: responseCode(1) + version(4 BE) + width(4 BE) + height(4 BE) = 13 bytes
        val headerBuf = java.nio.ByteBuffer.wrap(rawData.toByteArray()).order(java.nio.ByteOrder.BIG_ENDIAN)
        headerBuf.get() // skip responseCode
        val version = headerBuf.int.toUInt()
        val width = headerBuf.int.toUInt()
        val height = headerBuf.int.toUInt()

        val bpp = if (version == 1u) 1 else 8
        val totalDataSize = (width.toInt() * height.toInt() * bpp) / 8

        System.err.println("[bridge] Screenshot: ${width}x${height}, version=$version, bpp=$bpp, totalSize=$totalDataSize")

        // Collect pixel data
        val pixelData = java.io.ByteArrayOutputStream()
        // First chunk is the data after the 13-byte header
        val firstChunk = rawData.sliceArray(13 until rawData.size)
        pixelData.write(firstChunk.toByteArray())

        // Read remaining data packets
        while (pixelData.size() < totalDataSize) {
            val packet = readPacket()
            if (packet != null && packet is ScreenshotData) {
                pixelData.write(packet.data.get().toByteArray())
            } else if (packet != null) {
                handleBackgroundPacket(packet)
            }
        }

        // Output as JSON with base64-encoded data
        val b64 = Base64.getEncoder().encodeToString(pixelData.toByteArray())
        println("""{"width":$width,"height":$height,"version":$version,"data":"$b64"}""")
        System.err.println("[bridge] Screenshot captured: ${width}x${height}")
    }

    // ====================================================================
    // QEMU Emulator Control Commands
    // ====================================================================

    fun emuTap(axis: Int, direction: Int) {
        val payload = byteArrayOf(axis.toByte(), direction.toByte())
        sendQemuControl(QemuPacket.Protocol.Tap.value, payload)
        System.err.println("[bridge] Sent tap: axis=$axis, direction=$direction")
    }

    fun emuButton(state: Int) {
        val payload = byteArrayOf(state.toByte())
        sendQemuControl(QemuPacket.Protocol.Button.value, payload)
        System.err.println("[bridge] Sent button: state=$state")
    }

    fun emuBattery(percent: Int, charging: Boolean) {
        val payload = byteArrayOf(percent.toByte(), if (charging) 1 else 0)
        sendQemuControl(QemuPacket.Protocol.Battery.value, payload)
        System.err.println("[bridge] Sent battery: $percent%, charging=$charging")
    }

    fun emuBluetoothConnection(connectedState: Boolean) {
        val payload = byteArrayOf(if (connectedState) 1 else 0)
        sendQemuControl(QemuPacket.Protocol.BluetoothConnection.value, payload)
        System.err.println("[bridge] Sent BT connection: $connectedState")
    }

    fun emuCompass(heading: Int, calibration: Int) {
        // heading is 4 bytes LE, calibration is 1 byte
        val buf = java.nio.ByteBuffer.allocate(5).order(java.nio.ByteOrder.LITTLE_ENDIAN)
        buf.putInt(heading)
        buf.put(calibration.toByte())
        sendQemuControl(QemuPacket.Protocol.Compass.value, buf.array())
        System.err.println("[bridge] Sent compass: heading=$heading, calibration=$calibration")
    }

    fun emuTimeFormat(is24Hour: Boolean) {
        val payload = byteArrayOf(if (is24Hour) 1 else 0)
        sendQemuControl(QemuPacket.Protocol.TimeFormat.value, payload)
        System.err.println("[bridge] Sent time format: is24Hour=$is24Hour")
    }

    fun emuTimelinePeek(enabled: Boolean) {
        val payload = byteArrayOf(if (enabled) 1 else 0)
        sendQemuControl(QemuPacket.Protocol.TimelinePeek.value, payload)
        System.err.println("[bridge] Sent timeline peek: enabled=$enabled")
    }

    fun emuContentSize(size: Int) {
        val payload = byteArrayOf(size.toByte())
        sendQemuControl(QemuPacket.Protocol.ContentSize.value, payload)
        System.err.println("[bridge] Sent content size: $size")
    }

    fun emuAccel(samples: List<Triple<Short, Short, Short>>) {
        // Format: [count: u8][samples: (x:i16 LE, y:i16 LE, z:i16 LE)...]
        val buf = java.nio.ByteBuffer.allocate(1 + samples.size * 6).order(java.nio.ByteOrder.LITTLE_ENDIAN)
        buf.put(samples.size.toByte())
        for ((x, y, z) in samples) {
            buf.putShort(x)
            buf.putShort(y)
            buf.putShort(z)
        }
        sendQemuControl(QemuPacket.Protocol.Accel.value, buf.array())
        System.err.println("[bridge] Sent ${samples.size} accel samples")
    }

    // ====================================================================
    // Data Logging
    // ====================================================================

    fun dataLoggingList() {
        System.err.println("[bridge] Listing data logging sessions...")

        // Send ReportOpenSessions with empty list to request session enumeration
        sendPacket(DataLoggingOutgoingPacket.ReportOpenSessions(emptyList()))

        // Collect OpenSession responses
        val sessions = mutableListOf<Map<String, Any>>()
        val timeout = java.lang.System.currentTimeMillis() + 5_000
        socket.soTimeout = 2_000
        try {
            while (java.lang.System.currentTimeMillis() < timeout) {
                val packet = readPacket() ?: continue
                if (packet is DataLoggingIncomingPacket.OpenSession) {
                    sessions.add(mapOf(
                        "id" to packet.sessionId.get().toInt(),
                        "uuid" to packet.applicationUUID.get().toString(),
                        "timestamp" to packet.timestamp.get().toLong(),
                        "tag" to packet.tag.get().toLong(),
                        "type" to packet.dataItemTypeId.get().toInt(),
                        "size" to packet.dataItemSize.get().toInt()
                    ))
                } else if (packet is PingPong) {
                    respondToPing(packet)
                }
            }
        } catch (_: java.net.SocketTimeoutException) {
            // Expected - no more sessions
        }
        socket.soTimeout = 0

        // Output as JSON
        val json = buildString {
            append("""{"sessions":[""")
            sessions.forEachIndexed { i, s ->
                if (i > 0) append(",")
                append("""{"id":${s["id"]},"uuid":"${s["uuid"]}","timestamp":${s["timestamp"]},"tag":${s["tag"]},"type":${s["type"]},"size":${s["size"]}}""")
            }
            append("]}")
        }
        println(json)
        System.err.println("[bridge] Found ${sessions.size} data logging sessions")
    }

    fun dataLoggingGetSendEnabled() {
        System.err.println("[bridge] Getting data logging send enabled...")
        sendPacket(DataLoggingOutgoingPacket.GetSendEnabled())

        val timeout = java.lang.System.currentTimeMillis() + 5_000
        while (java.lang.System.currentTimeMillis() < timeout) {
            val packet = readPacket() ?: continue
            if (packet is DataLoggingIncomingPacket.SendEnabledResponse) {
                println("""{"enabled":${packet.sendEnabled}}""")
                return
            }
            if (packet is PingPong) respondToPing(packet)
        }
        System.err.println("[bridge] No response to GetSendEnabled")
        println("""{"enabled":null,"error":"timeout"}""")
    }

    fun dataLoggingSetSendEnabled(enabled: Boolean) {
        System.err.println("[bridge] Setting data logging send enabled=$enabled...")
        sendPacket(DataLoggingOutgoingPacket.SetSendEnabled(enabled))
        val status = if (enabled) "ENABLED" else "DISABLED"
        println("""{"enabled":$enabled,"status":"$status"}""")
    }
}
