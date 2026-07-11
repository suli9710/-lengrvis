package com.lengrvis.approval

import android.content.Context
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.facebook.react.modules.network.OkHttpClientProvider
import java.io.IOException
import java.net.URL
import java.security.cert.CertificateException
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import javax.net.ssl.SSLHandshakeException
import javax.net.ssl.SSLPeerUnverifiedException
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONArray
import org.json.JSONObject
import org.junit.After
import org.junit.Assert
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class LengrvisLanTrustInstrumentedTest {
  private lateinit var context: Context
  private lateinit var baseUrl: String
  private lateinit var fingerprintSha256: String
  private var pairCode: String = ""
  private var pairClaimSecret: String = ""

  @Before
  fun setUp() {
    context = InstrumentationRegistry.getInstrumentation().targetContext.applicationContext
    val args = InstrumentationRegistry.getArguments()
    baseUrl = requireArgument(args.getString("lengrvisBaseUrl"), "lengrvisBaseUrl").trim().trimEnd('/')
    fingerprintSha256 = requireArgument(args.getString("lengrvisFingerprintSha256"), "lengrvisFingerprintSha256")
    pairCode = args.getString("lengrvisPairCode")?.trim().orEmpty()
    pairClaimSecret = args.getString("lengrvisPairClaimSecret")?.trim().orEmpty()
    LengrvisLanTrust.install(context)
    LengrvisLanTrust.clearTrustedServers(context)
  }

  @After
  fun tearDown() {
    LengrvisLanTrust.clearTrustedServers(context)
  }

  @Test
  fun pinnedHostPolicyTracksFingerprintsByHost() {
    val host = URL(baseUrl).host

    Assert.assertFalse(LengrvisLanTrust.hostHasAnyFingerprintForHost(context, host))
    Assert.assertFalse(LengrvisLanTrust.hostHasFingerprint(context, host, fingerprintSha256))

    LengrvisLanTrust.trustServerCertificate(context, baseUrl, fingerprintSha256)

    Assert.assertTrue(LengrvisLanTrust.hostHasAnyFingerprintForHost(context, host))
    Assert.assertTrue(LengrvisLanTrust.hostHasFingerprint(context, host, fingerprintSha256))
    Assert.assertFalse(LengrvisLanTrust.hostHasFingerprint(context, host, wrongFingerprint(fingerprintSha256)))
    Assert.assertFalse(LengrvisLanTrust.hostHasAnyFingerprintForHost(context, "example.invalid"))
  }

  @Test
  fun pinLifecycleSupportsOverlapPromotionExpiryAndTargetedRevocation() {
    val origin = baseUrl
    val activeFingerprint = fingerprintSha256
    val nextFingerprint = wrongFingerprint(activeFingerprint)
    val replacementNextFingerprint = anotherWrongFingerprint(activeFingerprint)
    val now = System.currentTimeMillis()

    val active = LengrvisLanTrust.stageServerCertificate(
      context,
      origin,
      activeFingerprint,
      now + 60_000,
      now + 30_000,
      "desktop-source-1",
    )
    Assert.assertEquals("tls-pin-record-v1", active.getString("schema_version"))
    Assert.assertEquals("active", active.getString("status"))
    Assert.assertEquals(normalizedFingerprint(activeFingerprint), active.getString("fingerprint_sha256"))
    Assert.assertEquals("desktop-source-1", active.getString("source_device_id"))

    val next = LengrvisLanTrust.stageServerCertificate(
      context,
      origin,
      nextFingerprint,
      now + 60_000,
      now + 30_000,
      null,
    )
    Assert.assertEquals("next", next.getString("status"))
    Assert.assertTrue(LengrvisLanTrust.hostHasFingerprint(context, URL(origin).host, activeFingerprint))
    Assert.assertTrue(LengrvisLanTrust.hostHasFingerprint(context, URL(origin).host, nextFingerprint))

    val replacementNext = LengrvisLanTrust.stageServerCertificate(
      context,
      origin,
      replacementNextFingerprint,
      now + 60_000,
      now + 30_000,
      null,
    )
    Assert.assertEquals("next", replacementNext.getString("status"))
    Assert.assertFalse(LengrvisLanTrust.hostHasFingerprint(context, URL(origin).host, nextFingerprint))
    Assert.assertTrue(LengrvisLanTrust.hostHasFingerprint(context, URL(origin).host, replacementNextFingerprint))

    val promoted = LengrvisLanTrust.activateServerCertificate(
      context,
      origin,
      replacementNextFingerprint,
      now + 120_000,
      "desktop-source-2",
    )
    Assert.assertEquals("active", promoted.getString("status"))
    Assert.assertEquals("desktop-source-2", promoted.getString("source_device_id"))
    Assert.assertFalse(LengrvisLanTrust.hostHasFingerprint(context, URL(origin).host, activeFingerprint))
    Assert.assertTrue(LengrvisLanTrust.hostHasFingerprint(context, URL(origin).host, replacementNextFingerprint))
    Assert.assertFalse(
      LengrvisLanTrust.originHasFingerprint(context, differentPortOrigin(origin), replacementNextFingerprint),
    )

    val history = LengrvisLanTrust.listServerCertificatePins(context, origin, true)
    Assert.assertTrue((0 until history.length()).any { history.getJSONObject(it).getString("status") == "revoked" })
    Assert.assertTrue(LengrvisLanTrust.revokeServerCertificate(context, origin, replacementNextFingerprint))
    Assert.assertFalse(LengrvisLanTrust.hostHasAnyFingerprintForHost(context, URL(origin).host))
  }

  @Test
  fun expiredPinFailsClosedWithoutAutomaticRenewal() {
    val now = System.currentTimeMillis()
    LengrvisLanTrust.stageServerCertificate(
      context,
      baseUrl,
      fingerprintSha256,
      now + 80,
      now + 80,
      null,
    )
    Thread.sleep(120)

    Assert.assertFalse(LengrvisLanTrust.hasAnyFingerprint(context, fingerprintSha256))
    Assert.assertThrows(SSLPeerUnverifiedException::class.java) {
      LengrvisLanTrust.assertServerCertificateTrusted(context, baseUrl, fingerprintSha256)
    }
  }

  @Test
  fun malformedMultiPinStoreBlocksRequestsUntilExplicitRepair() {
    val now = System.currentTimeMillis()
    LengrvisLanTrust.stageServerCertificate(
      context,
      baseUrl,
      fingerprintSha256,
      now + 60_000,
      now + 30_000,
      null,
    )
    LengrvisLanTrust.stageServerCertificate(
      context,
      baseUrl,
      wrongFingerprint(fingerprintSha256),
      now + 60_000,
      now + 30_000,
      null,
    )
    val client = OkHttpClientProvider.createClient(context)
    getJson(client, "$baseUrl/api/health")
    val records = LengrvisLanTrust.listServerCertificatePins(context, baseUrl, true)
    val extra = JSONObject(records.getJSONObject(records.length() - 1).toString())
      .put("pin_id", "corrupt-extra-pin")
      .put("fingerprint_sha256", normalizedFingerprint(anotherWrongFingerprint(fingerprintSha256)))
    val corrupted = JSONArray(records.toString()).put(extra)
    Assert.assertTrue(
      context.getSharedPreferences("lengrvis_lan_tls_trust", Context.MODE_PRIVATE)
        .edit()
        .putString("tls_pin_records_v1", corrupted.toString())
        .commit(),
    )

    Assert.assertThrows(IllegalStateException::class.java) {
      LengrvisLanTrust.hostHasAnyFingerprintForHost(context, URL(baseUrl).host)
    }
    assertTlsHandshakeFails(client, "malformed pin storage must block a pooled HTTPS request")
    val preferences = context.getSharedPreferences("lengrvis_lan_tls_trust", Context.MODE_PRIVATE)
    Assert.assertEquals("corrupt-v1", preferences.getString("tls_pin_store_corrupt_v1", null))

    Assert.assertTrue(preferences.edit().remove("tls_pin_records_v1").commit())
    assertTlsHandshakeFails(client, "persisted corrupt-state sentinel must block fallback after malformed data is removed")

    LengrvisLanTrust.clearTrustedServers(context)
    LengrvisLanTrust.trustServerCertificate(context, baseUrl, fingerprintSha256)
    getJson(client, "$baseUrl/api/health")
  }

  @Test
  fun legacyPinStoreBlocksRequestsUntilExplicitRepair() {
    val legacy = JSONObject()
      .put(URL(baseUrl).host, JSONArray().put(normalizedFingerprint(fingerprintSha256)))
    val preferences = context.getSharedPreferences("lengrvis_lan_tls_trust", Context.MODE_PRIVATE)
    Assert.assertTrue(
      preferences.edit()
        .putString("pinned_certificate_sha256_by_host", legacy.toString())
        .commit(),
    )

    val client = OkHttpClientProvider.createClient(context)
    assertTlsHandshakeFails(client, "legacy pin storage must block HTTPS instead of becoming an empty pin set")
    Assert.assertEquals("corrupt-v1", preferences.getString("tls_pin_store_corrupt_v1", null))

    LengrvisLanTrust.clearTrustedServers(context)
    LengrvisLanTrust.trustServerCertificate(context, baseUrl, fingerprintSha256)
    getJson(client, "$baseUrl/api/health")
  }

  @Test
  fun pinnedLanHttpsPairingAndApprovalWss() {
    assertTlsHandshakeFails("self-signed LAN HTTPS must fail before pinning")

    LengrvisLanTrust.trustServerCertificate(context, baseUrl, wrongFingerprint(fingerprintSha256))
    assertTlsHandshakeFails("self-signed LAN HTTPS must fail with the wrong pin")

    LengrvisLanTrust.clearTrustedServers(context)
    LengrvisLanTrust.trustServerCertificate(context, baseUrl, fingerprintSha256)
    val client = OkHttpClientProvider.createClient(context)

    getJson(client, "$baseUrl/api/health")
    val pairing = if (pairCode.isBlank()) {
      postJson(client, "$baseUrl/api/pair/request", "{}")
    } else {
      null
    }
    val code = pairing?.getString("code") ?: pairCode
    val claimSecret = pairing?.getString("claim_secret")
      ?: requireArgument(pairClaimSecret, "lengrvisPairClaimSecret")
    val session = postJson(
      client,
      "$baseUrl/api/pair/confirm",
      JSONObject()
        .put("code", code)
        .put("claim_secret", claimSecret)
        .put("device_name", "Android TLS instrumentation")
        .toString(),
    )

    val token = session.getString("token")
    val connected = awaitWebSocketConnected(
      client = client,
      url = baseUrl.replaceFirst("https://", "wss://") + "/ws/mobile/approvals",
      token = token,
    )
    Assert.assertTrue("approval WSS should send a connected event: $connected", connected.contains("\"type\":\"connected\""))

    Assert.assertTrue(LengrvisLanTrust.revokeServerCertificate(context, baseUrl, fingerprintSha256))
    assertTlsHandshakeFails(client, "revoked LAN TLS pin must fail on an already pooled connection")
  }

  private fun assertTlsHandshakeFails(label: String) {
    assertTlsHandshakeFails(OkHttpClientProvider.createClient(context), label)
  }

  private fun assertTlsHandshakeFails(client: OkHttpClient, label: String) {
    try {
      client.newCall(Request.Builder().url("$baseUrl/api/health").build()).execute().use { response ->
        Assert.fail("$label; request unexpectedly completed with HTTP ${response.code}")
      }
    } catch (error: IOException) {
      Assert.assertTrue("$label; got non-TLS failure ${error::class.java.name}: ${error.message}", isTlsFailure(error))
    }
  }

  private fun getJson(client: OkHttpClient, url: String): JSONObject =
    jsonFromResponse(client.newCall(Request.Builder().url(url).build()).execute())

  private fun postJson(client: OkHttpClient, url: String, body: String): JSONObject {
    val mediaType = "application/json".toMediaType()
    val request = Request.Builder()
      .url(url)
      .post(body.toRequestBody(mediaType))
      .header("Accept", "application/json")
      .header("Content-Type", "application/json")
      .build()
    return jsonFromResponse(client.newCall(request).execute())
  }

  private fun jsonFromResponse(response: Response): JSONObject =
    response.use {
      val body = it.body?.string() ?: ""
      Assert.assertTrue("HTTP ${it.code}: $body", it.isSuccessful)
      JSONObject(body)
    }

  private fun awaitWebSocketConnected(client: OkHttpClient, url: String, token: String): String {
    val latch = CountDownLatch(1)
    val result = StringBuilder()
    var failure: Throwable? = null
    val request = Request.Builder()
      .url(url)
      .header("Sec-WebSocket-Protocol", "lengrvis.mobile.token.$token")
      .build()
    val socket = client.newWebSocket(
      request,
      object : WebSocketListener() {
        override fun onMessage(webSocket: WebSocket, text: String) {
          result.append(text)
          latch.countDown()
        }

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
          failure = t
          latch.countDown()
        }
      },
    )
    try {
      Assert.assertTrue("timed out waiting for WSS connected event", latch.await(20, TimeUnit.SECONDS))
      failure?.let { throw AssertionError("approval WSS failed: ${it::class.java.name}: ${it.message}", it) }
      return result.toString()
    } finally {
      socket.close(1000, "instrumentation complete")
    }
  }

  private fun isTlsFailure(error: Throwable): Boolean {
    var current: Throwable? = error
    while (current != null) {
      if (current is SSLHandshakeException || current is SSLPeerUnverifiedException || current is CertificateException) {
        return true
      }
      current = current.cause
    }
    return false
  }

  private fun requireArgument(value: String?, name: String): String {
    require(!value.isNullOrBlank()) { "Instrumentation argument $name is required." }
    return value
  }

  private fun wrongFingerprint(fingerprint: String): String {
    val normalized = normalizedFingerprint(fingerprint).uppercase()
    require(normalized.length == 64) { "Expected a 64 hex character SHA-256 fingerprint." }
    val replacement = if (normalized.first() == 'A') 'B' else 'A'
    return replacement + normalized.substring(1)
  }

  private fun anotherWrongFingerprint(fingerprint: String): String {
    val normalized = normalizedFingerprint(fingerprint).uppercase()
    val replacement = if (normalized[1] == 'C') 'D' else 'C'
    return "${normalized.first()}$replacement${normalized.substring(2)}"
  }

  private fun normalizedFingerprint(fingerprint: String): String =
    fingerprint.trim().replace(":", "").lowercase()

  private fun differentPortOrigin(origin: String): String {
    val url = URL(origin)
    val currentPort = if (url.port == -1) 443 else url.port
    return "https://${url.host}:${currentPort + 1}"
  }
}
