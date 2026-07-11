import { MOBILE_LEGAL_VERSIONS } from "./store/consent";

export type MobileLegalDocument = "eula" | "privacy";

/**
 * Version-bound, human-readable copies of the complete legal candidates.
 *
 * Keep these URLs pinned to the app release tag. A moving branch would let the
 * document change after a consent record had already captured its version.
 */
export const MOBILE_LEGAL_DOCUMENT_URLS: Record<MobileLegalDocument, string> = {
  eula: "https://github.com/suli9710/-lengrvis/blob/v0.1.2/docs/legal/eula.md",
  privacy: "https://github.com/suli9710/-lengrvis/blob/v0.1.2/docs/legal/privacy-policy.md",
};

export function mobileLegalDocumentLabel(document: MobileLegalDocument): string {
  return document === "eula"
    ? `完整最终用户许可协议候选草案（${MOBILE_LEGAL_VERSIONS.eula}）`
    : `完整隐私政策候选草案（${MOBILE_LEGAL_VERSIONS.privacy}）`;
}
