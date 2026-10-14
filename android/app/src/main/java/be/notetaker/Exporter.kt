package be.notetaker

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.util.Log
import androidx.documentfile.provider.DocumentFile
import java.io.File
import java.io.IOException
import java.time.LocalDateTime

/**
 * Opnames gaan eerst naar een wachtrij in de app en dan naar de inboxmap die Syncthing synchroniseert.
 * Elk bestand wordt onder een verborgen tijdelijke naam gekopieerd en pas daarna hernoemd, zodat de pc
 * nooit een half gekopieerde opname oppikt. De sidecar gaat eerst, de opname als laatste.
 */
object Exporter {
    private const val TAG = "Notetaker"
    private const val PREFS = "notetaker"
    private const val KEY_TREE = "inbox_tree"
    private const val KEY_HISTORY = "history"
    private const val MAX_HISTORY = 30
    private const val FAILED_SUFFIX = ".fout.txt"

    data class Entry(val name: String, val title: String, val status: String)

    private fun prefs(ctx: Context) = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    fun queueDir(ctx: Context): File = File(ctx.filesDir, "wachtrij").apply { mkdirs() }

    fun inboxUri(ctx: Context): Uri? = prefs(ctx).getString(KEY_TREE, null)?.let(Uri::parse)

    fun setInbox(ctx: Context, uri: Uri) {
        ctx.contentResolver.takePersistableUriPermission(
            uri, Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION,
        )
        prefs(ctx).edit().putString(KEY_TREE, uri.toString()).apply()
    }

    private fun inboxTree(ctx: Context): DocumentFile? =
        inboxUri(ctx)?.let { DocumentFile.fromTreeUri(ctx, it) }?.takeIf { it.canWrite() }

    fun inboxName(ctx: Context): String? = inboxUri(ctx)?.let { DocumentFile.fromTreeUri(ctx, it)?.name }

    /** Zet een afgewerkte opname met sidecar in de wachtrij. */
    fun enqueue(ctx: Context, recording: File, title: String, started: LocalDateTime,
                durationSeconds: Long, notes: String) {
        val dir = queueDir(ctx)
        val stem = Naming.stem(title, started)
        var name = stem
        var n = 2
        while (File(dir, "$name.ogg").exists()) name = "$stem-${n++}"
        File(dir, "$name.json").writeText(Naming.sidecar(title, started, durationSeconds, notes))
        if (!recording.renameTo(File(dir, "$name.ogg"))) throw IOException("Opname kon niet in de wachtrij")
        addHistory(ctx, name, title)
    }

    /** Stuurt alles uit de wachtrij naar de inboxmap. Geeft het aantal doorgestuurde opnames terug. */
    @Synchronized
    fun flush(ctx: Context): Int {
        val tree = inboxTree(ctx) ?: return 0
        val audios = queueDir(ctx).listFiles { f -> f.name.endsWith(".ogg") && !f.name.startsWith(".") }
            ?.sortedBy { it.name } ?: return 0
        var moved = 0
        for (audio in audios) {
            val name = audio.nameWithoutExtension
            val sidecar = File(audio.parentFile, "$name.json")
            try {
                if (sidecar.exists()) copyInto(ctx, tree, sidecar, "$name.json")
                copyInto(ctx, tree, audio, "$name.ogg")
                audio.delete()
                sidecar.delete()
                moved++
            } catch (e: Exception) {
                Log.w(TAG, "Doorsturen van $name mislukt", e)
            }
        }
        return moved
    }

    private fun copyInto(ctx: Context, tree: DocumentFile, src: File, finalName: String) {
        tree.findFile(finalName)?.delete()
        val tmpName = ".$finalName.notetaker-upload"
        tree.findFile(tmpName)?.delete()
        val tmp = tree.createFile("application/octet-stream", tmpName)
            ?: throw IOException("Kan $tmpName niet aanmaken")
        val out = ctx.contentResolver.openOutputStream(tmp.uri)
            ?: throw IOException("Kan $tmpName niet openen")
        out.use { o -> src.inputStream().use { it.copyTo(o) } }
        if (!tmp.renameTo(finalName)) throw IOException("Hernoemen naar $finalName mislukt")
    }

    private fun addHistory(ctx: Context, name: String, title: String) {
        val clean = title.replace('\t', ' ').replace('\n', ' ')
        val lines = listOf("$name\t$clean") + readHistory(ctx).map { "${it.first}\t${it.second}" }
        prefs(ctx).edit().putString(KEY_HISTORY, lines.take(MAX_HISTORY).joinToString("\n")).apply()
    }

    private fun readHistory(ctx: Context): List<Pair<String, String>> =
        (prefs(ctx).getString(KEY_HISTORY, "") ?: "").lines().filter { it.isNotBlank() }.map {
            val parts = it.split('\t', limit = 2)
            parts[0] to parts.getOrElse(1) { parts[0] }
        }

    /** Recente opnames met hun toestand. Leest de inboxmap: lees dit niet op de hoofdthread. */
    fun history(ctx: Context): List<Entry> {
        val queued = queueDir(ctx).list()?.toSet() ?: emptySet()
        val tree = inboxTree(ctx)
        val inInbox = tree?.listFiles()?.mapNotNull { it.name }?.toSet() ?: emptySet()
        return readHistory(ctx).map { (name, title) ->
            val status = when {
                "$name.ogg" in queued ->
                    if (tree == null) "Wacht: kies eerst een inboxmap" else "Wacht op doorsturen"
                "$name.ogg$FAILED_SUFFIX" in inInbox -> "Fout op de pc, zie $name.ogg$FAILED_SUFFIX"
                "$name.ogg" in inInbox -> "Doorgestuurd, wacht op de pc"
                tree == null -> "Onbekend: geen inboxmap"
                else -> "Verwerkt door de pc"
            }
            Entry(name, title, status)
        }
    }
}
