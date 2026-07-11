package com.lengrvis.approval

import android.content.Context
import com.facebook.react.modules.network.OkHttpClientFactory
import com.facebook.react.modules.network.OkHttpClientProvider
import java.net.URL
import java.security.KeyStore
import java.security.MessageDigest
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone
import java.util.UUID
import javax.net.ssl.HostnameVerifier
import javax.net.ssl.HttpsURLConnection
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLPeerUnverifiedException
import javax.net.ssl.SSLSession
import javax.net.ssl.TrustManager
import javax.net.ssl.TrustManagerFactory
import javax.net.ssl.X509TrustManager
import okhttp3.OkHttpClient
import okhttp3.Interceptor
import org.json.JSONArray
import org.json.JSONObject

object LengrvisLanTrust {
  private const val PREFS_NAME = "lengrvis_lan_tls_trust"
  private const val RECORDS_KEY = "tls_pin_records_v1"
  private const val LEGACY_PINS_KEY = "pinned_certificate_sha256_by_host"
  private const val CORRUPT_STATE_KEY = "tls_pin_store_corrupt_v1"
  private const val CORRUPT_STATE_VALUE = "corrupt-v1"
  private const val GOVERNED_STATE_KEY = "tls_pin_store_governed_v1"
  private const val GOVERNED_STATE_VALUE = "governed-v1"
  private const val RECORD_SCHEMA = "tls-pin-record-v1"
  private const val STATUS_ACTIVE = "active"
  private const val STATUS_NEXT = "next"
  private const val STATUS_REVOKED = "revoked"
  private const val MAX_RECORDS_PER_ORIGIN = 8
  private const val DEFAULT_ACTIVE_TTL_MS = 30L * 24 * 60 * 60 * 1000
  private const val DEFAULT_NEXT_TTL_MS = 24L * 60 * 60 * 1000
  private const val MAX_RECORD_LIFETIME_MS = DEFAULT_ACTIVE_TTL_MS + DEFAULT_NEXT_TTL_MS
  private const val TIMESTAMP_CLOCK_SKEW_MS = 5L * 60 * 1000
  private const val CORRUPT_STORE_MESSAGE =
    "LAN TLS pin state is corrupt or legacy. Clear mobile TLS trust and pair again."
  private val lock = Any()

  fun install(context: Context) {
    OkHttpClientProvider.setOkHttpClientFactory(LengrvisOkHttpClientFactory(context.applicationContext))
  }

  fun trustServerCertificate(context: Context, baseUrl: String, fingerprintSha256: String): JSONObject {
    val now = System.currentTimeMillis()
    return stageServerCertificate(
      context = context,
      baseUrl = baseUrl,
      fingerprintSha256 = fingerprintSha256,
      activeExpiresAtEpochMs = now + DEFAULT_ACTIVE_TTL_MS,
      nextExpiresAtEpochMs = now + DEFAULT_NEXT_TTL_MS,
      sourceDeviceId = null,
    )
  }

