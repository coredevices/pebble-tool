package co.touchlab.kermit

/**
 * Minimal Logger stub compatible with Kermit API, using stderr for output.
 */
object Logger {
    var enabled = false

    fun withTag(tag: String): Logger = this

    inline fun v(message: () -> String) {
        if (enabled) System.err.println("[VERBOSE] ${message()}")
    }

    inline fun d(message: () -> String) {
        if (enabled) System.err.println("[DEBUG] ${message()}")
    }

    inline fun i(message: () -> String) {
        if (enabled) System.err.println("[INFO] ${message()}")
    }

    inline fun w(message: () -> String) {
        System.err.println("[WARN] ${message()}")
    }

    inline fun e(message: () -> String) {
        System.err.println("[ERROR] ${message()}")
    }

    fun e(message: String, throwable: Throwable) {
        System.err.println("[ERROR] $message: ${throwable.message}")
    }
}
