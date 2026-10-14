package be.notetaker

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.graphics.drawable.Icon
import android.media.MediaRecorder
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.util.Log
import java.io.File
import java.time.LocalDateTime

/**
 * Opname als voorgronddienst: blijft lopen met het scherm uit of in een andere app.
 * Opus in Ogg, mono 16 kHz, 32 kbit/s: ongeveer 15 MB per uur, hetzelfde formaat als op de pc.
 */
class RecorderService : Service() {

    companion object {
        private const val TAG = "Notetaker"
        private const val ACTION_START = "be.notetaker.START"
        private const val ACTION_STOP = "be.notetaker.STOP"
        private const val CHANNEL = "opname"
        private const val NOTIFICATION_ID = 1

        fun start(ctx: Context) {
            ctx.startForegroundService(Intent(ctx, RecorderService::class.java).setAction(ACTION_START))
        }

        fun stop(ctx: Context) {
            ctx.startService(Intent(ctx, RecorderService::class.java).setAction(ACTION_STOP))
        }
    }

    private var recorder: MediaRecorder? = null
    private var output: File? = null
    private var started: LocalDateTime? = null
    private val handler = Handler(Looper.getMainLooper())

    private val pollLevel = object : Runnable {
        override fun run() {
            val r = recorder ?: return
            val amplitude = try { r.maxAmplitude } catch (e: RuntimeException) { 0 }
            val s = RecordingState.state.value
            RecordingState.state.value = s.copy(level = (amplitude / 32767f).coerceIn(0f, 1f))
            handler.postDelayed(this, 100)
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_START -> startRecording()
            ACTION_STOP -> stopRecording()
        }
        return START_NOT_STICKY
    }

    private fun startRecording() {
        if (recorder != null) return
        createChannel()
        startForeground(NOTIFICATION_ID, buildNotification(), ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE)

        val now = LocalDateTime.now()
        val file = File(Exporter.queueDir(this), ".opname-bezig.ogg").apply { delete() }
        val r = if (Build.VERSION.SDK_INT >= 31) MediaRecorder(this) else @Suppress("DEPRECATION") MediaRecorder()
        try {
            r.setAudioSource(MediaRecorder.AudioSource.MIC)
            r.setOutputFormat(MediaRecorder.OutputFormat.OGG)
            r.setAudioEncoder(MediaRecorder.AudioEncoder.OPUS)
            r.setAudioChannels(1)
            r.setAudioSamplingRate(16000)
            r.setAudioEncodingBitRate(32000)
            r.setOutputFile(file.absolutePath)
            r.prepare()
            r.start()
        } catch (e: Exception) {
            Log.e(TAG, "Opname starten mislukt", e)
            r.release()
            RecordingState.message.value = "Opname starten mislukt: ${e.message}"
            stopForeground(STOP_FOREGROUND_REMOVE)
            stopSelf()
            return
        }
        recorder = r
        output = file
        started = now
        if (RecordingState.title.value.isBlank()) RecordingState.title.value = Naming.defaultTitle(now)
        RecordingState.state.value = RecordingState.Snapshot(true, System.currentTimeMillis(), 0f)
        handler.post(pollLevel)
    }

    private fun stopRecording() {
        val r = recorder
        if (r == null) {
            stopSelf()
            return
        }
        handler.removeCallbacks(pollLevel)
        val ok = try {
            r.stop()
            true
        } catch (e: RuntimeException) {
            false // gebeurt bij een opname van minder dan een seconde
        }
        r.release()
        recorder = null

        val begin = started ?: LocalDateTime.now()
        val seconds = (System.currentTimeMillis() - RecordingState.state.value.startedAtMillis) / 1000
        RecordingState.state.value = RecordingState.Snapshot()
        val file = output
        val title = RecordingState.title.value.ifBlank { Naming.defaultTitle(begin) }
        val notes = RecordingState.notes.value

        if (ok && file != null && file.length() > 0) {
            RecordingState.title.value = ""
            RecordingState.notes.value = ""
            val ctx = applicationContext
            Thread {
                try {
                    Exporter.enqueue(ctx, file, title, begin, seconds, notes)
                    val sent = Exporter.flush(ctx)
                    RecordingState.message.value =
                        if (sent > 0) "Opname doorgestuurd naar de pc" else "Opname bewaard; wacht op de inboxmap"
                } catch (e: Exception) {
                    Log.e(TAG, "Opname bewaren mislukt", e)
                    RecordingState.message.value = "Opname bewaren mislukt: ${e.message}"
                }
            }.start()
        } else {
            file?.delete()
            RecordingState.message.value = "Opname te kort, niet bewaard"
        }
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    override fun onDestroy() {
        handler.removeCallbacks(pollLevel)
        recorder?.let {
            try { it.stop() } catch (_: RuntimeException) { }
            it.release()
        }
        recorder = null
        super.onDestroy()
    }

    private fun createChannel() {
        val manager = getSystemService(NotificationManager::class.java)
        if (manager.getNotificationChannel(CHANNEL) == null) {
            manager.createNotificationChannel(
                NotificationChannel(CHANNEL, "Opname", NotificationManager.IMPORTANCE_LOW).apply {
                    description = "Melding zolang Notetaker opneemt"
                },
            )
        }
    }

    private fun buildNotification(): Notification {
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE,
        )
        val stop = PendingIntent.getService(
            this, 1, Intent(this, RecorderService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_IMMUTABLE,
        )
        val stopAction = Notification.Action.Builder(
            Icon.createWithResource(this, R.drawable.ic_mic), "Stoppen", stop,
        ).build()
        return Notification.Builder(this, CHANNEL)
            .setContentTitle("Notetaker neemt op")
            .setContentText("Tik om notities toe te voegen")
            .setSmallIcon(R.drawable.ic_mic)
            .setOngoing(true)
            .setContentIntent(open)
            .addAction(stopAction)
            .build()
    }
}
