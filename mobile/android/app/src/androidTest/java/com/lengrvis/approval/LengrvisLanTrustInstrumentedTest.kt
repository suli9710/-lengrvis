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
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
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

  @Before
  fun setUp() {
    context = InstrumentationRegistry.getInstrumentation().targetContext.applicationContext
    val args = InstrumentationRegistry.getArguments()
    baseUrl = requireArgument(args.getString("lengrvisBaseUrl"), "lengrvisBaseUrl").trim().trimEnd('/')
    fingerprintSha256 = requireArgument(args.getString("lengrvisFingerprintSha256"), "lengrvisFingerprintSha256")
    pairCode = args.getString("lengrvisPairCode")?.trim().orEmpty()
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
  fun pinnedLanHttpsPairingAndApprovalWss() {
    assertTlsHandshakeFails("self-signed LAN HTTPS must fail before pinning")

    LengrvisLanTrust.trustServerCertificate(context, baseUrl, wrongFingerprint(fingerprintSha256))
    assertTlsHandshakeFails("self-signed LAN HTTPS must fail with the wrong pin")

    LengrvisLanTrust.clearTrustedServers(context)
    LengrvisLanTrust.trustServerCertificate(context, baseUrl, fingerprintSha256)
    val client = OkHttpClientProvider.createClient(context)

    getJson(client, "$baseUrl/api/health")
    val code = if (pairCode.isNotBlank()) {
      pairCode
    } else {
      postJson(client, "$baseUrl/api/pair/request", "{}").getString("code")
    }
    val session = postJson(
      client,
      "$baseUrl/api/pair/confirm",
      JSONObject()
        .put("code", code)
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
  }

  private fun assertTlsHandshakeFails(label: String) {
    val client = OkHttpClientProvider.createClient(context)
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
      if (current is SSLHandshakeException || current is CertificateException) return true
      current = current.cause
    }
    return false
  }

  private fun requireArgument(value: String?, name: String): String {
    require(!value.isNullOrBlank()) { "Instrumentation argument $name is required." }
    return value
  }

  private fun wrongFingerprint(fingerprint: String): String {
    val normalized = fingerprint.trim().replace(":", "").uppercase()
    require(normalized.length == 64) { "Expected a 64 hex character SHA-256 fingerprint." }
    val replacement = if (normalized.first() == 'A') 'B' else 'A'
    return replacement + normalized.substring(1)
  }
}
