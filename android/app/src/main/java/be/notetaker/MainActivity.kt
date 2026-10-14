package be.notetaker

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.ContextCompat
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.withContext

private val Red = Color(0xFFDC2626)

class MainActivity : ComponentActivity() {
    private val refresh = MutableStateFlow(0)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            val colors = if (isSystemInDarkTheme()) darkColorScheme() else lightColorScheme()
            MaterialTheme(colorScheme = colors) {
                Surface(Modifier.fillMaxSize()) { NotetakerScreen(refresh) }
            }
        }
    }

    override fun onResume() {
        super.onResume()
        // Wachtrij doorsturen en de status van eerdere opnames bijwerken
        val ctx = applicationContext
        Thread {
            Exporter.flush(ctx)
            refresh.value = refresh.value + 1
        }.start()
    }
}

private fun hasMicPermission(ctx: Context) =
    ContextCompat.checkSelfPermission(ctx, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED

@Composable
fun NotetakerScreen(refresh: StateFlow<Int>) {
    val ctx = LocalContext.current
    val state by RecordingState.state.collectAsState()
    val title by RecordingState.title.collectAsState()
    val notes by RecordingState.notes.collectAsState()
    val message by RecordingState.message.collectAsState()
    val tick by refresh.collectAsState()
    var inboxName by remember { mutableStateOf(Exporter.inboxName(ctx)) }
    var history by remember { mutableStateOf(emptyList<Exporter.Entry>()) }
    var now by remember { mutableLongStateOf(System.currentTimeMillis()) }

    LaunchedEffect(tick, state.recording, inboxName, message) {
        history = withContext(Dispatchers.IO) { Exporter.history(ctx) }
    }
    LaunchedEffect(state.recording) {
        while (state.recording) {
            now = System.currentTimeMillis()
            delay(500)
        }
    }

    val pickFolder = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocumentTree()) { uri ->
        if (uri != null) {
            Exporter.setInbox(ctx, uri)
            inboxName = Exporter.inboxName(ctx)
            Thread {
                val sent = Exporter.flush(ctx)
                if (sent > 0) RecordingState.message.value = "$sent opname(s) doorgestuurd"
            }.start()
        }
    }
    val permissions = buildList {
        add(Manifest.permission.RECORD_AUDIO)
        if (Build.VERSION.SDK_INT >= 33) add(Manifest.permission.POST_NOTIFICATIONS)
    }.toTypedArray()
    val askPermissions = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions(),
    ) { result ->
        if (result[Manifest.permission.RECORD_AUDIO] == true) {
            RecorderService.start(ctx)
        } else {
            RecordingState.message.value = "Zonder toegang tot de microfoon kan Notetaker niet opnemen"
        }
    }

    Column(
        Modifier
            .fillMaxSize()
            .safeDrawingPadding()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        Text("Notetaker", style = MaterialTheme.typography.headlineSmall)

        Card(Modifier.fillMaxWidth()) {
            Row(Modifier.padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text("Inboxmap (Syncthing)", style = MaterialTheme.typography.labelMedium)
                    Text(inboxName ?: "Nog niet gekozen", style = MaterialTheme.typography.bodyLarge)
                    if (inboxName == null) {
                        Text(
                            "Tip: kies de map Notetaker in je Obsidian-vault",
                            style = MaterialTheme.typography.bodySmall,
                        )
                    }
                }
                TextButton(onClick = { pickFolder.launch(null) }) {
                    Text(if (inboxName == null) "Kiezen" else "Wijzigen")
                }
            }
        }

        OutlinedTextField(
            value = title,
            onValueChange = { RecordingState.title.value = it },
            label = { Text("Titel") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )

        Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.Center) {
            Button(
                onClick = {
                    when {
                        state.recording -> RecorderService.stop(ctx)
                        hasMicPermission(ctx) -> RecorderService.start(ctx)
                        else -> askPermissions.launch(permissions)
                    }
                },
                shape = CircleShape,
                modifier = Modifier.size(132.dp),
                colors = ButtonDefaults.buttonColors(
                    containerColor = if (state.recording) Red else MaterialTheme.colorScheme.primary,
                ),
            ) {
                Text(if (state.recording) "Stop" else "Opnemen", fontSize = 18.sp)
            }
        }

        if (state.recording) {
            Text(
                Naming.formatDuration(((now - state.startedAtMillis) / 1000).coerceAtLeast(0)),
                style = MaterialTheme.typography.headlineMedium,
                modifier = Modifier.align(Alignment.CenterHorizontally),
            )
            LinearProgressIndicator(
                progress = { Naming.levelToHeight(state.level) },
                color = Red,
                modifier = Modifier.fillMaxWidth(),
            )
        }

        message?.let {
            Text(it, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.primary)
        }

        OutlinedTextField(
            value = notes,
            onValueChange = { RecordingState.notes.value = it },
            label = { Text("Mijn notities") },
            minLines = 4,
            modifier = Modifier.fillMaxWidth(),
        )

        Text(
            "Online vergadering op deze telefoon? Zet de luidspreker aan: Android laat apps het " +
                "gespreksgeluid van Teams of Zoom niet rechtstreeks opnemen.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )

        if (history.isNotEmpty()) {
            HorizontalDivider()
            Text("Recente opnames", style = MaterialTheme.typography.titleMedium)
            history.forEach { entry ->
                Column {
                    Text(entry.title, style = MaterialTheme.typography.bodyLarge)
                    Text(
                        entry.status,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }
    }
}
