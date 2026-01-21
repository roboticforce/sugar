/**
 * Sugar OpenCode Plugin
 *
 * Provides seamless integration between Sugar autonomous task queue
 * and OpenCode AI coding agent.
 *
 * Features:
 * - Custom tools for task management
 * - Session hooks for memory injection
 * - Slash command support
 */

import type { SugarPluginConfig, PluginContext } from "./types";
import { SugarCLI, detectSugarCommand, log } from "./utils";
import { createTools } from "./tools";
import { createHooks } from "./hooks";

export type { SugarPluginConfig, HooksObject, ToolDefinition } from "./types";
export { SugarCLI, detectSugarCommand } from "./utils";
export { createTools } from "./tools";
export { createHooks } from "./hooks";

const DEFAULT_CONFIG: SugarPluginConfig = {
  autoInjectMemories: true,
  memoryLimit: 5,
  storeLearnings: true,
  memoryTypes: ["decision", "preference", "error_pattern"],
  debug: false,
};

/**
 * Sugar Plugin for OpenCode
 *
 * @param ctx - Plugin context from OpenCode
 * @returns Plugin hooks and tools
 */
export const SugarPlugin = async (ctx: PluginContext) => {
  const { project, client, $, directory } = ctx;

  // Load config from opencode.json sugar section
  const config: SugarPluginConfig = {
    ...DEFAULT_CONFIG,
    ...(project?.config?.sugar ?? {}),
  };

  log(config.debug, "Sugar plugin initializing", { directory });

  // Detect Sugar CLI
  const sugarCmd = await detectSugarCommand($, directory);
  if (!sugarCmd) {
    await client.app.log({
      service: "sugar-plugin",
      level: "warn",
      message:
        "Sugar CLI not found. Install: pip install sugarai && sugar init",
    });
    return {};
  }

  log(config.debug, `Sugar CLI found: ${sugarCmd}`);

  // Create CLI wrapper
  const cli = new SugarCLI(sugarCmd, directory, $);

  // Create tools (for reference - actual registration depends on OpenCode plugin API)
  const tools = createTools(cli, config);

  // Create hooks
  const hooks = createHooks(cli, client, config);

  // Return plugin interface
  return {
    tools,
    hooks,
    // Expose CLI for advanced use cases
    cli,
    config,
  };
};

export default SugarPlugin;
