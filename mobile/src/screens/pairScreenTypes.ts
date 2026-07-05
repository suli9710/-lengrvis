export interface SecurityNotice {
  tone: "safe" | "warning" | "danger";
  title: string;
  detail: string;
}

export interface PairingFailureNotice {
  title: string;
  detail: string;
  action: string;
  checks?: Array<{
    title: string;
    detail: string;
  }>;
}

export type PairingFailureSource = "scan" | "input";
