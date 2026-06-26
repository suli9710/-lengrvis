package com.lengrvis.approval

import android.content.Context
import com.facebook.react.modules.network.OkHttpClientFactory
import com.facebook.react.modules.network.OkHttpClientProvider
import java.net.URL
import java.security.KeyStore
import java.security.MessageDigest
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import javax.net.ssl.HostnameVerifier
import javax.net.ssl.HttpsURLConnection
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLPeerUnverifiedException
import javax.net.ssl.SSLSession
import javax.net.ssl.TrustManager
import javax.net.ssl.TrustManagerFactory
import javax.net.ssl.X509TrustManager
import okhttp3.OkHttpClient
import org.json.JSONArray
import org.json.JSONObject

object LengrvisLanTrust {
  private const val PREFS_NAME = "lengrvis_lan_tls_trust"
  private const val PINS_KEY = "pinned_certificate_sha256_by_host"
  private val lock = Any()

  fun install(context: Context) {
    OkHttpClientProvider.setOkHttpClientFactory(LengrvisOkHttpClientFactory(context.applicationContext))
  }

  fun trustServerCertificate(context: Context, baseUrl: String, fingerprintSha256: String) {
    val url = URL(baseUrl)
    require(url.protocol.equals("https", ignoreCase = true)) {
      "LAN certificate pins are only accepted for HTTPS origins."
    }
    val host = normalizeHost(url.host)
    require(host.isNotBlank()) { "HTTPS origin is missing a host." }
    val fingerprint = normalizeFingerprint(fingerprintSha256)
    require(fingerprint.length == 64) { "Certificate SHA-256 fingerprint must contain 64 hex characters." }

    synchronized(lock) {
      val pins = readPinsLocked(context)
      val values = pins.optJSONArray(host) ?: JSONArray()
      if (!contains(values, fingerprint)) values.put(fingerprint)
      pins.put(host, values)
      writePinsLocked(context, pins)
    }
  }

  fun clearTrustedServers(context: Context) {
    synchronized(lock) {
      prefs(context).edit().remove(PINS_KEY).apply()
    }
  }

  fun hasAnyFingerprint(context: Context, fingerprintSha256: String): Boolean {
    val fingerprint = normalizeFingerprint(fingerprintSha256)
    if (fingerprint.isBlank()) return false
    synchronized(lock) {
      val pins = readPinsLocked(context)
      val keys = pins.keys()
      while (keys.hasNext()) {
        if (contains(pins.optJSONArray(keys.next()), fingerprint)) return true
      }
    }
    return false
  }

  fun hostHasFingerprint(context: Context, host: String?, fingerprintSha256: String): Boolean {
    val normalizedHost = normalizeHost(host)
    val fingerprint = normalizeFingerprint(fingerprintSha256)
    if (normalizedHost.isBlank() || fingerprint.isBlank()) return false
    synchronized(lock) {
      return contains(readPinsLocked(context).optJSONArray(normalizedHost), fingerprint)
    }
  }

  private fun prefs(context: Context) = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

  private fun readPinsLocked(context: Context): JSONObject {
    val raw = prefs(context).getString(PINS_KEY, "") ?: ""
    if (raw.isBlank()) return JSONObject()
    return try {
      JSONObject(raw)
    } catch (_: Exception) {
      JSONObject()
    }
  }

  private fun writePinsLocked(context: Context, pins: JSONObject) {
    prefs(context).edit().putString(PINS_KEY, pins.toString()).apply()
  }

  private fun contains(values: JSONArray?, fingerprint: String): Boolean {
    if (values == null) return false
    for (index in 0 until values.length()) {
      if (normalizeFingerprint(values.optString(index)) == fingerprint) return true
    }
    return false
  }

  private fun normalizeFingerprint(value: String): String =
    value.trim().replace(":", "").replace(Regex("\\s+"), "").uppercase()

  private fun normalizeHost(value: String?): String =
    (value ?: "").trim().trim('[', ']').lowercase()
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
      .build()
  }
}

private class LengrvisPinnedTrustManager(
  private val context: Context,
) : X509TrustManager {
  private val systemTrustManager: X509TrustManager = systemTrustManager()

  override fun checkClientTrusted(chain: Array<X509Certificate>, authType: String) {
    systemTrustManager.checkClientTrusted(chain, authType)
  }

  override fun checkServerTrusted(chain: Array<X509Certificate>, authType: String) {
    try {
      systemTrustManager.checkServerTrusted(chain, authType)
      return
    } catch (systemError: CertificateException) {
      val leaf = chain.firstOrNull() ?: throw systemError
      if (!LengrvisLanTrust.hasAnyFingerprint(context, sha256(leaf))) {
        throw systemError
      }
    }
  }

  override fun getAcceptedIssuers(): Array<X509Certificate> = systemTrustManager.acceptedIssuers

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
    if (!systemVerifier.verify(hostname, session)) return false
    val fingerprint = try {
      val leaf = session.peerCertificates.firstOrNull() as? X509Certificate ?: return false
      sha256(leaf)
    } catch (_: SSLPeerUnverifiedException) {
      return false
    }
    return if (LengrvisLanTrust.hasAnyFingerprint(context, fingerprint)) {
      LengrvisLanTrust.hostHasFingerprint(context, hostname, fingerprint)
    } else {
      true
    }
  }
}

private fun sha256(certificate: X509Certificate): String {
  val digest = MessageDigest.getInstance("SHA-256").digest(certificate.encoded)
  return digest.joinToString(separator = "") { byte -> "%02X".format(byte.toInt() and 0xff) }
}
