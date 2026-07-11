package com.lengrvis.approval

import com.facebook.react.bridge.Promise
import com.facebook.react.bridge.Arguments
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.bridge.ReactContextBaseJavaModule
import com.facebook.react.bridge.ReactMethod
import com.facebook.react.bridge.WritableMap
import org.json.JSONArray
import org.json.JSONObject

class LengrvisLanTrustModule(
  private val reactContext: ReactApplicationContext,
) : ReactContextBaseJavaModule(reactContext) {
  override fun getName(): String = "LengrvisLanTrust"

  @ReactMethod
  fun stageServerCertificate(
    baseUrl: String,
    fingerprintSha256: String,
    activeExpiresAtEpochMs: Double,
    nextExpiresAtEpochMs: Double,
    sourceDeviceId: String?,
    promise: Promise,
  ) {
    try {
      val record = LengrvisLanTrust.stageServerCertificate(
        context = reactContext,
        baseUrl = baseUrl,
        fingerprintSha256 = fingerprintSha256,
        activeExpiresAtEpochMs = activeExpiresAtEpochMs.toLong(),
        nextExpiresAtEpochMs = nextExpiresAtEpochMs.toLong(),
        sourceDeviceId = sourceDeviceId,
      )
      promise.resolve(record.toWritableMap())
    } catch (error: Exception) {
      promise.reject("E_LENGRVIS_TLS_TRUST_STAGE", error.message, error)
    }
  }

  @ReactMethod
  fun assertServerCertificateTrusted(baseUrl: String, fingerprintSha256: String, promise: Promise) {
    try {
      promise.resolve(
        LengrvisLanTrust.assertServerCertificateTrusted(reactContext, baseUrl, fingerprintSha256).toWritableMap(),
      )
    } catch (error: Exception) {
      promise.reject("E_LENGRVIS_TLS_TRUST_ASSERT", error.message, error)
    }
  }

  @ReactMethod
  fun activateServerCertificate(
    baseUrl: String,
    fingerprintSha256: String,
    activeExpiresAtEpochMs: Double,
    sourceDeviceId: String?,
    promise: Promise,
  ) {
    try {
      val record = LengrvisLanTrust.activateServerCertificate(
        context = reactContext,
        baseUrl = baseUrl,
        fingerprintSha256 = fingerprintSha256,
        activeExpiresAtEpochMs = activeExpiresAtEpochMs.toLong(),
        sourceDeviceId = sourceDeviceId,
      )
      promise.resolve(record.toWritableMap())
    } catch (error: Exception) {
      promise.reject("E_LENGRVIS_TLS_TRUST_ACTIVATE", error.message, error)
    }
  }

  @ReactMethod
  fun revokeServerCertificate(baseUrl: String, fingerprintSha256: String, promise: Promise) {
    try {
      promise.resolve(LengrvisLanTrust.revokeServerCertificate(reactContext, baseUrl, fingerprintSha256))
    } catch (error: Exception) {
      promise.reject("E_LENGRVIS_TLS_TRUST_REVOKE", error.message, error)
    }
  }

  @ReactMethod
  fun listServerCertificatePins(baseUrl: String, includeRevoked: Boolean, promise: Promise) {
    try {
      promise.resolve(
        LengrvisLanTrust.listServerCertificatePins(reactContext, baseUrl, includeRevoked).toWritableArray(),
      )
    } catch (error: Exception) {
      promise.reject("E_LENGRVIS_TLS_TRUST_LIST", error.message, error)
    }
  }

  @ReactMethod
  fun clearTrustedServers(promise: Promise) {
    try {
      LengrvisLanTrust.clearTrustedServers(reactContext)
      promise.resolve(true)
    } catch (error: Exception) {
      promise.reject("E_LENGRVIS_TLS_TRUST_CLEAR", error.message, error)
    }
  }
}

private fun JSONObject.toWritableMap(): WritableMap = Arguments.createMap().also { target ->
  keys().forEach { key ->
    when (val value = get(key)) {
      is String -> target.putString(key, value)
      is Boolean -> target.putBoolean(key, value)
      is Int -> target.putInt(key, value)
      is Double -> target.putDouble(key, value)
      is Long -> target.putDouble(key, value.toDouble())
      JSONObject.NULL -> target.putNull(key)
      else -> throw IllegalArgumentException("Unsupported TLS pin field $key.")
    }
  }
}

private fun JSONArray.toWritableArray() = Arguments.createArray().also { target ->
  for (index in 0 until length()) {
    target.pushMap(getJSONObject(index).toWritableMap())
  }
}