  fun stageServerCertificate(
    context: Context,
    baseUrl: String,
    fingerprintSha256: String,
    activeExpiresAtEpochMs: Long,
    nextExpiresAtEpochMs: Long,
    sourceDeviceId: String?,
  ): JSONObject {
    val origin = normalizeHttpsOrigin(baseUrl)
    val host = originHost(origin)
    val fingerprint = requireFingerprint(fingerprintSha256)
    val now = System.currentTimeMillis()
    require(activeExpiresAtEpochMs > now) { "Active TLS pin expiry must be in the future." }
    require(nextExpiresAtEpochMs > now) { "Next TLS pin expiry must be in the future." }
    require(activeExpiresAtEpochMs <= now + DEFAULT_ACTIVE_TTL_MS) {
      "Active TLS pin lifetime cannot exceed 30 days."
    }
    require(nextExpiresAtEpochMs <= now + DEFAULT_NEXT_TTL_MS) {
      "Next TLS pin overlap cannot exceed 24 hours."
    }

    synchronized(lock) {
      val records = readRecordsLocked(context)
      records.firstOrNull {
        it.origin == origin && it.fingerprintSha256 == fingerprint && it.isUsable(now)
      }?.let { return it.toJson() }

      val hasActive = records.any { it.origin == origin && it.status == STATUS_ACTIVE && it.isUsable(now) }
      val status = if (hasActive) STATUS_NEXT else STATUS_ACTIVE
      if (status == STATUS_NEXT) {
        revokeMatching(records, now) { it.origin == origin && it.status == STATUS_NEXT && it.isUsable(now) }
      } else {
        revokeMatching(records, now) { it.origin == origin && it.isUsable(now) }
      }
      val record = StoredTlsPinRecord(
        pinId = UUID.randomUUID().toString(),
        origin = origin,
        host = host,
        fingerprintSha256 = fingerprint,
        status = status,
        createdAtEpochMs = now,
        expiresAtEpochMs = if (status == STATUS_ACTIVE) activeExpiresAtEpochMs else nextExpiresAtEpochMs,
        sourceDeviceId = normalizeSourceDeviceId(sourceDeviceId),
        revokedAtEpochMs = null,
      )
      records.add(record)
      writeRecordsLocked(context, pruneRecords(records))
      return record.toJson()
    }
  }

  fun assertServerCertificateTrusted(context: Context, baseUrl: String, fingerprintSha256: String): JSONObject {
    val origin = normalizeHttpsOrigin(baseUrl)
    val fingerprint = requireFingerprint(fingerprintSha256)
    synchronized(lock) {
      val now = System.currentTimeMillis()
      val record = readRecordsLocked(context).firstOrNull {
        it.origin == origin && it.fingerprintSha256 == fingerprint && it.isUsable(now)
      } ?: throw SSLPeerUnverifiedException("LAN TLS certificate pin is missing, expired, or revoked for $origin.")
      return record.toJson()
    }
  }

  fun activateServerCertificate(
    context: Context,
    baseUrl: String,
    fingerprintSha256: String,
    activeExpiresAtEpochMs: Long,
    sourceDeviceId: String?,
  ): JSONObject {
    val origin = normalizeHttpsOrigin(baseUrl)
    val fingerprint = requireFingerprint(fingerprintSha256)
    val now = System.currentTimeMillis()
    require(activeExpiresAtEpochMs > now) { "Active TLS pin expiry must be in the future." }
    require(activeExpiresAtEpochMs <= now + DEFAULT_ACTIVE_TTL_MS) {
      "Active TLS pin lifetime cannot exceed 30 days."
    }
    synchronized(lock) {
      val records = readRecordsLocked(context)
      val index = records.indexOfFirst {
        it.origin == origin && it.fingerprintSha256 == fingerprint && it.isUsable(now)
      }
      if (index < 0) {
        throw SSLPeerUnverifiedException("LAN TLS certificate pin cannot be activated because it is missing, expired, or revoked for $origin.")
      }
      revokeMatching(records, now) {
        it.origin == origin && it.fingerprintSha256 != fingerprint && it.isUsable(now)
      }
      val current = records[index]
      val activated = current.copy(
        status = STATUS_ACTIVE,
        expiresAtEpochMs = if (current.status == STATUS_NEXT) activeExpiresAtEpochMs else current.expiresAtEpochMs,
        sourceDeviceId = normalizeSourceDeviceId(sourceDeviceId) ?: current.sourceDeviceId,
        revokedAtEpochMs = null,
      )
      records[index] = activated
      writeRecordsLocked(context, pruneRecords(records))
      return activated.toJson()
    }
  }

  fun revokeServerCertificate(context: Context, baseUrl: String, fingerprintSha256: String): Boolean {
    val origin = normalizeHttpsOrigin(baseUrl)
    val fingerprint = requireFingerprint(fingerprintSha256)
    synchronized(lock) {
      val records = readRecordsLocked(context)
      val changed = revokeMatching(records, System.currentTimeMillis()) {
        it.origin == origin && it.fingerprintSha256 == fingerprint && it.status != STATUS_REVOKED
      }
      if (changed) writeRecordsLocked(context, pruneRecords(records))
      return changed
    }
  }

