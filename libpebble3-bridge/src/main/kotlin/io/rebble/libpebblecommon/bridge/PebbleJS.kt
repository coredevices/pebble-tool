package io.rebble.libpebblecommon.bridge

import io.rebble.libpebblecommon.packets.AppMessage
import io.rebble.libpebblecommon.packets.AppMessageTuple
import io.rebble.libpebblecommon.structmapper.SUInt
import io.rebble.libpebblecommon.structmapper.StructMapper
import io.rebble.libpebblecommon.util.Endian
import org.mozilla.javascript.BaseFunction
import org.mozilla.javascript.Context
import org.mozilla.javascript.NativeArray
import org.mozilla.javascript.ScriptableObject
import org.mozilla.javascript.Scriptable
import org.mozilla.javascript.Undefined
import org.mozilla.javascript.UniqueTag
import java.io.BufferedReader
import java.io.InputStreamReader
import java.io.OutputStreamWriter
import java.net.HttpURLConnection
import java.net.URI
import java.util.concurrent.LinkedBlockingQueue
import kotlin.uuid.Uuid

typealias JSFunction = org.mozilla.javascript.Function

/**
 * PebbleKit JavaScript runtime using Mozilla Rhino.
 *
 * Implements the Pebble JS API that PBW apps use:
 * - Pebble.addEventListener / removeEventListener
 * - Pebble.sendAppMessage
 * - Pebble.getAccountToken / getWatchToken
 * - Pebble.openURL
 * - console.log/warn/error
 * - XMLHttpRequest (basic implementation)
 * - localStorage (in-memory)
 *
 * Follows the patterns from libpebble3's js/ package interfaces
 * (PKJSInterface, PrivatePKJSInterface, JsRunner).
 */
