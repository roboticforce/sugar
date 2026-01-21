/**
 * Sugar OpenCode Plugin Tools
 *
 * Registers custom tools for Sugar task management in OpenCode.
 */

import { z } from "zod";
import type { SugarCLI } from "../utils/cli";
import type { SugarPluginConfig } from "../types";

// Tool registration interface (simplified for compatibility)
export interface ToolDefinition {
  name: string;
  description: string;
  parameters: z.ZodObject<z.ZodRawShape>;
  execute: (args: Record<string, unknown>) => Promise<string>;
}

/**
 * Create all Sugar tools for OpenCode
 */
export function createTools(
  cli: SugarCLI,
  _config: SugarPluginConfig
): ToolDefinition[] {
  return [
    // sugar_add - Create a new task
    {
      name: "sugar_add",
      description: `Add a new task to Sugar's autonomous work queue.
The task will be picked up by Sugar's background worker for autonomous execution.
Use --triage for complex tasks that need intelligent decomposition.`,
      parameters: z.object({
        title: z.string().describe("Task title - clear, actionable description"),
        type: z
          .enum(["bug_fix", "feature", "test", "refactor", "documentation"])
          .default("feature")
          .describe("Task type category"),
        priority: z
          .number()
          .min(1)
          .max(5)
          .default(3)
          .describe("Priority: 1=minimal, 2=low, 3=normal, 4=high, 5=urgent"),
        urgent: z
          .boolean()
          .default(false)
          .describe("Mark as urgent (sets priority to 5)"),
        description: z
          .string()
          .optional()
          .describe("Detailed task description with context"),
        triage: z
          .boolean()
          .default(false)
          .describe("Enable intelligent triage for complex tasks"),
      }),
      async execute(args) {
        const result = await cli.add(args.title as string, {
          type: args.type as string,
          priority: args.priority as number,
          urgent: args.urgent as boolean,
          description: args.description as string | undefined,
          triage: args.triage as boolean,
        });

        if (result.success) {
          const match =
            result.stdout.match(/Task.*?:\s*([^\s]+)/i) ||
            result.stdout.match(/ID:\s*([^\s]+)/i);
          const taskId = match?.[1] ?? "unknown";
          return `Task created successfully!\nID: ${taskId}\n\n${result.stdout}`;
        }
        throw new Error(
          `Failed to create task: ${result.stderr || result.stdout}`
        );
      },
    },

    // sugar_list - List tasks
    {
      name: "sugar_list",
      description:
        "List Sugar tasks with optional filtering by status, type, or priority",
      parameters: z.object({
        status: z
          .enum(["pending", "hold", "active", "completed", "failed", "all"])
          .optional()
          .describe("Filter by task status"),
        type: z.string().optional().describe("Filter by task type"),
        priority: z
          .number()
          .min(1)
          .max(5)
          .optional()
          .describe("Filter by priority level"),
        limit: z
          .number()
          .default(20)
          .describe("Maximum number of tasks to return"),
      }),
      async execute(args) {
        const result = await cli.list({
          status: args.status as
            | "pending"
            | "hold"
            | "active"
            | "completed"
            | "failed"
            | "all"
            | undefined,
          type: args.type as string | undefined,
          priority: args.priority as number | undefined,
          limit: args.limit as number,
        });

        if (result.success) {
          return result.stdout || "No tasks found matching criteria";
        }
        throw new Error(`Failed to list tasks: ${result.stderr}`);
      },
    },

    // sugar_view - View task details
    {
      name: "sugar_view",
      description:
        "View detailed information about a specific task including history and context",
      parameters: z.object({
        taskId: z.string().describe("Task ID to view (e.g., abc123)"),
      }),
      async execute(args) {
        const result = await cli.view(args.taskId as string);

        if (result.success) {
          return result.stdout;
        }
        throw new Error(`Task not found: ${args.taskId}`);
      },
    },

    // sugar_remove - Remove a task
    {
      name: "sugar_remove",
      description: "Remove a task from the work queue (cannot be undone)",
      parameters: z.object({
        taskId: z.string().describe("Task ID to remove"),
      }),
      async execute(args) {
        const result = await cli.remove(args.taskId as string);

        if (result.success) {
          return `Task ${args.taskId} removed successfully`;
        }
        throw new Error(`Failed to remove task: ${result.stderr}`);
      },
    },

    // sugar_priority - Change task priority
    {
      name: "sugar_priority",
      description:
        "Change the priority of a task. Higher priority tasks are executed first.",
      parameters: z.object({
        taskId: z.string().describe("Task ID to reprioritize"),
        level: z
          .union([
            z.number().min(1).max(5),
            z.enum(["urgent", "high", "normal", "low", "minimal"]),
          ])
          .describe("New priority: 1-5 or urgent/high/normal/low/minimal"),
      }),
      async execute(args) {
        const result = await cli.priority(
          args.taskId as string,
          args.level as number | string
        );

        if (result.success) {
          return `Task ${args.taskId} priority updated to ${args.level}`;
        }
        throw new Error(`Failed to update priority: ${result.stderr}`);
      },
    },

    // sugar_status - Get system status
    {
      name: "sugar_status",
      description:
        "Get Sugar system status including task queue metrics and worker status",
      parameters: z.object({}),
      async execute() {
        const result = await cli.status();

        if (result.success) {
          return result.stdout;
        }
        throw new Error(`Failed to get status: ${result.stderr}`);
      },
    },

    // sugar_recall - Search memories
    {
      name: "sugar_recall",
      description: `Search Sugar's memory system for relevant context, decisions, and learnings.
Use before starting work to understand past decisions and patterns.`,
      parameters: z.object({
        query: z.string().describe("Search query for memories"),
        type: z
          .enum([
            "decision",
            "preference",
            "research",
            "file_context",
            "error_pattern",
            "outcome",
            "all",
          ])
          .default("all")
          .describe("Type of memory to search"),
        limit: z.number().default(5).describe("Maximum memories to return"),
      }),
      async execute(args) {
        const result = await cli.recall(args.query as string, {
          type: args.type as
            | "decision"
            | "preference"
            | "research"
            | "file_context"
            | "error_pattern"
            | "outcome"
            | "all",
          limit: args.limit as number,
        });

        if (result.success) {
          return result.stdout || "No relevant memories found";
        }
        throw new Error(`Memory recall failed: ${result.stderr}`);
      },
    },

    // sugar_run - Execute one cycle
    {
      name: "sugar_run",
      description: `Execute one autonomous development cycle.
Sugar will pick the highest priority pending task and execute it.
Use --dry-run to simulate without making changes.`,
      parameters: z.object({
        dryRun: z
          .boolean()
          .default(false)
          .describe("Simulate execution without making changes"),
        validate: z
          .boolean()
          .default(false)
          .describe("Validate configuration before running"),
      }),
      async execute(args) {
        const result = await cli.run({
          dryRun: args.dryRun as boolean,
          validate: args.validate as boolean,
        });

        if (result.success) {
          return result.stdout;
        }
        throw new Error(`Execution failed: ${result.stderr}`);
      },
    },
  ];
}