  fun listServerCertificatePins(context: Context, baseUrl: String, includeRevoked: Boolean): JSONArray {
    val origin = normalizeHttpsOrigin(baseUrl)
    synchronized(lock) {
      val now = System.currentTimeMillis()
      val result = JSONArray()
      readRecordsLocked(context)
        .filter { it.origin == origin && (includeRevoked || it.isUsable(now)) }
        .sortedBy { it.createdAtEpochMs }
        .forEach { result.put(it.toJson()) }
      return result
    }
  }

  fun clearTrustedServers(context: Context) {
    synchronized(lock) {
      check(
        prefs(context).edit()
          .remove(RECORDS_KEY)
          .remove(LEGACY_PINS_KEY)
          .remove(CORRUPT_STATE_KEY)
          .remove(GOVERNED_STATE_KEY)
          .commit(),
      ) {
        "Failed to clear LAN TLS trust records."
      }
    }
  }

  fun assertRequestTrustStateHealthy(context: Context, origin: String) {
    val normalizedOrigin = normalizeHttpsOrigin(origin)
    synchronized(lock) {
      try {
        readRecordsLocked(context)
      } catch (error: CorruptTlsPinStoreException) {
        throw SSLPeerUnverifiedException("$CORRUPT_STORE_MESSAGE Origin: $normalizedOrigin").also {
          it.initCause(error)
        }
      }
    }
  }

  fun hasAnyFingerprint(context: Context, fingerprintSha256: String): Boolean {
    val fingerprint = normalizeFingerprint(fingerprintSha256)
    if (fingerprint.isBlank()) return false
    synchronized(lock) {
      val now = System.currentTimeMillis()
      return readRecordsLocked(context).any { it.fingerprintSha256 == fingerprint && it.isUsable(now) }
    }
  }

  fun hostHasFingerprint(context: Context, host: String?, fingerprintSha256: String): Boolean {
    val normalizedHost = normalizeHost(host)
    val fingerprint = normalizeFingerprint(fingerprintSha256)
    if (normalizedHost.isBlank() || fingerprint.isBlank()) return false
    synchronized(lock) {
      val now = System.currentTimeMillis()
      return readRecordsLocked(context).any {
        it.host == normalizedHost && it.fingerprintSha256 == fingerprint && it.isUsable(now)
      }
    }
  }

  fun hostHasAnyFingerprintForHost(context: Context, host: String?): Boolean {
    val normalizedHost = normalizeHost(host)
    if (normalizedHost.isBlank()) return false
    synchronized(lock) {
      val now = System.currentTimeMillis()
      return readRecordsLocked(context).any { it.host == normalizedHost && it.isUsable(now) }
    }
  }

  fun originHasFingerprint(context: Context, origin: String, fingerprintSha256: String): Boolean {
    val normalizedOrigin = normalizeHttpsOrigin(origin)
    val fingerprint = normalizeFingerprint(fingerprintSha256)
    if (fingerprint.isBlank()) return false
    synchronized(lock) {
      val now = System.currentTimeMillis()
      return readRecordsLocked(context).any {
        it.origin == normalizedOrigin && it.fingerprintSha256 == fingerprint && it.isUsable(now)
      }
    }
  }

  private fun prefs(context: Context) = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

