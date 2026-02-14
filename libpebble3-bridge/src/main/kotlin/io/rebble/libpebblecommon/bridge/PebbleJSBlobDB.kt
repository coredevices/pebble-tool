package io.rebble.libpebblecommon.bridge

import coredev.BlobDatabase
import io.rebble.libpebblecommon.packets.blobdb.BlobCommand
import io.rebble.libpebblecommon.packets.blobdb.TimelineAttribute
import io.rebble.libpebblecommon.packets.blobdb.TimelineItem
import kotlinx.serialization.json.*
import kotlin.uuid.Uuid

/**
 * BlobDB operations for the PKJS runtime: sending notifications and
 * app glances to the watch.
 */
object PebbleJSBlobDB {

    /**
     * Send a real notification to the watch via BlobDB.
     */
    fun sendNotification(bridge: QemuBridge, appUuid: Uuid, title: String, body: String) {
        try {
            val notifUuid = Uuid.random()
            val now = (System.currentTimeMillis() / 1000).toUInt()

            val attributes = mutableListOf<TimelineItem.Attribute>()
            attributes.add(TimelineItem.Attribute(
                TimelineAttribute.Title.id,
                title.toByteArray(Charsets.UTF_8).toUByteArray()
            ))
            attributes.add(TimelineItem.Attribute(
                TimelineAttribute.Body.id,
                body.toByteArray(Charsets.UTF_8).toUByteArray()
            ))
            attributes.add(TimelineItem.Attribute(
                TimelineAttribute.Sender.id,
                "PebbleKit JS".toByteArray(Charsets.UTF_8).toUByteArray()
            ))

            val timelineItem = TimelineItem(
                itemId = notifUuid,
                parentId = appUuid,
                timestampSecs = now,
                duration = 0u,
                type = TimelineItem.Type.Notification,
                flags = TimelineItem.Flag.makeFlags(listOf(TimelineItem.Flag.IS_VISIBLE)),
                layout = TimelineItem.Layout.GenericNotification,
                attributes = attributes,
                actions = listOf(
                    TimelineItem.Action(
                        0u,
                        TimelineItem.Action.Type.Dismiss,
                        listOf(TimelineItem.Attribute(
                            TimelineAttribute.Title.id,
                            "Dismiss".toByteArray(Charsets.UTF_8).toUByteArray()
                        ))
                    )
                )
            )

            val itemBytes = timelineItem.toBytes()
            val uuidBytes = notifUuid.toByteArray().toUByteArray()
            val token = (System.currentTimeMillis() % 65536).toUShort()

            val insertCmd = BlobCommand.InsertCommand(
                token = token,
                database = BlobDatabase.Notification,
                key = uuidBytes,
                value = itemBytes
            )
            bridge.sendPacket(insertCmd)
            System.err.println("[pkjs] Sent notification to watch: $title")
        } catch (e: Exception) {
            System.err.println("[pkjs] Failed to send notification: ${e.message}")
        }
    }

    /**
     * Send app glance data to the watch via BlobDB.
     */
    fun sendAppGlance(bridge: QemuBridge, appUuid: Uuid, slices: JsonArray) {
        try {
            // Build the app glance binary payload:
            // Format: [version:u8=1][created_at:u32LE][num_slices:u8][slices...]
            // Each slice: [type:u8=0][expiration:u32LE][num_attributes:u8][attributes...]
            // Each attribute: [id:u8][length:u16LE][data...]
            val buf = java.io.ByteArrayOutputStream()
            buf.write(1) // version
            val now = (System.currentTimeMillis() / 1000).toInt()
            buf.write(now and 0xFF)
            buf.write((now shr 8) and 0xFF)
            buf.write((now shr 16) and 0xFF)
            buf.write((now shr 24) and 0xFF)
            buf.write(slices.size) // num_slices

            for (slice in slices) {
                val sliceObj = slice.jsonObject
                buf.write(0) // type = icon-subtitle
                // expiration = 0 (no expiration)
                buf.write(0); buf.write(0); buf.write(0); buf.write(0)

                val layout = sliceObj["layout"]?.jsonObject
                val attrs = mutableListOf<Pair<UByte, ByteArray>>()
                if (layout != null) {
                    val icon = layout["icon"]?.jsonPrimitive?.content
                    if (icon != null) {
                        attrs.add(TimelineAttribute.Icon.id to icon.toByteArray(Charsets.UTF_8))
                    }
                    val subtitle = layout["subtitleTemplateString"]?.jsonPrimitive?.content
                    if (subtitle != null) {
                        attrs.add(TimelineAttribute.SubtitleTemplateString.id to subtitle.toByteArray(Charsets.UTF_8))
                    }
                }
                buf.write(attrs.size)
                for ((id, data) in attrs) {
                    buf.write(id.toInt())
                    buf.write(data.size and 0xFF)
                    buf.write((data.size shr 8) and 0xFF)
                    buf.write(data)
                }
            }

            val glanceBytes = buf.toByteArray().toUByteArray()
            val uuidBytes = appUuid.toByteArray().toUByteArray()
            val token = (System.currentTimeMillis() % 65536).toUShort()

            val insertCmd = BlobCommand.InsertCommand(
                token = token,
                database = BlobDatabase.AppGlance,
                key = uuidBytes,
                value = glanceBytes
            )
            bridge.sendPacket(insertCmd)
            System.err.println("[pkjs] Sent app glance to watch (${slices.size} slices)")
        } catch (e: Exception) {
            System.err.println("[pkjs] Failed to send app glance: ${e.message}")
        }
    }
}
