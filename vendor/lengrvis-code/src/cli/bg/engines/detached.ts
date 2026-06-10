import { closeSync, mkdirSync, openSync } from 'fs'
import { dirname } from 'path'
import { buildCliLaunch, spawnCli } from '../../../utils/cliLaunch.js'
import type {
  BgEngine,
  BgStartOptions,
  BgStartResult,
  SessionEntry,
} from '../engine.js'
import { tailLog } from '../tail.js'

export class DetachedEngine implements BgEngine {
  readonly name = 'detached' as const
  readonly supportsInteractiveInput = false

  async available(): Promise<boolean> {
    return true
  }

  async start(opts: BgStartOptions): Promise<BgStartResult> {
    mkdirSync(dirname(opts.logPath), { recursive: true })

    const logFd = openSync(opts.logPath, 'a')

    const launch = buildCliLaunch(opts.args, {
      env: {
        ...opts.env,
        LENGRVIS_CODE_SESSION_KIND: 'bg',
        LENGRVIS_CODE_SESSION_NAME: opts.sessionName,
        LENGRVIS_CODE_SESSION_LOG: opts.logPath,
      } as NodeJS.ProcessEnv,
    })

    const child = spawnCli(launch, {
      detached: true,
      stdio: ['ignore', logFd, logFd],
      cwd: opts.cwd,
    })

    child.unref()
    closeSync(logFd)

    const pid = child.pid ?? 0

    return {
      pid,
      sessionName: opts.sessionName,
      logPath: opts.logPath,
      engineUsed: 'detached',
    }
  }

  async attach(session: SessionEntry): Promise<void> {
    if (!session.logPath) {
      throw new Error(`Session ${session.sessionId} has no log path.`)
    }
    await tailLog(session.logPath)
  }
}
