import { isEnvTruthy } from './envUtils.js'

/**
 * Centralized runtime check for agent teams/teammate features.
 * This is the single gate that should be checked everywhere teammates
 * are referenced (prompts, code, tools isEnabled, UI, etc.).
 *
 * Fork build: enabled by default. Can be disabled via
 * LENGRVIS_CODE_EXPERIMENTAL_AGENT_TEAMS=0 if needed.
 */
export function isAgentSwarmsEnabled(): boolean {
  if (isEnvTruthy(process.env.LENGRVIS_CODE_EXPERIMENTAL_AGENT_TEAMS_DISABLED)) {
    return false
  }

  return true
}