  private fun readRecordsLocked(context: Context): MutableList<StoredTlsPinRecord> {
    val preferences = prefs(context)
    val values = try {
      preferences.all
    } catch (error: Exception) {
      failCorruptStoreLocked(preferences, error)
    }

    if (values.containsKey(CORRUPT_STATE_KEY)) {
      throw CorruptTlsPinStoreException()
    }
    if (values.containsKey(LEGACY_PINS_KEY)) {
      failCorruptStoreLocked(preferences)
    }

    val governedState = values[GOVERNED_STATE_KEY]
    if (governedState != null && governedState != GOVERNED_STATE_VALUE) {
      failCorruptStoreLocked(preferences)
    }
    val wasGoverned = governedState == GOVERNED_STATE_VALUE
    val rawValue = values[RECORDS_KEY]
    if (rawValue == null) {
      if (wasGoverned) failCorruptStoreLocked(preferences)
      return mutableListOf()
    }
    if (rawValue !is String || rawValue.isBlank()) {
      failCorruptStoreLocked(preferences)
    }

    val records = try {
      val raw = rawValue
      val array = JSONArray(raw)
      MutableList(array.length()) { index -> StoredTlsPinRecord.fromJson(array.getJSONObject(index)) }
        .also(::validateRecordSet)
    } catch (error: Exception) {
      failCorruptStoreLocked(preferences, error)
    }
    if (records.isEmpty() && wasGoverned) {
      failCorruptStoreLocked(preferences)
    }
    if (records.isNotEmpty() && !wasGoverned) {
      check(preferences.edit().putString(GOVERNED_STATE_KEY, GOVERNED_STATE_VALUE).commit()) {
        "Failed to persist governed LAN TLS trust state."
      }
    }
    return records
  }

  private fun validateRecordSet(records: List<StoredTlsPinRecord>) {
    val now = System.currentTimeMillis()
    require(records.map { it.pinId }.toSet().size == records.size) {
      "TLS pin records contain duplicate identifiers."
    }
    records.groupBy { it.origin }.forEach { (_, originRecords) ->
      val usable = originRecords.filter { it.isUsable(now) }
      require(usable.size <= 2) { "TLS pin origin contains too many usable pins." }
      require(usable.count { it.status == STATUS_ACTIVE } <= 1) { "TLS pin origin contains multiple active pins." }
      require(usable.count { it.status == STATUS_NEXT } <= 1) { "TLS pin origin contains multiple next pins." }
      require(usable.map { it.fingerprintSha256 }.toSet().size == usable.size) {
        "TLS pin origin contains duplicate usable fingerprints."
      }
    }
  }

  private fun writeRecordsLocked(context: Context, records: List<StoredTlsPinRecord>) {
    val payload = JSONArray()
    records.forEach { payload.put(it.toJson()) }
    check(
      prefs(context).edit()
        .putString(RECORDS_KEY, payload.toString())
        .putString(GOVERNED_STATE_KEY, GOVERNED_STATE_VALUE)
        .commit(),
    ) {
      "Failed to persist LAN TLS trust records."
    }
  }

  private fun failCorruptStoreLocked(
    preferences: android.content.SharedPreferences,
    cause: Throwable? = null,
  ): Nothing {
    try {
      preferences.edit().putString(CORRUPT_STATE_KEY, CORRUPT_STATE_VALUE).commit()
    } catch (_: Exception) {
      // The malformed primary state remains enough to fail closed on the next read.
    }
    throw CorruptTlsPinStoreException(cause)
  }

  private fun pruneRecords(records: List<StoredTlsPinRecord>): MutableList<StoredTlsPinRecord> {
    val retained = mutableListOf<StoredTlsPinRecord>()
    records.groupBy { it.origin }.values.forEach { originRecords ->
      retained.addAll(originRecords.sortedByDescending { it.createdAtEpochMs }.take(MAX_RECORDS_PER_ORIGIN))
    }
    return retained.sortedBy { it.createdAtEpochMs }.toMutableList()
  }

  private fun revokeMatching(
    records: MutableList<StoredTlsPinRecord>,
    revokedAtEpochMs: Long,
    predicate: (StoredTlsPinRecord) -> Boolean,
  ): Boolean {
    var changed = false
    records.indices.forEach { index ->
      val record = records[index]
      if (predicate(record)) {
        records[index] = record.copy(status = STATUS_REVOKED, revokedAtEpochMs = revokedAtEpochMs)
        changed = true
      }
    }
    return changed
  }

  private fun normalizeFingerprint(value: String): String =
    value.trim().replace(":", "").replace(Regex("\\s+"), "").lowercase()

  private fun requireFingerprint(value: String): String {
    val fingerprint = normalizeFingerprint(value)
    require(fingerprint.matches(Regex("^[a-f0-9]{64}$"))) {
      "Certificate SHA-256 fingerprint must contain 64 hex characters."
    }
    return fingerprint
  }

