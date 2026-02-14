package io.rebble.libpebblecommon.bridge

import java.net.URI
import java.net.http.HttpClient
import uniffi.library_rs.JsContext

/**
 * Manages real WebSocket connections on behalf of the JS runtime.
 * JS queues connect/send/close actions via the consolidated event queue;
 * Kotlin creates real connections and pumps incoming events back to JS.
 */
class JavaWebSocket(
    val id: Int,
    val url: String
) {
    private val incomingMessages = mutableListOf<String>()
    private val incomingBinary = mutableListOf<ByteArray>()
    var isOpen = false
        private set
    var isClosed = false
        private set
    var closeCode = 0
        private set
    var closeReason = ""
        private set
    var errorMessage: String? = null
        private set

    private var javaWs: java.net.http.WebSocket? = null

    fun connect() {
        try {
            val client = HttpClient.newHttpClient()
            val builder = client.newWebSocketBuilder()
            val listener = object : java.net.http.WebSocket.Listener {
                private val textBuffer = StringBuilder()

                override fun onOpen(webSocket: java.net.http.WebSocket) {
                    isOpen = true
                    webSocket.request(1)
                }

                override fun onText(
                    webSocket: java.net.http.WebSocket,
                    data: CharSequence,
                    last: Boolean
                ): java.util.concurrent.CompletionStage<*>? {
                    textBuffer.append(data)
                    if (last) {
                        synchronized(incomingMessages) {
                            incomingMessages.add(textBuffer.toString())
                        }
                        textBuffer.clear()
                    }
                    webSocket.request(1)
                    return null
                }

                override fun onBinary(
                    webSocket: java.net.http.WebSocket,
                    data: java.nio.ByteBuffer,
                    last: Boolean
                ): java.util.concurrent.CompletionStage<*>? {
                    val bytes = ByteArray(data.remaining())
                    data.get(bytes)
                    synchronized(incomingBinary) {
                        incomingBinary.add(bytes)
                    }
                    webSocket.request(1)
                    return null
                }

                override fun onClose(
                    webSocket: java.net.http.WebSocket,
                    statusCode: Int,
                    reason: String
                ): java.util.concurrent.CompletionStage<*>? {
                    isClosed = true
                    closeCode = statusCode
                    closeReason = reason
                    isOpen = false
                    return null
                }

                override fun onError(webSocket: java.net.http.WebSocket, error: Throwable) {
                    errorMessage = error.message ?: "Unknown WebSocket error"
                    isClosed = true
                    isOpen = false
                }
            }
            javaWs = builder.buildAsync(URI.create(url), listener).join()
        } catch (e: Exception) {
            errorMessage = e.message ?: "Connection failed"
            isClosed = true
            isOpen = false
        }
    }

    fun send(data: String) {
        javaWs?.sendText(data, true)
    }

    fun close(code: Int, reason: String) {
        try {
            javaWs?.sendClose(code, reason)
        } catch (_: Exception) {}
        isClosed = true
        isOpen = false
    }

    fun drainMessages(): List<String> {
        synchronized(incomingMessages) {
            val copy = incomingMessages.toList()
            incomingMessages.clear()
            return copy
        }
    }
}

/**
 * Pump WebSocket events from Java connections back into JS.
 */
fun pumpWebSocketEvents(
    ctx: JsContext,
    webSockets: MutableMap<Int, JavaWebSocket>,
    drainPendingWork: () -> Unit
) {
    val toRemove = mutableListOf<Int>()
    for ((id, ws) in webSockets) {
        try {
            if (ws.isOpen) {
                ctx.eval("if (_wsInstances[$id] && _wsInstances[$id].readyState === 0) { _pkjsWsEvent($id, 'open', '', 0, ''); }")
                drainPendingWork()

                for (msg in ws.drainMessages()) {
                    val escaped = msg
                        .replace("\\", "\\\\")
                        .replace("'", "\\'")
                        .replace("\n", "\\n")
                        .replace("\r", "\\r")
                    ctx.eval("_pkjsWsEvent($id, 'message', '$escaped', 0, '')")
                    drainPendingWork()
                }
            }

            if (ws.errorMessage != null) {
                val errEsc = (ws.errorMessage ?: "")
                    .replace("\\", "\\\\")
                    .replace("'", "\\'")
                ctx.eval("_pkjsWsEvent($id, 'error', '$errEsc', 0, '')")
                drainPendingWork()
            }

            if (ws.isClosed) {
                val reasonEsc = ws.closeReason
                    .replace("\\", "\\\\")
                    .replace("'", "\\'")
                ctx.eval("_pkjsWsEvent($id, 'close', '', ${ws.closeCode}, '$reasonEsc')")
                drainPendingWork()
                toRemove.add(id)
            }
        } catch (e: Exception) {
            System.err.println("[pkjs] Error pumping WS events for #$id: ${e.message}")
            toRemove.add(id)
        }
    }
    for (id in toRemove) {
        webSockets.remove(id)
    }
}
