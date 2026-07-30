import { Mic, Square } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import type { LengrvisApiClient } from "../lib/apiClient";

interface VoiceInputButtonProps {
  api: LengrvisApiClient;
  disabled?: boolean;
  onTranscript: (transcript: string) => void;
  onError?: (message: string) => void;
}

const TARGET_SAMPLE_RATE = 16_000;
const MAX_RECORDING_MS = 60_000;

type VoiceAvailability = "unknown" | "available" | "unavailable";

export function VoiceInputButton({ api, disabled = false, onTranscript, onError }: VoiceInputButtonProps) {
  const [availability, setAvailability] = useState<VoiceAvailability>("unknown");
  const [unavailableDetail, setUnavailableDetail] = useState("");
  const [isRecording, setIsRecording] = useState(false);
  const [isStarting, setIsStarting] = useState(false);
  const [isTranscribing, setIsTranscribing] = useState(false);
  const recorderRef = useRef<PcmRecorder | null>(null);
  const stopTimerRef = useRef<number | null>(null);
  const mountedRef = useRef(true);
  const startGenerationRef = useRef(0);

  useEffect(() => {
    let cancelled = false;
    void api.getVoiceHealth().then((response) => {
      if (cancelled) return;
      if (response.ok && response.data) {
        setAvailability(response.data.available ? "available" : "unavailable");
        setUnavailableDetail(response.data.detail || "");
      } else {
        setAvailability("unavailable");
        setUnavailableDetail("无法读取语音识别状态");
      }
    }).catch(() => {
      if (cancelled) return;
      setAvailability("unavailable");
      setUnavailableDetail("无法读取语音识别状态");
    });
    return () => {
      cancelled = true;
    };
  }, [api]);

  const stopRecording = useCallback(async () => {
    const recorder = recorderRef.current;
    recorderRef.current = null;
    if (stopTimerRef.current !== null) {
      window.clearTimeout(stopTimerRef.current);
      stopTimerRef.current = null;
    }
    setIsRecording(false);
    if (!recorder) return;

    try {
      const pcm = await recorder.stop();
      if (!pcm.byteLength) {
        onError?.("没有录到声音，请重试。");
        return;
      }

      if (mountedRef.current) setIsTranscribing(true);
      const response = await api.transcribeVoice({
        audioBase64: arrayBufferToBase64(pcm),
        sampleRate: recorder.sampleRate
      });
      if (response.ok && response.data) {
        if (response.data.transcript) {
          if (mountedRef.current) onTranscript(response.data.transcript);
        } else {
          if (mountedRef.current) onError?.("没有识别出内容，请靠近麦克风再试一次。");
        }
      } else {
        if (mountedRef.current) onError?.(response.error?.message ?? "语音识别失败，请稍后重试。");
      }
    } catch {
      if (mountedRef.current) onError?.("语音识别连接失败，请稍后重试。");
    } finally {
      if (mountedRef.current) setIsTranscribing(false);
    }
  }, [api, onError, onTranscript]);

  const startRecording = useCallback(async () => {
    if (isRecording || isStarting || isTranscribing) return;
    const generation = startGenerationRef.current + 1;
    startGenerationRef.current = generation;
    setIsStarting(true);
    try {
      const recorder = await PcmRecorder.start();
      if (!mountedRef.current || startGenerationRef.current !== generation) {
        recorder.dispose();
        return;
      }
      recorderRef.current = recorder;
      setIsRecording(true);
      stopTimerRef.current = window.setTimeout(() => void stopRecording(), MAX_RECORDING_MS);
    } catch {
      if (mountedRef.current) onError?.("无法访问麦克风，请检查系统麦克风权限。");
    } finally {
      if (mountedRef.current && startGenerationRef.current === generation) setIsStarting(false);
    }
  }, [isRecording, isStarting, isTranscribing, onError, stopRecording]);

  useEffect(() => () => {
    mountedRef.current = false;
    startGenerationRef.current += 1;
    recorderRef.current?.dispose();
    recorderRef.current = null;
    if (stopTimerRef.current !== null) window.clearTimeout(stopTimerRef.current);
  }, []);

  if (availability === "unavailable") {
    return (
      <button
        type="button"
        className="icon-button composer__voice"
        disabled
        title={unavailableDetail || "语音输入暂不可用"}
        aria-label="语音输入暂不可用"
      >
        <Mic size={16} aria-hidden="true" />
      </button>
    );
  }

  return (
    <button
      type="button"
      className={isRecording ? "icon-button composer__voice composer__voice--recording" : "icon-button composer__voice"}
      disabled={disabled || availability === "unknown" || isStarting || isTranscribing}
      onClick={() => (isRecording ? void stopRecording() : void startRecording())}
      title={isRecording ? "停止录音并识别" : isTranscribing ? "正在识别…" : "按住说话前先点击开始录音"}
      aria-label={isRecording ? "停止录音并识别" : "开始语音输入"}
      aria-pressed={isRecording}
    >
      {isRecording ? <Square size={16} aria-hidden="true" /> : <Mic size={16} aria-hidden="true" />}
    </button>
  );
}

