import { QrCode } from "lucide-react";
import QRCode from "qrcode";
import { useEffect, useState } from "react";

import type { MobilePairingQrContent } from "../../../shared/mobilePairingPayload";

export function PairingVisualCode({ code, qrContent }: { code?: string; qrContent?: MobilePairingQrContent | null }) {
  const normalized = code ?? "------";
  const [qrImage, setQrImage] = useState<string | null>(null);
  const [qrError, setQrError] = useState("");
  const bits = Array.from({ length: 36 }, (_, index) => {
    const charCode = normalized.charCodeAt(index % normalized.length) || 45;
    return (charCode + index * 7) % 3 !== 0;
  });

  useEffect(() => {
    let cancelled = false;
    setQrError("");
    if (!qrContent?.value) {
      setQrImage(null);
      return () => {
        cancelled = true;
      };
    }

    void QRCode.toDataURL(qrContent.value, {
      errorCorrectionLevel: "M",
      margin: 2,
      width: 148,
      color: {
        dark: "#0f172a",
        light: "#ffffff"
      }
    }).then((value) => {
      if (!cancelled) setQrImage(value);
    }).catch(() => {
      if (!cancelled) {
        setQrImage(null);
        setQrError("二维码暂时无法生成，可复制配对信息。");
      }
    });

    return () => {
      cancelled = true;
    };
  }, [qrContent?.value]);

  return (
    <div className="mobile-pairing__visual" aria-label={code ? `配对码 ${code}` : "尚未生成配对码"}>
      <div className="mobile-pairing__code">{normalized}</div>
      {qrContent ? (
        <div
          className="mobile-pairing__qr-ready"
          data-mobile-pairing-qr="ready"
          data-qr-encoding={qrContent.encoding}
          data-qr-length={qrContent.length}
          data-qr-mime-type={qrContent.mime_type}
        >
          <div className="mobile-pairing__qr-head">
            <QrCode size={16} aria-hidden="true" />
            <span>打开手机 App 扫码</span>
          </div>
          {qrImage ? (
            <img className="mobile-pairing__qr-image" src={qrImage} alt="打开手机 App 扫描的配对二维码" />
          ) : (
            <div className="mobile-pairing__matrix" aria-hidden="true">
              {bits.map((active, index) => (
                <span key={index} className={active ? "mobile-pairing__cell mobile-pairing__cell--active" : "mobile-pairing__cell"} />
              ))}
            </div>
          )}
          {qrError ? <small className="mobile-pairing__qr-error">{qrError}</small> : null}
        </div>
      ) : (
        <div className="mobile-pairing__matrix" aria-hidden="true">
          {bits.map((active, index) => (
            <span key={index} className={active ? "mobile-pairing__cell mobile-pairing__cell--active" : "mobile-pairing__cell"} />
          ))}
        </div>
      )}
    </div>
  );
}