class PebbleJS(
    private val bridge: QemuBridge,
    private val jsSource: String,
    private val appUuid: Uuid
) {
    private var cx: Context? = null
    private var scope: ScriptableObject? = null
    private val eventHandlers = mutableMapOf<String, MutableList<JSFunction>>()
    private var nextTransactionId: UByte = 1u
    private val pendingEvents = LinkedBlockingQueue<Runnable>()
    private val localStorage = mutableMapOf<String, String>()

    fun start() {
        cx = Context.enter()
        cx!!.optimizationLevel = -1 // Interpreter mode for better compatibility
        cx!!.languageVersion = Context.VERSION_ES6
        scope = cx!!.initStandardObjects()

        setupPebbleAPI()
        setupConsoleAPI()
        setupXMLHttpRequest()
        setupLocalStorage()
        setupTimers()

        // Run the app JS
        try {
            cx!!.evaluateString(scope, jsSource, "pebble-js-app.js", 1, null)
        } catch (e: Exception) {
            System.err.println("[pkjs] Error loading JS: ${e.message}")
        }

        // Fire 'ready' event
        fireEvent("ready", cx!!.newObject(scope))
    }

    fun stop() {
        try {
            Context.exit()
        } catch (_: Exception) {}
    }

    fun handleAppMessage(push: AppMessage.AppMessagePush) {
        val cx = this.cx ?: return
        val scope = this.scope ?: return

        try {
            // Convert AppMessage dictionary to JS object
            val payload = cx.newObject(scope)
            for (i in 0 until push.count.get().toInt()) {
                val tuple = push.dictionary.list[i]
                val key = tuple.key.get().toString()
                val value: Any = when (AppMessageTuple.Type.fromValue(tuple.type.get())) {
                    AppMessageTuple.Type.CString -> tuple.dataAsString
                    AppMessageTuple.Type.UInt -> tuple.dataAsUnsignedNumber.toDouble()
                    AppMessageTuple.Type.Int -> tuple.dataAsSignedNumber.toDouble()
                    AppMessageTuple.Type.ByteArray -> {
                        val bytes = tuple.dataAsBytes
                        cx.newArray(scope, bytes.map { it.toDouble() as Any }.toTypedArray())
                    }
                }
                ScriptableObject.putProperty(payload, key, value)
            }

            val event = cx.newObject(scope)
            ScriptableObject.putProperty(event, "payload", payload)

            fireEvent("appmessage", event)
        } catch (e: Exception) {
            System.err.println("[pkjs] Error handling AppMessage: ${e.message}")
        }
    }

    fun processPendingEvents() {
        while (true) {
            val event = pendingEvents.poll() ?: break
            try {
                event.run()
            } catch (e: Exception) {
                System.err.println("[pkjs] Error processing event: ${e.message}")
            }
        }
    }

    private fun fireEvent(eventName: String, data: Any?) {
        val handlers = eventHandlers[eventName] ?: return
        val cx = this.cx ?: return
        val scope = this.scope ?: return

        for (handler in handlers.toList()) {
            try {
                handler.call(cx, scope, scope, arrayOf(data ?: Undefined.instance))
            } catch (e: Exception) {
                System.err.println("[pkjs] Error in '$eventName' handler: ${e.message}")
            }
        }
    }

    private fun setupPebbleAPI() {
        val cx = this.cx!!
        val scope = this.scope!!

        val pebble = cx.newObject(scope)

        // Pebble.addEventListener(event, callback)
        ScriptableObject.putProperty(pebble, "addEventListener", object : BaseFunction() {
            override fun call(cx: Context, scope: Scriptable, thisObj: Scriptable, args: Array<Any?>): Any {
                val event = Context.toString(args[0])
                val callback = args[1] as? JSFunction ?: return Undefined.instance
                eventHandlers.getOrPut(event) { mutableListOf() }.add(callback)
                return Undefined.instance
            }
        })

        // Pebble.removeEventListener(event, callback)
        ScriptableObject.putProperty(pebble, "removeEventListener", object : BaseFunction() {
            override fun call(cx: Context, scope: Scriptable, thisObj: Scriptable, args: Array<Any?>): Any {
                val event = Context.toString(args[0])
                val callback = args[1] as? JSFunction
                if (callback != null) {
                    eventHandlers[event]?.remove(callback)
                }
                return Undefined.instance
            }
        })

        // Pebble.sendAppMessage(dict, successCallback, errorCallback)
        ScriptableObject.putProperty(pebble, "sendAppMessage", object : BaseFunction() {
            override fun call(cx: Context, scope: Scriptable, thisObj: Scriptable, args: Array<Any?>): Any {
                val dict = args.getOrNull(0) as? Scriptable ?: return Undefined.instance
                val success = args.getOrNull(1) as? JSFunction
                val error = args.getOrNull(2) as? JSFunction
                sendAppMessageFromJS(cx, scope, dict, success, error)
                return Undefined.instance
            }
        })

        // Pebble.getAccountToken()
        ScriptableObject.putProperty(pebble, "getAccountToken", object : BaseFunction() {
            override fun call(cx: Context, scope: Scriptable, thisObj: Scriptable, args: Array<Any?>): Any {
                return "0123456789abcdef0123456789abcdef"
            }
        })

        // Pebble.getWatchToken()
        ScriptableObject.putProperty(pebble, "getWatchToken", object : BaseFunction() {
            override fun call(cx: Context, scope: Scriptable, thisObj: Scriptable, args: Array<Any?>): Any {
                return "fedcba9876543210fedcba9876543210"
            }
        })

        // Pebble.openURL(url)
        ScriptableObject.putProperty(pebble, "openURL", object : BaseFunction() {
            override fun call(cx: Context, scope: Scriptable, thisObj: Scriptable, args: Array<Any?>): Any {
                val url = Context.toString(args[0])
                System.err.println("[pkjs] openURL: $url")
                return Undefined.instance
            }
        })

        // Pebble.showSimpleNotificationOnPebble(title, body)
        ScriptableObject.putProperty(pebble, "showSimpleNotificationOnPebble", object : BaseFunction() {
            override fun call(cx: Context, scope: Scriptable, thisObj: Scriptable, args: Array<Any?>): Any {
                val title = Context.toString(args.getOrNull(0))
                val body = Context.toString(args.getOrNull(1))
                System.err.println("[pkjs] Notification: $title - $body")
                return Undefined.instance
            }
        })

        ScriptableObject.putProperty(scope, "Pebble", pebble)
    }

    private fun setupConsoleAPI() {
        val cx = this.cx!!
        val scope = this.scope!!

        val console = cx.newObject(scope)

        for (level in listOf("log", "info", "warn", "error", "debug")) {
            ScriptableObject.putProperty(console, level, object : BaseFunction() {
                override fun call(cx: Context, scope: Scriptable, thisObj: Scriptable, args: Array<Any?>): Any {
                    val msg = args.joinToString(" ") { Context.toString(it) }
                    val ts = java.time.LocalTime.now().toString().substring(0, 8)
                    println("[$ts] [JS $level] $msg")
                    java.lang.System.out.flush()
                    return Undefined.instance
                }
            })
        }

        ScriptableObject.putProperty(scope, "console", console)
    }

    private fun setupXMLHttpRequest() {
        val cx = this.cx!!
        val scope = this.scope!!

        // Define XMLHttpRequest constructor
        val xhrConstructor = object : BaseFunction() {
            override fun construct(cx: Context, scope: Scriptable, args: Array<Any?>): Scriptable {
                return createXHRObject(cx, scope)
            }
            override fun call(cx: Context, scope: Scriptable, thisObj: Scriptable, args: Array<Any?>): Any {
                return construct(cx, scope, args)
            }
        }
        ScriptableObject.putProperty(scope, "XMLHttpRequest", xhrConstructor)
    }

    private fun createXHRObject(cx: Context, scope: Scriptable): Scriptable {
        val xhr = cx.newObject(scope)

        // Internal state
        var method = "GET"
        var url = ""
        var async = true
        var requestHeaders = mutableMapOf<String, String>()
        var responseText = ""
        var status = 0
        var readyState = 0

        ScriptableObject.putProperty(xhr, "readyState", 0)
        ScriptableObject.putProperty(xhr, "status", 0)
        ScriptableObject.putProperty(xhr, "responseText", "")
        ScriptableObject.putProperty(xhr, "response", "")

        // open(method, url, async)
        ScriptableObject.putProperty(xhr, "open", object : BaseFunction() {
            override fun call(cx: Context, scope: Scriptable, thisObj: Scriptable, args: Array<Any?>): Any {
                method = Context.toString(args[0]).uppercase()
                url = Context.toString(args[1])
                async = if (args.size > 2) Context.toBoolean(args[2]) else true
                requestHeaders.clear()
                readyState = 1
                ScriptableObject.putProperty(xhr, "readyState", 1)
                return Undefined.instance
            }
        })

        // setRequestHeader(name, value)
        ScriptableObject.putProperty(xhr, "setRequestHeader", object : BaseFunction() {
            override fun call(cx: Context, scope: Scriptable, thisObj: Scriptable, args: Array<Any?>): Any {
                requestHeaders[Context.toString(args[0])] = Context.toString(args[1])
                return Undefined.instance
            }
        })

        // send(body)
        ScriptableObject.putProperty(xhr, "send", object : BaseFunction() {
            override fun call(cx: Context, scope: Scriptable, thisObj: Scriptable, args: Array<Any?>): Any {
                val body = if (args.isNotEmpty() && args[0] != null && args[0] != Undefined.instance)
                    Context.toString(args[0]) else null

                val doRequest = Runnable {
                    try {
                        val conn = URI(url).toURL().openConnection() as HttpURLConnection
                        conn.requestMethod = method
                        conn.connectTimeout = 10000
                        conn.readTimeout = 10000
                        for ((k, v) in requestHeaders) {
                            conn.setRequestProperty(k, v)
                        }
                        if (body != null && (method == "POST" || method == "PUT")) {
                            conn.doOutput = true
                            OutputStreamWriter(conn.outputStream).use { it.write(body) }
                        }
                        status = conn.responseCode
                        responseText = try {
                            BufferedReader(InputStreamReader(
                                if (status in 200..299) conn.inputStream else conn.errorStream
                            )).readText()
                        } catch (_: Exception) { "" }
                        readyState = 4

                        ScriptableObject.putProperty(xhr, "status", status)
                        ScriptableObject.putProperty(xhr, "responseText", responseText)
                        ScriptableObject.putProperty(xhr, "response", responseText)
                        ScriptableObject.putProperty(xhr, "readyState", 4)

                        // Fire callbacks
                        val onload = ScriptableObject.getProperty(xhr, "onload")
                        if (onload is JSFunction) {
                            if (async) {
                                pendingEvents.add(Runnable {
                                    val enterCx = Context.enter()
                                    enterCx.optimizationLevel = -1
                                    try {
                                        onload.call(enterCx, scope, xhr, arrayOf<Any>())
                                    } finally {
                                        Context.exit()
                                    }
                                })
                            } else {
                                onload.call(cx, scope, xhr, arrayOf<Any>())
                            }
                        }
                        val onreadystatechange = ScriptableObject.getProperty(xhr, "onreadystatechange")
                        if (onreadystatechange is JSFunction) {
                            if (async) {
                                pendingEvents.add(Runnable {
                                    val enterCx = Context.enter()
                                    enterCx.optimizationLevel = -1
                                    try {
                                        onreadystatechange.call(enterCx, scope, xhr, arrayOf<Any>())
                                    } finally {
                                        Context.exit()
                                    }
                                })
                            } else {
                                onreadystatechange.call(cx, scope, xhr, arrayOf<Any>())
                            }
                        }
                    } catch (e: Exception) {
                        System.err.println("[pkjs] XHR error: ${e.message}")
                        readyState = 4
                        ScriptableObject.putProperty(xhr, "readyState", 4)
                        val onerror = ScriptableObject.getProperty(xhr, "onerror")
                        if (onerror is JSFunction) {
                            if (async) {
                                pendingEvents.add(Runnable {
                                    val enterCx = Context.enter()
                                    enterCx.optimizationLevel = -1
                                    try {
                                        onerror.call(enterCx, scope, xhr, arrayOf<Any>())
                                    } finally {
                                        Context.exit()
                                    }
                                })
                            } else {
                                onerror.call(cx, scope, xhr, arrayOf<Any>())
                            }
                        }
                    }
                }

                if (async) {
                    Thread(doRequest).start()
                } else {
                    doRequest.run()
                }

                return Undefined.instance
            }
        })

        return xhr
    }

    private fun setupLocalStorage() {
        val cx = this.cx!!
        val scope = this.scope!!

        val ls = cx.newObject(scope)

        ScriptableObject.putProperty(ls, "getItem", object : BaseFunction() {
            override fun call(cx: Context, scope: Scriptable, thisObj: Scriptable, args: Array<Any?>): Any {
                val key = Context.toString(args[0])
                return localStorage[key] as Any? ?: UniqueTag.NULL_VALUE
            }
        })

        ScriptableObject.putProperty(ls, "setItem", object : BaseFunction() {
            override fun call(cx: Context, scope: Scriptable, thisObj: Scriptable, args: Array<Any?>): Any {
                val key = Context.toString(args[0])
                val value = Context.toString(args[1])
                localStorage[key] = value
                return Undefined.instance
            }
        })

        ScriptableObject.putProperty(ls, "removeItem", object : BaseFunction() {
            override fun call(cx: Context, scope: Scriptable, thisObj: Scriptable, args: Array<Any?>): Any {
                val key = Context.toString(args[0])
                localStorage.remove(key)
                return Undefined.instance
            }
        })

        ScriptableObject.putProperty(ls, "clear", object : BaseFunction() {
            override fun call(cx: Context, scope: Scriptable, thisObj: Scriptable, args: Array<Any?>): Any {
                localStorage.clear()
                return Undefined.instance
            }
        })

        ScriptableObject.putProperty(scope, "localStorage", ls)
    }

    private fun setupTimers() {
        val cx = this.cx!!
        val scope = this.scope!!

        // setTimeout(callback, delay)
        ScriptableObject.putProperty(scope, "setTimeout", object : BaseFunction() {
            override fun call(cx: Context, scope: Scriptable, thisObj: Scriptable, args: Array<Any?>): Any {
                val callback = args[0] as? JSFunction ?: return Undefined.instance
                val delay = if (args.size > 1) Context.toNumber(args[1]).toLong() else 0L

                Thread {
                    Thread.sleep(delay)
                    pendingEvents.add(Runnable {
                        val enterCx = Context.enter()
                        enterCx.optimizationLevel = -1
                        try {
                            callback.call(enterCx, scope, scope, arrayOf<Any>())
                        } catch (e: Exception) {
                            System.err.println("[pkjs] setTimeout error: ${e.message}")
                        } finally {
                            Context.exit()
                        }
                    })
                }.start()

                return 1.0 // timer id
            }
        })

        // setInterval(callback, delay) - simplified
        ScriptableObject.putProperty(scope, "setInterval", object : BaseFunction() {
            override fun call(cx: Context, scope: Scriptable, thisObj: Scriptable, args: Array<Any?>): Any {
                val callback = args[0] as? JSFunction ?: return Undefined.instance
                val delay = if (args.size > 1) Context.toNumber(args[1]).toLong() else 1000L

                val thread = Thread {
                    while (!Thread.currentThread().isInterrupted) {
                        Thread.sleep(delay)
                        pendingEvents.add(Runnable {
                            val enterCx = Context.enter()
                            enterCx.optimizationLevel = -1
                            try {
                                callback.call(enterCx, scope, scope, arrayOf<Any>())
                            } catch (e: Exception) {
                                System.err.println("[pkjs] setInterval error: ${e.message}")
                            } finally {
                                Context.exit()
                            }
                        })
                    }
                }
                thread.isDaemon = true
                thread.start()
                return 1.0
            }
        })

        // clearTimeout / clearInterval - stubs
        ScriptableObject.putProperty(scope, "clearTimeout", object : BaseFunction() {
            override fun call(cx: Context, scope: Scriptable, thisObj: Scriptable, args: Array<Any?>): Any = Undefined.instance
        })
        ScriptableObject.putProperty(scope, "clearInterval", object : BaseFunction() {
            override fun call(cx: Context, scope: Scriptable, thisObj: Scriptable, args: Array<Any?>): Any = Undefined.instance
        })
    }

    private fun sendAppMessageFromJS(
        cx: Context, scope: Scriptable,
        dict: Scriptable, successCb: JSFunction?, errorCb: JSFunction?
    ) {
        val noArgs = arrayOf<Any>()
        try {
            val tuples = mutableListOf<AppMessageTuple>()

            // Iterate over JS object keys
            val ids = dict.ids
            for (id in ids) {
                val key = when (id) {
                    is Number -> id.toInt().toUInt()
                    is String -> id.toUInt()
                    else -> continue
                }
                val value = when (id) {
                    is Number -> ScriptableObject.getProperty(dict, id.toInt())
                    is String -> ScriptableObject.getProperty(dict, id)
                    else -> continue
                }

                val tuple = when (value) {
                    is Number -> AppMessageTuple.createInt(key, value.toInt())
                    is String -> AppMessageTuple.createString(key, value)
                    is NativeArray -> {
                        val bytes = UByteArray(value.length.toInt()) {
                            Context.toNumber(value.get(it, value)).toInt().toUByte()
                        }
                        AppMessageTuple.createUByteArray(key, bytes)
                    }
                    else -> AppMessageTuple.createInt(key, Context.toNumber(value).toInt())
                }
                tuples.add(tuple)
            }

            val txId = nextTransactionId
            nextTransactionId = ((nextTransactionId.toInt() + 1) % 256).toUByte()

            val push = AppMessage.AppMessagePush(
                transactionId = txId,
                uuid = appUuid,
                tuples = tuples
            )
            bridge.sendPacket(push)
            System.err.println("[pkjs] Sent AppMessage with ${tuples.size} tuples")

            // Call success callback
            successCb?.call(cx, scope, scope, noArgs)
        } catch (e: Exception) {
            System.err.println("[pkjs] Failed to send AppMessage: ${e.message}")
            errorCb?.call(cx, scope, scope, noArgs)
        }
    }
}