/** Captures microphone audio and downsamples it to 16 kHz mono PCM16. */
export class PcmRecorder {
  readonly sampleRate = TARGET_SAMPLE_RATE;
  private chunks: Float32Array[] = [];
  private constructor(
    private readonly stream: MediaStream,
    private readonly context: AudioContext,
    private readonly source: MediaStreamAudioSourceNode,
    private readonly processor: ScriptProcessorNode,
    private readonly silentSink: GainNode
  ) {}

  static async start(): Promise<PcmRecorder> {
    let stream: MediaStream | null = null;
    let context: AudioContext | null = null;
    let source: MediaStreamAudioSourceNode | null = null;
    let processor: ScriptProcessorNode | null = null;
    let silentSink: GainNode | null = null;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1 } });
      context = new AudioContext();
      source = context.createMediaStreamSource(stream);
      processor = context.createScriptProcessor(4096, 1, 1);
      silentSink = context.createGain();
      silentSink.gain.value = 0;
      const recorder = new PcmRecorder(stream, context, source, processor, silentSink);
      processor.onaudioprocess = (event) => {
        recorder.chunks.push(new Float32Array(event.inputBuffer.getChannelData(0)));
      };
      source.connect(processor);
      processor.connect(silentSink);
      silentSink.connect(context.destination);
      return recorder;
    } catch (error) { // broad-exception-boundary
      try {
        source?.disconnect();
        processor?.disconnect();
        silentSink?.disconnect();
      } catch {
        // Partial Web Audio graphs are best-effort during initialization failure.
      }
      stream?.getTracks().forEach((track) => track.stop());
      if (context) await context.close().catch(() => undefined);
      throw error;
    }
  }

  async stop(): Promise<ArrayBuffer> {
    const inputRate = this.context.sampleRate;
    this.dispose();
    const total = this.chunks.reduce((sum, chunk) => sum + chunk.length, 0);
    const merged = new Float32Array(total);
    let offset = 0;
    for (const chunk of this.chunks) {
      merged.set(chunk, offset);
      offset += chunk.length;
    }
    this.chunks = [];
    return floatToPcm16(downsample(merged, inputRate, TARGET_SAMPLE_RATE));
  }

  dispose(): void {
    this.processor.onaudioprocess = null;
    try {
      this.source.disconnect();
      this.processor.disconnect();
      this.silentSink.disconnect();
    } catch {
      // already disconnected
    }
    this.stream.getTracks().forEach((track) => track.stop());
    void this.context.close().catch(() => undefined);
  }
}

function downsample(input: Float32Array, inputRate: number, targetRate: number): Float32Array {
  if (inputRate <= targetRate) return input;
  const ratio = inputRate / targetRate;
  const output = new Float32Array(Math.floor(input.length / ratio));
  for (let index = 0; index < output.length; index += 1) {
    const start = Math.floor(index * ratio);
    const end = Math.min(Math.floor((index + 1) * ratio), input.length);
    let sum = 0;
    for (let sample = start; sample < end; sample += 1) sum += input[sample];
    output[index] = end > start ? sum / (end - start) : 0;
  }
  return output;
}

function floatToPcm16(input: Float32Array): ArrayBuffer {
  const buffer = new ArrayBuffer(input.length * 2);
  const view = new DataView(buffer);
  for (let index = 0; index < input.length; index += 1) {
    const clamped = Math.max(-1, Math.min(1, input[index]));
    view.setInt16(index * 2, clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff, true);
  }
  return buffer;
}

function arrayBufferToBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  const chunkSize = 0x8000;
  for (let index = 0; index < bytes.length; index += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(index, index + chunkSize));
  }
  return btoa(binary);
}
