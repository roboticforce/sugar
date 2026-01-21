/**
 * Sugar OpenCode Plugin Hooks
 *
 * Registers session and tool lifecycle hooks for Sugar integration.
 */

import type { SugarCLI } from "../utils/cli";
import type { SugarPluginConfig, PluginContext } from "../types";
import { log } from "../utils/logger";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export interface HooksObject {
  [key: string]: (...args: any[]) => Promise<void> | void;
}

interface SessionEvent {
  id: string;
  project?: unknown;
}

interface ToolEvent {
  tool: { name: string };
  result?: unknown;
  error?: Error;
  sessionId?: string;
}

interface ErrorEvent {
  error?: Error | { message?: string };
  sessionId?: string;
}

/**
 * Create session and tool lifecycle hooks
 */
export function createHooks(
  cli: SugarCLI,
  client: PluginContext["client"],
  config: SugarPluginConfig
): HooksObject {
  return {
    // Inject relevant memories when session starts
    "session.created": async (session: SessionEvent) => {
      if (!config.autoInjectMemories) return;

      log(config.debug, "Session created, injecting memories", {
        sessionId: session.id,
      });

      try {
        // Get project context for memory query
        const contextResult = await cli.exportContext();
        if (contextResult.success && contextResult.stdout) {
          await client.app.log({
            service: "sugar-plugin",
            level: "info",
            message: "Sugar context injected into session",
            extra: { sessionId: session.id },
          });
        }
      } catch (error) {
        await client.app.log({
          service: "sugar-plugin",
          level: "warn",
          message: "Failed to inject Sugar memories",
          extra: { error: String(error) },
        });
      }
    },

    // Store learnings after session completes meaningful work
    "session.idle": async (session: SessionEvent) => {
      if (!config.storeLearnings) return;

      log(config.debug, "Session idle, checking for learnings", {
        sessionId: session.id,
      });

      // Session.idle fires when agent is waiting - good time to persist any learnings
    },

    // Track tool execution outcomes for learning
    "tool.execute.after": async (event: ToolEvent) => {
      if (!config.storeLearnings) return;

      const { tool, error } = event;

      // Track significant operations for learning
      if (tool.name.startsWith("sugar_")) {
        log(config.debug, "Sugar tool executed", {
          tool: tool.name,
          success: !error,
        });

        // Store error patterns for future reference
        if (error) {
          try {
            await cli.remember(
              `Tool ${tool.name} failed: ${error.message}`,
              "error_pattern"
            );
          } catch {
            // Silent fail for memory storage
          }
        }
      }
    },

    // Handle session compaction for multi-agent handoff
    "session.compacted": async (event: { sessionId: string }) => {
      log(config.debug, "Session compacted", { sessionId: event.sessionId });

      // Export current context for potential handoff
      try {
        const contextResult = await cli.exportContext();
        if (contextResult.success) {
          await client.app.log({
            service: "sugar-plugin",
            level: "debug",
            message: "Context exported for compaction",
          });
        }
      } catch {
        // Silent fail
      }
    },

    // Log session errors
    "session.error": async (event: ErrorEvent) => {
      await client.app.log({
        service: "sugar-plugin",
        level: "error",
        message: "Session error occurred",
        extra: {
          error: event.error,
          sessionId: event.sessionId,
        },
      });

      // Store error for learning
      if (config.storeLearnings) {
        try {
          const errorMsg =
            (event.error as Error)?.message ||
            String(event.error) ||
            "Unknown error";
          await cli.remember(`Session error: ${errorMsg}`, "error_pattern");
        } catch {
          // Silent fail
        }
      }
    },
  };
}
