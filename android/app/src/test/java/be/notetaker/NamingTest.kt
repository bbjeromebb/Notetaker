package be.notetaker

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDateTime

class NamingTest {
    private val start = LocalDateTime.of(2026, 10, 14, 9, 30, 15, 123)

    @Test
    fun safeFilenameMatchesPcRule() {
        assertEquals("Overleg-RUP--Kortrijk", Naming.safeFilename("Overleg: RUP / Kortrijk"))
        assertEquals("Café-déjà_vu", Naming.safeFilename("Café déjà_vu!"))
        assertEquals("vergadering", Naming.safeFilename("???"))
        assertEquals(60, Naming.safeFilename("x".repeat(100)).length)
    }

    @Test
    fun stemHasTimestampAndTitle() {
        assertEquals("2026-10-14_0930_Werfoverleg", Naming.stem("Werfoverleg", start))
    }

    @Test
    fun sidecarIsValidJson() {
        val json = Naming.sidecar("Titel \"x\"", start, 125, "regel 1\nregel\t2 \\ einde")
        assertTrue(json.contains("\"title\": \"Titel \\\"x\\\"\""))
        assertTrue(json.contains("\"started\": \"2026-10-14T09:30:15\""))
        assertTrue(json.contains("\"duration_seconds\": 125"))
        assertTrue(json.contains("\"notes\": \"regel 1\\nregel\\t2 \\\\ einde\""))
    }

    @Test
    fun durationAndLevel() {
        assertEquals("1:02:03", Naming.formatDuration(3723))
        assertEquals(0f, Naming.levelToHeight(0f), 0f)
        assertEquals(1f, Naming.levelToHeight(1f), 0f)
        assertEquals(0.25f, Naming.levelToHeight(0.01f), 0.001f)
    }
}
