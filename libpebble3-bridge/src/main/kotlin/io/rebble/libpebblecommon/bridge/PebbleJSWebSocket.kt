package io.rebble.libpebblecommon.bridge

import uniffi.library_rs.JsContext
import java.net.URI
import java.net.http.HttpClient

/**
 * Manages real WebSocket connections on behalf of the JS runtime.
 *
 * JS queues WebSocket actions (connect, send, close) via the consolidated
 * event queue. This class creates real Java WebSocket connections, and
 * pumps incoming messages back into JS via eval().
 */
class PebbleJSWebSocket {
    private val webSockets = mutableMapOf<Int, JavaWebSocket>()

    /**
     * Represents a real WebSocket connection managed by Java's HTTP client.
     */
    inner class JavaWebSocket(
        val id: Int,
        val url: String
    ) {
        private val incomingMessages = mutableListOf<String>()
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
                        // Binary data not commonly used by Pebble apps; skip
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
     * Handle a WebSocket action from the consolidated event queue.
     */
    fun handleAction(action: String, id: Int, url: String?, data: String?, code: Int?, reason: String?) {
        when (action) {
            "connect" -> {
                val wsUrl = url ?: return
                val ws = JavaWebSocket(id, wsUrl)
                webSockets[id] = ws
                Thread { ws.connect() }.start()
                System.err.println("[pkjs] WebSocket connecting: $wsUrl")
            }
            "send" -> {
                webSockets[id]?.send(data ?: "")
            }
            "close" -> {
                webSockets[id]?.close(code ?: 1000, reason ?: "")
            }
        }
    }

    /**
     * Pump WebSocket events from Java connections back into JS.
     */
    fun pumpEvents(ctx: JsContext, drainPendingWork: () -> Unit) {
        val toRemove = mutableListOf<Int>()
        for ((id, ws) in webSockets) {
            try {
                if (ws.isOpen) {
                    // Fire onopen if WS just transitioned to open
                    ctx.eval("if (_wsInstances[$id] && _wsInstances[$id].readyState === 0) { _pkjsWsEvent($id, 'open', '', 0, ''); }")
                    drainPendingWork()

                    // Deliver incoming messages
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

    /**
     * Close all active WebSocket connections.
     */
    fun closeAll() {
        for ((_, ws) in webSockets) {
            ws.close(1001, "App stopping")
        }
        webSockets.clear()
    }
}