  private fun normalizeHost(value: String?): String =
    (value ?: "").trim().trim('[', ']').lowercase()

  private fun normalizeHttpsOrigin(value: String): String {
    val url = URL(value)
    require(url.protocol.equals("https", ignoreCase = true)) {
      "LAN certificate pins are only accepted for HTTPS origins."
    }
    require(url.userInfo.isNullOrBlank() && url.query.isNullOrBlank() && url.ref.isNullOrBlank()) {
      "LAN certificate pin origin must not include credentials, query, or fragment."
    }
    require(url.path.isNullOrBlank() || url.path == "/") {
      "LAN certificate pins must be scoped to an HTTPS origin, not a path."
    }
    require(url.port == -1 || url.port in 1..65535) { "LAN certificate pin origin has an invalid port." }
    val host = normalizeHost(url.host)
    require(host.isNotBlank()) { "HTTPS origin is missing a host." }
    val renderedHost = if (host.contains(':')) "[$host]" else host
    val port = if (url.port == -1 || url.port == 443) "" else ":${url.port}"
    return "https://$renderedHost$port"
  }

  private fun originHost(origin: String): String = normalizeHost(URL(origin).host)

  private fun normalizeSourceDeviceId(value: String?): String? =
    value?.trim()?.takeIf { it.isNotBlank() }?.take(128)

  private data class StoredTlsPinRecord(
    val pinId: String,
    val origin: String,
    val host: String,
    val fingerprintSha256: String,
    val status: String,
    val createdAtEpochMs: Long,
    val expiresAtEpochMs: Long,
    val sourceDeviceId: String?,
    val revokedAtEpochMs: Long?,
  ) {
    fun isUsable(nowEpochMs: Long): Boolean =
      (status == STATUS_ACTIVE || status == STATUS_NEXT) && revokedAtEpochMs == null && expiresAtEpochMs > nowEpochMs

    fun toJson(): JSONObject = JSONObject()
      .put("schema_version", RECORD_SCHEMA)
      .put("pin_id", pinId)
      .put("origin", origin)
      .put("host", host)
      .put("fingerprint_sha256", fingerprintSha256)
      .put("status", status)
      .put("created_at", formatTimestamp(createdAtEpochMs))
      .put("expires_at", formatTimestamp(expiresAtEpochMs))
      .also { value ->
        sourceDeviceId?.let { value.put("source_device_id", it) }
        revokedAtEpochMs?.let { value.put("revoked_at", formatTimestamp(it)) }
      }

    companion object {
      fun fromJson(value: JSONObject): StoredTlsPinRecord {
        require(value.getString("schema_version") == RECORD_SCHEMA) { "Unsupported TLS pin record schema." }
        val origin = normalizeHttpsOrigin(value.getString("origin"))
        val host = normalizeHost(value.getString("host"))
        require(host == originHost(origin)) { "TLS pin record host does not match its origin." }
        val fingerprint = requireFingerprint(value.getString("fingerprint_sha256"))
        val status = value.getString("status")
        require(status == STATUS_ACTIVE || status == STATUS_NEXT || status == STATUS_REVOKED) {
          "Unsupported TLS pin record status."
        }
        val createdAt = parseTimestamp(value.getString("created_at"))
        val expiresAt = parseTimestamp(value.getString("expires_at"))
        require(expiresAt > createdAt) { "TLS pin record expiry must be after creation." }
        val maximumLifetime = if (status == STATUS_NEXT) DEFAULT_NEXT_TTL_MS else MAX_RECORD_LIFETIME_MS
        require(expiresAt - createdAt <= maximumLifetime) { "TLS pin record lifetime is too long." }
        require(createdAt <= System.currentTimeMillis() + TIMESTAMP_CLOCK_SKEW_MS) {
          "TLS pin record creation time is in the future."
        }
        val revokedAt = value.optString("revoked_at").takeIf { it.isNotBlank() }?.let(::parseTimestamp)
        require((status == STATUS_REVOKED) == (revokedAt != null)) {
          "TLS pin revocation status and timestamp must agree."
        }
        require(revokedAt == null || (revokedAt >= createdAt && revokedAt <= System.currentTimeMillis() + TIMESTAMP_CLOCK_SKEW_MS)) {
          "TLS pin revocation time is invalid."
        }
        return StoredTlsPinRecord(
          pinId = value.getString("pin_id").trim().also { require(it.isNotBlank()) },
          origin = origin,
          host = host,
          fingerprintSha256 = fingerprint,
          status = status,
          createdAtEpochMs = createdAt,
          expiresAtEpochMs = expiresAt,
          sourceDeviceId = normalizeSourceDeviceId(value.optString("source_device_id")),
          revokedAtEpochMs = revokedAt,
        )
      }
    }
  }

