export interface ScreenFrame {
  sequence: number;
  image: string;
  timestamp: string;
  width: number;
  height: number;
  originalWidth: number;
  originalHeight: number;
  screenOriginX: number;
  screenOriginY: number;
}

export type RemoteInputPayload =
  | { type: "click"; x: number; y: number }
  | { type: "type"; text: string }
  | { type: "key"; key: string };
