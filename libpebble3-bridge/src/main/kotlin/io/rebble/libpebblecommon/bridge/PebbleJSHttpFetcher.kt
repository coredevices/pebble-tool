package io.rebble.libpebblecommon.bridge

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import uniffi.library_rs.JsFetcher
import uniffi.library_rs.JsRequestKt
import uniffi.library_rs.JsResponseKt
import uniffi.library_rs.FetcherException
import java.net.HttpURLConnection
import java.net.URI

/**
 * Detect HTTP(S) proxy from environment variables (HTTP_PROXY, HTTPS_PROXY, etc.)
 * Returns a Proxy object or Proxy.NO_PROXY.
 */
fun detectProxy(url: String): java.net.Proxy {
    val isHttps = url.startsWith("https://", ignoreCase = true)

    val noProxy = System.getenv("NO_PROXY") ?: System.getenv("no_proxy") ?: ""
    if (noProxy.isNotEmpty()) {
        val host = try { URI(url).host ?: "" } catch (_: Exception) { "" }
        for (entry in noProxy.split(",")) {
            val pattern = entry.trim()
            if (pattern.isEmpty()) continue
            if (pattern == "*") return java.net.Proxy.NO_PROXY
            if (host == pattern || host.endsWith(pattern.removePrefix("*"))) {
                return java.net.Proxy.NO_PROXY
            }
        }
    }

    val proxyUrl = if (isHttps) {
        System.getenv("HTTPS_PROXY") ?: System.getenv("https_proxy")
    } else {
        System.getenv("HTTP_PROXY") ?: System.getenv("http_proxy")
    } ?: return java.net.Proxy.NO_PROXY

    return try {
        val proxyUri = URI(proxyUrl)
        val proxyHost = proxyUri.host ?: return java.net.Proxy.NO_PROXY
        val proxyPort = if (proxyUri.port > 0) proxyUri.port else 80

        val userInfo = proxyUri.userInfo
        if (userInfo != null) {
            val parts = userInfo.split(":", limit = 2)
            val user = parts[0]
            val pass = if (parts.size > 1) parts[1] else ""
            java.net.Authenticator.setDefault(object : java.net.Authenticator() {
                override fun getPasswordAuthentication(): java.net.PasswordAuthentication? {
                    if (requestorType == RequestorType.PROXY) {
                        return java.net.PasswordAuthentication(user, pass.toCharArray())
                    }
                    return null
                }
            })
            System.setProperty("jdk.http.auth.tunneling.disabledSchemes", "")
        }

        java.net.Proxy(java.net.Proxy.Type.HTTP, java.net.InetSocketAddress(proxyHost, proxyPort))
    } catch (_: Exception) {
        java.net.Proxy.NO_PROXY
    }
}

/**
 * JsFetcher implementation that delegates HTTP requests from the
 * JS fetch() API to Java's HttpURLConnection.
 * Automatically detects and uses HTTP_PROXY/HTTPS_PROXY env vars.
 *
 * Prepends response headers as JSON + \0 to the body since Boa's fetch()
 * doesn't expose response headers on the JS Response object.
 */
fun createJsFetcher(): JsFetcher = object : JsFetcher {
    override suspend fun fetch(request: JsRequestKt): JsResponseKt {
        try {
            return withContext(Dispatchers.IO) {
                val proxy = detectProxy(request.url)
                val conn = URI(request.url).toURL().openConnection(proxy) as HttpURLConnection
                conn.requestMethod = request.method
                conn.connectTimeout = 30000
                conn.readTimeout = 30000
                for ((k, v) in request.headers) {
                    conn.setRequestProperty(k, v)
                }
                if (request.body.isNotEmpty() &&
                    (request.method == "POST" || request.method == "PUT" || request.method == "PATCH")
                ) {
                    conn.doOutput = true
                    conn.outputStream.use { it.write(request.body) }
                }
                val status = conn.responseCode
                val body = try {
                    val stream = if (status in 200..299) conn.inputStream else conn.errorStream
                    stream?.readBytes() ?: ByteArray(0)
                } catch (_: Exception) {
                    ByteArray(0)
                }
                val headers = mutableMapOf<String, String>()
                conn.headerFields?.forEach { (key, values) ->
                    if (key != null && values != null) {
                        headers[key.lowercase()] = values.joinToString(", ")
                    }
                }
                val headersJson = buildString {
                    append("{")
                    headers.entries.forEachIndexed { i, (k, v) ->
                        if (i > 0) append(",")
                        append("\"")
                        append(k.replace("\"", "\\\""))
                        append("\":\"")
                        append(v.replace("\"", "\\\""))
                        append("\"")
                    }
                    append("}")
                }
                val prefixedBody = headersJson.toByteArray(Charsets.UTF_8) +
                    byteArrayOf(0) + body
                JsResponseKt(
                    status = status.toUShort(),
                    headers = headers,
                    body = prefixedBody
                )
            }
        } catch (e: FetcherException) {
            throw e
        } catch (e: Throwable) {
            throw FetcherException.NetworkException(
                "${e.javaClass.simpleName}: ${e.message ?: "unknown error"}"
            )
        }
    }
}