  private class CorruptTlsPinStoreException(cause: Throwable? = null) :
    IllegalStateException(CORRUPT_STORE_MESSAGE, cause)

  private fun formatTimestamp(epochMs: Long): String = timestampFormat().format(Date(epochMs))

  private fun parseTimestamp(value: String): Long =
    requireNotNull(timestampFormat().parse(value)) { "Invalid TLS pin timestamp." }.time

  private fun timestampFormat(): SimpleDateFormat =
    SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'", Locale.US).apply {
      isLenient = false
      timeZone = TimeZone.getTimeZone("UTC")
    }
}

private class LengrvisOkHttpClientFactory(
  private val context: Context,
) : OkHttpClientFactory {
  override fun createNewNetworkModuleClient(): OkHttpClient {
    val trustManager = LengrvisPinnedTrustManager(context)
    val sslContext = SSLContext.getInstance("TLS")
    sslContext.init(null, arrayOf<TrustManager>(trustManager), null)
    return OkHttpClientProvider
      .createClientBuilder(context)
      .sslSocketFactory(sslContext.socketFactory, trustManager)
      .hostnameVerifier(LengrvisPinnedHostnameVerifier(context))
      .addInterceptor { chain ->
        val request = chain.request()
        if (!request.url.isHttps) {
          chain.proceed(request)
        } else {
          val origin = requestOrigin(request.url)
          LengrvisLanTrust.assertRequestTrustStateHealthy(context, origin)
          LengrvisTlsOriginScope.withOrigin(origin) {
            chain.proceed(request)
          }
        }
      }
      .addNetworkInterceptor { chain ->
        verifyPinnedOriginBeforeRequest(context, trustManager, chain)
        chain.proceed(chain.request())
      }
      .build()
  }
}

private object LengrvisTlsOriginScope {
  private val currentOrigin = ThreadLocal<String?>()

  fun get(): String? = currentOrigin.get()

  fun <T> withOrigin(origin: String, action: () -> T): T {
    val previous = currentOrigin.get()
    currentOrigin.set(origin)
    return try {
      action()
    } finally {
      if (previous == null) currentOrigin.remove() else currentOrigin.set(previous)
    }
  }
}

private fun verifyPinnedOriginBeforeRequest(
  context: Context,
  trustManager: LengrvisPinnedTrustManager,
  chain: Interceptor.Chain,
) {
  val requestUrl = chain.request().url
  if (!requestUrl.isHttps) return
  val origin = requestOrigin(requestUrl)
  LengrvisLanTrust.assertRequestTrustStateHealthy(context, origin)
  val certificateChain = chain.connection()?.handshake()?.peerCertificates
    ?.filterIsInstance<X509Certificate>()
    ?.toTypedArray()
    ?: emptyArray()
  val leaf = certificateChain.firstOrNull() ?: return
  if (trustManager.isSystemTrusted(certificateChain)) return
  val fingerprint = sha256(leaf)
  if (!LengrvisLanTrust.originHasFingerprint(context, origin, fingerprint)) {
    throw SSLPeerUnverifiedException("LAN TLS certificate pin is not authorized for origin $origin.")
  }
}

