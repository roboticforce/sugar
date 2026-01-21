/**
 * Sugar OpenCode Plugin Types
 */

// Re-export hook and tool types
export type { HooksObject } from "../hooks";
export type { ToolDefinition } from "../tools";

export interface SugarPluginConfig {
  /** Auto-inject memories on session start */
  autoInjectMemories: boolean;
  /** Number of memories to inject */
  memoryLimit: number;
  /** Store learnings after task execution */
  storeLearnings: boolean;
  /** Memory types to recall */
  memoryTypes: string[];
  /** Enable debug logging */
  debug: boolean;
}

export interface CommandResult {
  success: boolean;
  code: number;
  stdout: string;
  stderr: string;
}

export interface AddOptions {
  type?: string;
  priority?: number;
  urgent?: boolean;
  description?: string;
  triage?: boolean;
}

export interface ListOptions {
  status?: "pending" | "hold" | "active" | "completed" | "failed" | "all";
  type?: string;
  priority?: number;
  limit?: number;
}

export interface RecallOptions {
  type?:
    | "decision"
    | "preference"
    | "research"
    | "file_context"
    | "error_pattern"
    | "outcome"
    | "all";
  limit?: number;
}

export interface RunOptions {
  dryRun?: boolean;
  validate?: boolean;
}

export interface Task {
  id: string;
  title: string;
  type: string;
  priority: number;
  status: string;
  description?: string;
  created_at?: string;
  attempts?: number;
}

export interface PluginContext {
  project?: {
    config?: {
      sugar?: Partial<SugarPluginConfig>;
    };
  };
  client: {
    app: {
      log: (msg: {
        service: string;
        level: string;
        message: string;
        extra?: Record<string, unknown>;
      }) => Promise<void>;
    };
  };
  $: ShellExecutor;
  directory: string;
  worktree?: string;
}

export type ShellExecutor = (
  strings: TemplateStringsArray,
  ...values: unknown[]
) => ShellProcess;

export interface ShellProcess {
  cwd: (path: string) => ShellProcess;
  timeout: (ms: number) => Promise<ShellResult>;
  quiet: () => Promise<ShellResult>;
}

export interface ShellResult {
  exitCode: number;
  stdout: Buffer;
  stderr: Buffer;
}
