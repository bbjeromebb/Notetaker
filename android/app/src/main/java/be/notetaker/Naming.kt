package be.notetaker

import java.time.LocalDateTime
import java.time.format.DateTimeFormatter
import kotlin.math.log10

/** Pure hulpfuncties zonder Android-afhankelijkheden, zodat ze op de JVM testbaar zijn. */
object Naming {
    private val STAMP: DateTimeFormatter = DateTimeFormatter.ofPattern("yyyy-MM-dd_HHmm")
    private val HUMAN: DateTimeFormatter = DateTimeFormatter.ofPattern("dd-MM-yyyy HH:mm")

    /** Zelfde regel als safe_filename() in de pc-app, zodat namen aan beide kanten overeenkomen. */
    fun safeFilename(text: String): String {
        val cleaned = text.replace(Regex("[^\\p{L}\\p{N}_\\- ]"), "").trim().replace(" ", "-")
        return cleaned.take(60).ifEmpty { "vergadering" }
    }

    fun stem(title: String, started: LocalDateTime): String =
        "${started.format(STAMP)}_${safeFilename(title)}"

    fun defaultTitle(started: LocalDateTime): String = "Vergadering ${started.format(HUMAN)}"

    fun jsonString(value: String): String = buildString {
        append('"')
        for (c in value) {
            when (c) {
                '"' -> append("\\\"")
                '\\' -> append("\\\\")
                '\n' -> append("\\n")
                '\r' -> append("\\r")
                '\t' -> append("\\t")
                else -> if (c < ' ') append("\\u%04x".format(c.code)) else append(c)
            }
        }
        append('"')
    }

    /** Metadata naast de opname; de pc leest title, started en notes. */
    fun sidecar(title: String, started: LocalDateTime, durationSeconds: Long, notes: String): String =
        "{\n" +
            "  \"title\": ${jsonString(title)},\n" +
            "  \"started\": ${jsonString(started.withNano(0).toString())},\n" +
            "  \"duration_seconds\": $durationSeconds,\n" +
            "  \"notes\": ${jsonString(notes)},\n" +
            "  \"source\": \"android\"\n" +
            "}\n"

    fun formatDuration(seconds: Long): String =
        "%d:%02d:%02d".format(seconds / 3600, seconds % 3600 / 60, seconds % 60)

    /** Amplitude (0..1) naar een logaritmische schaal, zoals het oor volume ervaart. */
    fun levelToHeight(amplitude: Float, floorDb: Float = -50f, ceilDb: Float = -10f): Float {
        if (amplitude <= 0f) return 0f
        val db = 20f * log10(amplitude)
        return ((db - floorDb) / (ceilDb - floorDb)).coerceIn(0f, 1f)
    }
}
