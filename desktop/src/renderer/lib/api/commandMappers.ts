import type { CommandExecutionResult, CommandInfo } from "../../../shared/types";
import type { BackendCommandExecutionResult, BackendCommandInfo } from "./backendTypes";

export function mapCommandInfo(command: BackendCommandInfo): CommandInfo {
  return {
    name: String(command.name ?? ""),
    title: String(command.title ?? command.name ?? ""),
    description: String(command.description ?? ""),
    category: String(command.category ?? ""),
    inputSchema: (command.input_schema && typeof command.input_schema === "object" ? command.input_schema : {}) as Record<string, unknown>
  };
}

export function mapCommandExecutionResult(result: BackendCommandExecutionResult): CommandExecutionResult {
  return {
    ok: Boolean(result.ok),
    command: String(result.command ?? ""),
    title: result.title ? String(result.title) : undefined,
    result: result.result,
    diagnostics: Array.isArray(result.diagnostics) ? result.diagnostics.map(String) : undefined,
    error: result.error ? String(result.error) : undefined,
    nextAction: result.next_action ? String(result.next_action) : undefined
  };
}
