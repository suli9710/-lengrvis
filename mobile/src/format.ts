import type { BackendApproval } from "./api";

export function approvalTitle(approval: BackendApproval): string {
  if (approval.approval_type === "tool_call") return "Tool approval";
  return titleCase(approval.approval_type.replace(/[_-]/g, " "));
}

export function approvalStatusLabel(status: BackendApproval["status"]): string {
  if (status === "approved") return "Approved";
  if (status === "rejected") return "Denied";
  if (status === "expired") return "Expired";
  return "Pending";
}

export function formatPreview(value: unknown): string {
  if (!value || typeof value !== "object") return "No preview payload";
  const objectValue = value as Record<string, unknown>;
  const preview = objectValue.diff_preview;
  if (Array.isArray(preview) && preview.length > 0) {
    return preview
      .slice(0, 3)
      .map((item) => {
        if (!item || typeof item !== "object") return String(item);
        const row = item as Record<string, unknown>;
        const action = String(row.action ?? row.kind ?? "change");
        const path = String(row.path ?? row.to ?? row.from ?? "");
        return path ? `${action}: ${path}` : action;
      })
      .join("\n");
  }
  return JSON.stringify(value, null, 2);
}

export function shortDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function titleCase(value: string): string {
  return value.replace(/\b\w/g, (character) => character.toUpperCase());
}
