package com.lengrvis.approval

import com.facebook.react.bridge.Promise
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.bridge.ReactContextBaseJavaModule
import com.facebook.react.bridge.ReactMethod

class LengrvisLanTrustModule(
  private val reactContext: ReactApplicationContext,
) : ReactContextBaseJavaModule(reactContext) {
  override fun getName(): String = "LengrvisLanTrust"

  @ReactMethod
  fun trustServerCertificate(baseUrl: String, fingerprintSha256: String, promise: Promise) {
    try {
      LengrvisLanTrust.trustServerCertificate(reactContext, baseUrl, fingerprintSha256)
      promise.resolve(true)
    } catch (error: Exception) {
      promise.reject("E_LENGRVIS_TLS_TRUST", error.message, error)
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
