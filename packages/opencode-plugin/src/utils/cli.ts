/**
 * Sugar CLI wrapper for OpenCode plugin
 */

import type {
  CommandResult,
  AddOptions,
  ListOptions,
  RecallOptions,
  RunOptions,
  ShellExecutor,
} from "../types";

export class SugarCLI {
  constructor(
    private readonly command: string,
    private readonly cwd: string,
    private readonly $: ShellExecutor
  ) {}

  async exec(
    args: string[],
    options: { timeout?: number } = {}
  ): Promise<CommandResult> {
    const timeout = options.timeout ?? 30000;
    const cmdStr = `${this.command} ${args.join(" ")}`;

    try {
      // Use template literal with the shell executor
      const proc = this.$`${cmdStr}`.cwd(this.cwd).timeout(timeout);
      const result = await proc;

      return {
        success: result.exitCode === 0,
        code: result.exitCode,
        stdout: result.stdout.toString().trim(),
        stderr: result.stderr.toString().trim(),
      };
    } catch (error: unknown) {
      const err = error as {
        exitCode?: number;
        stdout?: Buffer;
        stderr?: Buffer;
        message?: string;
      };
      return {
        success: false,
        code: err.exitCode ?? -1,
        stdout: err.stdout?.toString().trim() ?? "",
        stderr: err.stderr?.toString().trim() ?? err.message ?? "Unknown error",
      };
    }
  }

  async add(title: string, options: AddOptions = {}): Promise<CommandResult> {
    const args = ["add", `"${title.replace(/"/g, '\\"')}"`];
    if (options.type) args.push("--type", options.type);
    if (options.priority) args.push("--priority", String(options.priority));
    if (options.urgent) args.push("--urgent");
    if (options.description)
      args.push("--description", `"${options.description.replace(/"/g, '\\"')}"`);
    if (options.triage) args.push("--triage");
    return this.exec(args);
  }

  async list(options: ListOptions = {}): Promise<CommandResult> {
    const args = ["list"];
    if (options.status && options.status !== "all")
      args.push("--status", options.status);
    if (options.type) args.push("--type", options.type);
    if (options.priority) args.push("--priority", String(options.priority));
    if (options.limit) args.push("--limit", String(options.limit));
    return this.exec(args);
  }

  async view(taskId: string): Promise<CommandResult> {
    return this.exec(["view", taskId]);
  }

  async remove(taskId: string): Promise<CommandResult> {
    return this.exec(["remove", taskId, "--yes"]);
  }

  async priority(
    taskId: string,
    level: number | string
  ): Promise<CommandResult> {
    const args = ["priority", taskId];
    if (typeof level === "number") {
      args.push("--priority", String(level));
    } else {
      args.push(`--${level}`);
    }
    return this.exec(args);
  }

  async status(): Promise<CommandResult> {
    return this.exec(["status"]);
  }

  async recall(query: string, options: RecallOptions = {}): Promise<CommandResult> {
    const args = ["recall", `"${query.replace(/"/g, '\\"')}"`];
    if (options.type && options.type !== "all") args.push("--type", options.type);
    if (options.limit) args.push("--limit", String(options.limit));
    return this.exec(args);
  }

  async remember(content: string, memoryType: string): Promise<CommandResult> {
    return this.exec([
      "remember",
      `"${content.replace(/"/g, '\\"')}"`,
      "--type",
      memoryType,
    ]);
  }

  async run(options: RunOptions = {}): Promise<CommandResult> {
    const args = ["run", "--once"];
    if (options.dryRun) args.push("--dry-run");
    if (options.validate) args.push("--validate");
    return this.exec(args, { timeout: 300000 }); // 5 min timeout
  }

  async exportContext(): Promise<CommandResult> {
    return this.exec(["export-context"]);
  }
}

export async function detectSugarCommand(
  $: ShellExecutor,
  cwd: string
): Promise<string | null> {
  const candidates = [
    "sugar",
    `${process.env.HOME}/.local/bin/sugar`,
    `${cwd}/venv/bin/sugar`,
    `${cwd}/.venv/bin/sugar`,
  ];

  for (const cmd of candidates) {
    try {
      const result = await $`${cmd} --version`.cwd(cwd).quiet();
      if (result.exitCode === 0) return cmd;
    } catch {
      continue;
    }
  }
  return null;
}
