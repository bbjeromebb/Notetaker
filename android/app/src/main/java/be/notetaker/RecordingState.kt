package be.notetaker

import kotlinx.coroutines.flow.MutableStateFlow

/** Gedeelde toestand tussen het scherm en de opnamedienst. */
object RecordingState {
    data class Snapshot(
        val recording: Boolean = false,
        val startedAtMillis: Long = 0L,
        val level: Float = 0f,
    )

    val state = MutableStateFlow(Snapshot())
    val title = MutableStateFlow("")
    val notes = MutableStateFlow("")
    val message = MutableStateFlow<String?>(null)
}
