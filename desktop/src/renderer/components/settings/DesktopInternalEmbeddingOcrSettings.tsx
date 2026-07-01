import type { Dispatch, SetStateAction } from "react";

import type { AppSettings } from "../../../shared/types";

type SetDraft = Dispatch<SetStateAction<AppSettings>>;

export function DesktopInternalEmbeddingOcrSettings({ draft, setDraft }: { draft: AppSettings; setDraft: SetDraft }) {
  return (
    <>
      <label className="field">
        <span>文本向量后端</span>
        <input
          value={draft.onnxEmbeddingBackend}
          onChange={(event) => setDraft((current) => ({ ...current, onnxEmbeddingBackend: event.target.value }))}
        />
      </label>
      <label className="field">
        <span>文本向量模型路径</span>
        <input
          value={draft.onnxEmbeddingModelPath}
          onChange={(event) => setDraft((current) => ({ ...current, onnxEmbeddingModelPath: event.target.value }))}
        />
      </label>
      <label className="field">
        <span>文本向量 EP</span>
        <input
          value={draft.onnxEmbeddingExecutionProvider}
          onChange={(event) =>
            setDraft((current) => ({ ...current, onnxEmbeddingExecutionProvider: event.target.value }))
          }
        />
      </label>
      <label className="field">
        <span>图像向量后端</span>
        <input
          value={draft.imageEmbeddingBackend}
          onChange={(event) => setDraft((current) => ({ ...current, imageEmbeddingBackend: event.target.value }))}
        />
      </label>
      <label className="field">
        <span>图像向量模型路径</span>
        <input
          value={draft.onnxImageEmbeddingModelPath}
          onChange={(event) => setDraft((current) => ({ ...current, onnxImageEmbeddingModelPath: event.target.value }))}
        />
      </label>
      <label className="field">
        <span>OCR 后端</span>
        <input
          value={draft.ocrBackend}
          onChange={(event) => setDraft((current) => ({ ...current, ocrBackend: event.target.value }))}
        />
      </label>
      <label className="field">
        <span>OCR EP</span>
        <input
          value={draft.ocrExecutionProvider}
          onChange={(event) => setDraft((current) => ({ ...current, ocrExecutionProvider: event.target.value }))}
        />
      </label>
    </>
  );
}