private fun requestOrigin(url: okhttp3.HttpUrl): String {
  val host = url.host.lowercase()
  val renderedHost = if (host.contains(':')) "[$host]" else host
  val port = if (url.port == 443) "" else ":${url.port}"
  return "https://$renderedHost$port"
}

private class LengrvisPinnedTrustManager(
  private val context: Context,
) : X509TrustManager {
  private val systemTrustManager: X509TrustManager = systemTrustManager()

  override fun checkClientTrusted(chain: Array<X509Certificate>, authType: String) {
    systemTrustManager.checkClientTrusted(chain, authType)
  }

  override fun checkServerTrusted(chain: Array<X509Certificate>, authType: String) {
    val expectedOrigin = LengrvisTlsOriginScope.get()
    if (expectedOrigin != null) {
      try {
        LengrvisLanTrust.assertRequestTrustStateHealthy(context, expectedOrigin)
      } catch (error: SSLPeerUnverifiedException) {
        throw CertificateException(error.message, error)
      }
    }
    try {
      systemTrustManager.checkServerTrusted(chain, authType)
      return
    } catch (systemError: CertificateException) {
      val leaf = chain.firstOrNull() ?: throw systemError
      val fingerprint = sha256(leaf)
      if (expectedOrigin == null || !LengrvisLanTrust.originHasFingerprint(context, expectedOrigin, fingerprint)) {
        throw systemError
      }
    }
  }

  override fun getAcceptedIssuers(): Array<X509Certificate> = systemTrustManager.acceptedIssuers

  fun isSystemTrusted(chain: Array<X509Certificate>): Boolean {
    val leaf = chain.firstOrNull() ?: return false
    return try {
      systemTrustManager.checkServerTrusted(chain, leaf.publicKey.algorithm)
      true
    } catch (_: CertificateException) {
      false
    }
  }

  private fun systemTrustManager(): X509TrustManager {
    val androidCaStore = KeyStore.getInstance("AndroidCAStore")
    androidCaStore.load(null)
    val systemOnlyStore = KeyStore.getInstance(KeyStore.getDefaultType())
    systemOnlyStore.load(null)
    var systemCertificateCount = 0
    val aliases = androidCaStore.aliases()
    while (aliases.hasMoreElements()) {
      val alias = aliases.nextElement()
      if (!alias.startsWith("system:")) continue
      val certificate = androidCaStore.getCertificate(alias) ?: continue
      systemOnlyStore.setCertificateEntry(alias, certificate)
      systemCertificateCount += 1
    }
    check(systemCertificateCount > 0) { "Android system CA store did not expose any system trust anchors." }
    val factory = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm())
    factory.init(systemOnlyStore)
    return factory.trustManagers.filterIsInstance<X509TrustManager>().first()
  }
}

private class LengrvisPinnedHostnameVerifier(
  private val context: Context,
) : HostnameVerifier {
  private val systemVerifier = HttpsURLConnection.getDefaultHostnameVerifier()

  override fun verify(hostname: String?, session: SSLSession?): Boolean {
    if (hostname.isNullOrBlank() || session == null) return false
    LengrvisTlsOriginScope.get()?.let { origin ->
      try {
        LengrvisLanTrust.assertRequestTrustStateHealthy(context, origin)
      } catch (_: SSLPeerUnverifiedException) {
        return false
      }
    }
    if (!systemVerifier.verify(hostname, session)) return false
    val fingerprint = try {
      val leaf = session.peerCertificates.firstOrNull() as? X509Certificate ?: return false
      sha256(leaf)
    } catch (_: SSLPeerUnverifiedException) {
      return false
    }
    return if (LengrvisLanTrust.hostHasAnyFingerprintForHost(context, hostname)) {
      LengrvisLanTrust.hostHasFingerprint(context, hostname, fingerprint)
    } else {
      !LengrvisLanTrust.hasAnyFingerprint(context, fingerprint)
    }
  }
}

private fun sha256(certificate: X509Certificate): String {
  val digest = MessageDigest.getInstance("SHA-256").digest(certificate.encoded)
  return digest.joinToString(separator = "") { byte -> "%02X".format(byte.toInt() and 0xff) }
}
