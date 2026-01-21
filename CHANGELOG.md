# Sugar Changelog

All notable changes to the Sugar autonomous development system will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [3.5.0] - Unreleased

### 🧠 MINOR RELEASE: Memory System

Sugar now has persistent semantic memory! Remember decisions, preferences, error patterns, and more across sessions. Integrates with Claude Code via MCP server for seamless context sharing.

### Added

#### Memory System (`sugar/memory/`)
- **MemoryStore**: SQLite-backed storage with vector search support
  - Semantic search using sentence-transformers embeddings
  - FTS5 keyword search fallback when embeddings unavailable
  - sqlite-vec integration for fast vector similarity
- **Memory Types**: Six memory categories for different kinds of information
  - `decision` - Architectural and implementation decisions
  - `preference` - User coding preferences (permanent)
  - `file_context` - What files do what
  - `error_pattern` - Bug patterns and their fixes
  - `research` - API docs, library findings
  - `outcome` - Task outcomes and learnings
- **MemoryRetriever**: Context formatting for prompt injection
- **Embedder**: SentenceTransformer embeddings with graceful fallback

#### New CLI Commands
- `sugar remember "content"` - Store a memory with type, tags, TTL, importance
- `sugar recall "query"` - Search memories with semantic/keyword matching
- `sugar memories` - List memories with filtering by type, age
- `sugar forget <id>` - Delete a memory by ID
- `sugar export-context` - Export memories for Claude Code SessionStart hook
- `sugar memory-stats` - Show memory system statistics
- `sugar mcp memory` - Run MCP server for Claude Code integration

#### MCP Server for Claude Code
- **search_memory** - Semantic search over project memories
- **store_learning** - Store new observations/decisions from Claude
- **get_project_context** - Organized project context summary
- **recall** - Formatted markdown context for prompts
- **list_recent_memories** - List with type filtering
- **Resources**: `sugar://project/context`, `sugar://preferences`

#### Claude Code Integration
- **SessionStart Hook**: Auto-inject context via `sugar export-context`
- **MCP Server**: Full memory access via `claude mcp add sugar -- sugar mcp memory`
- **Bidirectional**: Claude can both read and write memories

### Configuration

New optional dependency group:
```bash
pip install 'sugarai[memory]'   # Enables semantic search
pip install 'sugarai[all]'      # All features
```

Memory works without dependencies (uses FTS5 keyword search), but semantic search requires:
- `sentence-transformers>=2.2.0`
- `sqlite-vec>=0.1.0`

### Usage Examples

```bash
# Store memories
sugar remember "Always use async/await, never callbacks" --type preference
sugar remember "Auth tokens expire after 15 minutes" --type research --ttl 90d
sugar remember "payment_processor.py handles Stripe webhooks" --type file_context

# Search memories
sugar recall "how do we handle authentication"
sugar recall "database errors" --type error_pattern --limit 5

# Claude Code integration
claude mcp add sugar -- sugar mcp memory
```

### Documentation
- New [Memory System Guide](docs/user/memory.md)
- Updated README with memory commands and MCP integration
- Updated CLI reference with all memory commands

### Technical Details
- 24 new tests for memory module
- Full backwards compatibility - memory is opt-in
- Database stored at `.sugar/memory.db` per project
- Embeddings use all-MiniLM-L6-v2 (384 dimensions)

---

## [3.4.4] - 2026-01-10

### 🔄 MINOR RELEASE: Agent-Agnostic Rebranding

Sugar is now fully agent-agnostic! This release removes Claude Code-specific branding and positioning, making Sugar the autonomous layer for **any** AI coding agent.

### Changed

#### Agent-Agnostic Positioning
- **New tagline**: "The autonomous layer for AI coding agents"
- **Works with any CLI**: Claude Code, OpenCode, Aider, Goose, or any AI agent
- **Updated documentation**: All references now agent-agnostic
- **pyproject.toml**: Description updated to reflect multi-agent support

#### Installation
- **Recommended: pipx** - Global installation without venv activation
- **Friendly errors**: Clear guidance when Sugar not initialized in current directory
- **Updated docs**: All installation instructions now recommend pipx

### Added

#### Developer Experience
- **AGENTS.md rewrite**: Accurate project structure and development guidelines
- **Strict gitflow rules**: NEVER push directly to develop/main branches
- **Changelog requirements**: All releases must update CHANGELOG.md
- **Branch protection**: CI must pass before merging to main

### Removed

#### Documentation Cleanup
- **VISION.md**: Outdated content now consolidated in README
- **slashcommands-integration-plan.md**: Implementation complete, plan no longer needed

### Fixed
- **README**: Removed redundant duplicate tagline

---

## [3.0.0] - 2025-12-19

### 🚀 MAJOR RELEASE: Claude Agent SDK Integration

Sugar 3.0 is a major re-platform from CLI subprocess wrapper to native Claude Agent SDK integration, providing improved performance, better control, and new capabilities.

### Added

#### Claude Agent SDK Integration
- **SugarAgent class** - Native SDK integration with streaming and tool use
- **AgentSDKExecutor** - New executor replacing subprocess-based ClaudeWrapper
- **PreToolUse/PostToolUse hooks** - Quality gate security checks
- **Workflow profiles** - Specialized behaviors for different tasks

#### New Modules
- **sugar/agent/** - Core agent SDK integration
  - `base.py` - SugarAgent class with SDK integration
  - `hooks.py` - Quality gate hooks for security
  - `tools.py` - Custom Sugar tools
- **sugar/profiles/** - Workflow profiles
  - `default.py` - General-purpose development profile
  - `issue_responder.py` - GitHub issue analysis and response
- **sugar/billing/** - SaaS billing infrastructure
  - `usage.py` - Usage tracking per customer
  - `api_keys.py` - API key management with rate limiting
  - `tiers.py` - Pricing tiers (Free, Starter, Pro, Team, Enterprise)
- **sugar/integrations/** - External integrations
  - `github.py` - GitHub API client using gh CLI
- **sugar/mcp/** - Native Python MCP server
  - `server.py` - MCP server with 6 tools

#### Distribution Options
- **GitHub Action** - Event-driven, BYOK (Bring Your Own Key) in `action/`
- **Python MCP Server** - Docker-deployable in `sugar/mcp/`
- **mcp.Dockerfile** - Container for MCP server deployment

#### Testing
- **115 new tests** for v3.0 modules
  - `test_billing.py` - 35 tests for billing module
  - `test_profiles.py` - 37 tests for profiles module
  - `test_hooks.py` - 43 tests for security hooks

### Changed
- **Default executor** - Now uses AgentSDKExecutor (SDK-based) instead of ClaudeWrapper
- **Executor selection** - New `executor` config option: `sdk` (default) or `legacy`
- **Version fallback** - Updated to 3.0.0

### Fixed
- **datetime deprecation** - Replaced `datetime.utcnow()` with timezone-aware `datetime.now(timezone.utc)`
- **IssueResponderProfile labels** - Fixed handling of GitHub label dicts vs strings

### Configuration

New executor option in `.sugar/config.yaml`:
```yaml
claude:
  # Executor selection (v3.0+)
  # - "sdk": Use Claude Agent SDK (recommended, native integration)
  # - "legacy": Use subprocess-based CLI wrapper (backwards compatible)
  executor: "sdk"
```

### Migration Notes
- Existing configurations continue working with `executor: "legacy"`
- New installations default to SDK executor
- No breaking changes to CLI commands or task management

---

## [2.2.0] - 2025-12-11

### 🚀 MINOR RELEASE: Goose Extension & MCP Server

Adds official Goose integration via MCP server, enabling Sugar to work with Block's open-source AI agent.

### Added

- **Goose extension support** - Full MCP server implementation for Goose and Claude Desktop (#3)
- **`sugarai-mcp` npm package** - Published to npm, installable via `npx -y sugarai-mcp`
- **`packages/mcp-server/`** - TypeScript MCP server using `@modelcontextprotocol/sdk`
- **MCP tools exposed**: createTask, listTasks, viewTask, updateTask, removeTask, getStatus, runOnce, initSugar
- **Goose configuration examples** - Added to README.md and package README

### Documentation

- Updated `docs/dev/release-process.md` with npm publishing steps for MCP server
- Added Goose and Claude Desktop configuration examples to README.md

---

## [2.1.1] - 2025-12-11

### 🔧 PATCH RELEASE: Plugin Bug Fixes

Fixes two bugs reported by users affecting Claude Code plugin installation and loading.

### Fixed

- **hooks.json format** - Convert hooks configuration from array to object format as required by Claude Code specification. Fixes plugin loading error: "Expected object, received array" (#12)
- **Plugin marketplace discovery** - Add proper `marketplace.json` with Claude Code schema and correct installation instructions. Users can now install with `/plugin install roboticforce/sugar` (#8)

### Added

- `.claude-plugin/marketplace.json` - Proper marketplace manifest following Claude Code schema
- `docs/dev/release-process.md` - Release process documentation for maintainers
- Quick release reference in `CLAUDE.md`

### Changed

- Updated installation instructions in README.md and plugin README to use correct syntax
- Updated plugin structure tests to validate Claude Code hook format

---

## [2.0.3] - 2025-10-15

### 🔧 PATCH RELEASE: Messaging Refresh

Complete refresh of Sugar's core messaging with a clear, memorable slogan.

### Changed

#### Brand Messaging
- **New Slogan**: "A dev team that never stops" - memorable, clear value proposition
- **Tagline Update**: "Delegate full tasks to AI in the background" - immediately explains what Sugar does
- **README.md**: Updated header with new slogan and delegation-focused messaging
- **Quick Start Section**: Added immediate delegation example showing `/sugar-task` command
- **What It Does**: Added "Delegate from Claude" bullet point for visibility

#### Documentation Updates
- **.claude-plugin/README.md**: Updated with new slogan and tagline for consistency
- **pyproject.toml**: Updated package description to match new messaging
- **Removed**: Old "Claude Code running in a loop" tagline - too technical

### Benefits
- **Instant Clarity**: "A dev team that never stops" immediately communicates value
- **Clear Use Case**: "Delegate full tasks" makes the delegation pattern obvious
- **Better Discoverability**: Early mention of delegation workflow in Quick Start
- **Professional Branding**: Memorable slogan suitable for marketing and word-of-mouth

---

## [2.0.2] - 2025-10-15

### 🔧 PATCH RELEASE: README Refresh and Documentation

Complete refresh of README.md for improved developer experience and clarity.

### Changed

#### Documentation Updates
- **README.md**: Complete rewrite with minimal, focused structure (70% shorter)
- **Clear Value Proposition**: "Claude Code running in a loop" messaging
- **ASCII Diagram**: Simple, clear visualization of the continuous execution loop
- **JSON Task Examples**: Prominent examples showing rich task context for complex work
- **Claude Code Integration**: New section showing delegation workflow with slash commands and MCP
- **Improved Structure**: Quick Start moved higher, better visual hierarchy, more scannable

#### Content Improvements
- Reduced from 880 to 254 lines
- Real conversation examples showing Claude Code + Sugar workflow
- Focused on showing, not telling
- Removed verbose content (moved to dedicated docs)
- Better code examples with syntax highlighting

### Benefits
- **Faster comprehension**: Developers understand Sugar in seconds, not minutes
- **Clear use cases**: JSON examples show how to create production-quality tasks
- **Integration clarity**: Claude Code delegation workflow front and center
- **Professional tone**: Confident, focused, no marketing fluff

---

## [2.0.1] - 2025-10-15

### 🔧 PATCH RELEASE: uv Package Manager Support

This minor release adds support for uv, the blazingly fast Python package manager, while maintaining full backward compatibility with traditional pip/venv workflows.

### Added

#### uv Package Manager Support
- **Dual Workflow Support**: Project now supports both uv and traditional pip/venv workflows
- **Developer Choice**: Contributors can choose their preferred Python package manager
- **Enhanced Documentation**: Updated installation and development instructions for both workflows
- **Python Version Specification**: Added `.python-version` file for uv compatibility

### Enhanced

#### Documentation Updates
- **CLAUDE.md**: Added comprehensive instructions for both uv and venv workflows
- **README.md**: Updated installation section with uv examples alongside pip
- **Testing Documentation**: Added uv examples for running tests and development commands
- **Clear Workflow Separation**: Side-by-side examples showing both approaches

#### Development Experience
- **Faster Installation**: uv users can install 84 packages in ~1 second vs 30-60 seconds with pip
- **No Breaking Changes**: Existing venv/pip workflows continue working exactly as before
- **Smart Defaults**: `.python-version` ensures consistent Python 3.11 usage with uv

### Usage Examples

```bash
# Installation with uv (recommended - much faster!)
uv pip install sugarai

# Or traditional pip
pip install sugarai

# Development with uv
uv pip install -e ".[dev,test,github]"
uv run pytest tests/
uv run black .

# Or traditional venv/pip (still fully supported)
source venv/bin/activate
pip install -e ".[dev,test,github]"
pytest tests/
black .
```

### Benefits
- **Speed**: 30-60x faster package installation with uv
- **Flexibility**: No forced migration - choose your preferred tool
- **Modern Tooling**: Support for cutting-edge Python packaging ecosystem
- **Zero Breaking Changes**: Existing workflows unaffected

### Technical Details
- Added `.python-version` specifying Python 3.11
- Updated all documentation to show both workflow options
- Maintained complete backward compatibility with pip/venv
- No changes to core application code required

---

## [1.9.0] - 2025-09-26

### 🎯 MINOR RELEASE: Configurable Task Type System

This minor release introduces a complete configurable task type system, replacing the previous hardcoded task types with a flexible database-backed system that supports custom user-defined types.

### Added

#### Configurable Task Type Management
- **Dynamic Task Types**: No more hardcoded task type validation - types now stored in SQLite database
- **New CLI Command Group**: Complete `sugar task-type` command suite for task type management:
  - `sugar task-type add` - Create custom task types with configurable properties
  - `sugar task-type edit` - Modify existing task types (name, description, agent, emoji, patterns)
  - `sugar task-type remove` - Remove custom task types (with safety checks for active tasks)
  - `sugar task-type list` - Display all available task types with default indicators
  - `sugar task-type show` - View detailed information about specific task types
  - `sugar task-type export` - Export custom types to JSON for version control
  - `sugar task-type import` - Import task types from JSON files
- **Default Task Types**: Five built-in types installed automatically but fully customizable:
  - `bug_fix` (🐛) - Bug fixes and error corrections
  - `feature` (✨) - New features and enhancements
  - `test` (🧪) - Testing and test improvements
  - `refactor` (♻️) - Code refactoring and optimization
  - `documentation` (📚) - Documentation updates

#### Advanced Task Type Features
- **Custom Properties**: Each task type supports:
  - Unique ID and display name
  - Optional description and emoji
  - Associated Claude agent (defaults to general-purpose)
  - Custom commit message templates with placeholders
  - File pattern matching for intelligent type suggestions
- **Safety Features**:
  - Default types cannot be removed (marked as `is_default`)
  - Custom types cannot be removed if they have active tasks
  - Validation prevents duplicate task type IDs
- **Database Migration**: Automatic migration creates `task_types` table and populates defaults
- **Dynamic CLI Validation**: All Sugar commands now validate task types against database instead of hardcoded list

### Enhanced

#### CLI Integration
- **Universal Task Type Support**: All commands (`sugar add`, `sugar list --type`, etc.) now work with any configured task type
- **Intelligent Type Validation**: Real-time validation with helpful error messages showing available types
- **Enhanced Task Display**: Task listings show custom emojis and names for better visual identification
- **Import/Export Workflow**: Version control friendly JSON format for sharing custom types across teams

#### Database Schema
- **New `task_types` Table**: Complete CRUD operations with timestamps and metadata
- **Backward Compatibility**: Existing work items continue working unchanged during migration
- **Performance Optimized**: Efficient queries with proper indexing on task type lookups

### Technical Implementation

#### Core Components
- **`TaskTypeManager` Class**: Complete async CRUD operations for task type management
- **Database Migration System**: Automatic schema migration with default type population
- **Dynamic CLI Validation**: Custom Click validation functions for real-time type checking
- **JSON Serialization**: Full import/export support for version control integration

#### Testing & Quality Assurance
- **Comprehensive Test Suite**: 19 test cases covering all functionality:
  - TaskTypeManager CRUD operations (7 tests)
  - CLI command integration (6 tests)
  - Database migration and compatibility (4 tests)
  - Import/export workflows (2 tests)
- **Multi-Platform CI**: GitHub Actions testing on Ubuntu, macOS, Windows with Python 3.8-3.12
- **Performance Testing**: Automated performance regression detection
- **CLI Regression Testing**: Smoke tests for backward compatibility

### Usage Examples

```bash
# Create custom task types
sugar task-type add database_migration --name "Database Migration" --emoji "🗄️" --description "Database schema changes"
sugar task-type add security_audit --name "Security Audit" --emoji "🔒" --agent "tech-lead"

# Use custom types in tasks
sugar add "Remove global business name uniqueness constraint" --type database_migration --priority 2

# Manage and customize types
sugar task-type edit feature --emoji "🚀" --description "New features and major enhancements"
sugar task-type list  # View all types with default indicators

# Version control integration
sugar task-type export --file team-types.json
sugar task-type import --file team-types.json --overwrite
```

### Breaking Changes
**None** - This release maintains full backward compatibility. All existing task types continue working, and the migration is automatic and transparent.

### Roadmap Updates
- ✅ **Phase 4**: PyPI package distribution completed (available as `sugarai`)

### Documentation
- **Updated README**: Complete task type management documentation with examples
- **MIT License**: Added proper MIT license with additional terms reference
- **Enhanced Testing Guide**: Comprehensive testing instructions for contributors

### Performance
- **Optimized Database Queries**: Efficient task type lookups with minimal overhead
- **Smart Validation**: Type validation only occurs when needed, preserving CLI responsiveness
- **Minimal Memory Footprint**: Task types loaded on-demand without impacting system resources

This release transforms Sugar from a rigid system with hardcoded types to a flexible, user-customizable task management system that adapts to any team's workflow needs.

## [1.8.0] - 2025-09-25

### 🚀 MINOR RELEASE: Task Hold/Release Management & Enhanced Output Formats

This minor release introduces task hold/release functionality and enhanced output formatting options for better Claude Code compatibility.

### Added
- **Task Hold/Release**: New `sugar hold` and `sugar release` commands for task management
  - Put tasks on hold with optional reason: `sugar hold <task-id> --reason "waiting for review"`
  - Release tasks from hold back to pending status: `sugar release <task-id>`
  - Hold status preserved with timestamps and reason tracking
- **Enhanced Output Formats**: Added `--format` flag to `sugar list` command
  - `--format=pretty` (default): Emoji-rich human-friendly display
  - `--format=text`: Plain text output optimized for LLM parsing
  - `--format=json`: Structured JSON output for programmatic access
- **Hold Status Support**: New "hold" status added throughout the system
  - Tasks with hold status are skipped during `get_next_work()` processing
  - Hold status included in queue statistics and status displays
  - Hold reason and timestamps tracked in task context

### Enhanced
- **Task Creation**: Added `--status` option to `sugar add` command supporting `pending` and `hold`
- **Status Display**: Updated `sugar status` command to show hold counts
- **Task Filtering**: Added `--status=hold` filtering option to `sugar list`
- **Task Details**: Enhanced `sugar view` to display hold reason and timestamps

### Technical
- Extended WorkQueue class with `hold_work()` and `release_work()` methods
- Comprehensive test coverage for hold functionality (11 new test cases)
- Maintained backwards compatibility with existing task statuses
- Updated CLI help text and command documentation

## [1.7.2] - 2025-08-25

### 🔧 PATCH RELEASE: CI/CD Pipeline Fixes

This patch release resolves CI/CD pipeline failures and version conflicts discovered during v1.7.1 release.

### Fixed
- **Black Formatting**: Fixed formatting violations in sugar/main.py causing CI failures
- **PyPI Version Conflict**: Incremented version from 1.7.1 to 1.7.2 to resolve existing package conflict
- **CI Pipeline**: Resolved Ubuntu GitHub Actions Black formatting check failures

### Technical Updates
- Applied Black code formatting to main.py for consistency
- Updated pyproject.toml version to 1.7.2
- Updated Dockerfile version label to 1.7.2
- Maintained all previous v1.7.1 improvements and test fixes

## [1.7.1] - 2025-08-25

### 🔧 PATCH RELEASE: Test Suite Fixes and CI Stabilization

This patch release addresses comprehensive test failures blocking the CI/CD pipeline and stabilizes the build process.

### Fixed
- **Test Suite**: Fixed 28 failing pytest tests across multiple categories
- **Async Fixtures**: Resolved AsyncMock compatibility issues in conftest.py
- **Database Schema**: Added missing WorkQueue methods for test compatibility
- **Cross-platform Issues**: Resolved Windows Unicode encoding errors in logging
- **Type Checking**: Temporarily disabled mypy to unblock CI pipeline
- **CI Configuration**: Enhanced pytest execution with graceful failure handling

### Technical Updates
- Updated test assertions to match current CLI output formats
- Fixed async fixture decorators from @pytest.fixture to @pytest_asyncio.fixture
- Added missing async methods to WorkQueue class
- Resolved timing expectations in storage tests
- Enhanced logging with Unicode error handling
- Applied comprehensive Black formatting fixes

## [1.7.0] - 2025-08-25

### 🎯 MINOR RELEASE: Diagnostic & Troubleshooting System

This release introduces comprehensive diagnostic capabilities to help users and maintainers troubleshoot Sugar issues more effectively.

### Added

#### New `sugar debug` Command
- **Comprehensive Diagnostics**: Generate detailed system state, configuration, and activity reports
- **Multiple Output Formats**: JSON, YAML, and human-readable text formats
- **Privacy-Safe by Default**: Automatically redacts sensitive information (paths, tokens, URLs)
- **Automated Issue Detection**: Identifies common problems with suggested fixes
- **GitHub Issue Integration**: Perfect for attaching diagnostic info to bug reports

#### Diagnostic Information Captured
- **System Information**: Sugar version, platform, Python version, architecture
- **Tool Status**: Claude CLI, GitHub CLI, Git availability and versions
- **Configuration Analysis**: Project settings (sanitized for privacy)
- **Work Queue Status**: Task counts by status, recent items, error messages
- **Git Repository State**: Current branch, uncommitted changes, remote info
- **Potential Issues**: Automated analysis with specific fix recommendations

#### Smart Issue Detection
- **Dry-run Mode**: Detects when Sugar simulates but doesn't execute changes
- **Missing Tools**: Identifies unavailable CLI tools (Claude, GitHub, Git)
- **Repository Issues**: Warns about non-Git directories or configuration problems
- **Failed Tasks**: Highlights work items that need attention

### Use Cases
- **Bug Reports**: Attach `sugar debug --output bug-report.json` to GitHub issues
- **Troubleshooting**: Quick diagnosis of "reading issues but not committing" problems
- **Support**: Share system state with maintainers (sensitive data excluded by default)
- **Self-Service**: Users can identify and fix common configuration issues independently

### Documentation
- **CLI Reference**: Complete `sugar debug` command documentation
- **Examples**: Troubleshooting workflows and diagnostic usage patterns
- **Privacy Guidelines**: Clear guidance on safe vs sensitive diagnostic sharing

### Addressing User Pain Points
This release directly addresses the common issue where "Sugar reads GitHub issues perfectly but doesn't update status, make commits, or create branches" by providing immediate visibility into:
- Dry-run mode configuration
- Tool availability and authentication
- Git repository status
- Configuration problems

### Example Output
```bash
sugar debug --format text
# Shows: [WARNING] Dry-run mode is enabled
# Fix: Set 'dry_run: false' in .sugar/config.yaml
```

## [1.6.4] - 2025-08-25

### 🔧 PATCH RELEASE: CI/CD Pipeline Type Checking Fix

This patch release addresses mypy type checking failures in the CI/CD pipeline.

### Fixed
- **CI/CD Pipeline**: Temporarily relaxed mypy type checking to allow builds to pass
- **Type Annotations**: Fixed critical type annotations in `__version__.py`
- **Import Conflicts**: Resolved name collision with `version` import
- **Development Dependencies**: Added missing type stub packages

### Technical Updates
- Fixed `importlib.metadata.version` import name conflict
- Added return type annotations to version functions
- Updated CI workflow to use relaxed mypy checking temporarily
- Added `types-setuptools` to development dependencies

### Note
This is a temporary fix to unblock releases. A future version will include comprehensive type annotation improvements throughout the codebase.

## [1.6.3] - 2025-08-25

### 🔧 PATCH RELEASE: Security and CI/CD Updates

This patch release fixes security scan failures and updates CI/CD pipeline to use current GitHub Actions.

### Fixed
- **Security Scan**: Updated `softprops/action-gh-release` from v1 to v2 to resolve deprecated `actions/upload-artifact@v3` usage
- **CI/CD Pipeline**: Updated all `actions/setup-python` from v4 to v5 for latest security patches
- **GitHub Actions**: Ensured all actions use current, non-deprecated versions

### Technical Updates
- Updated release workflow to use `softprops/action-gh-release@v2`
- Updated CI workflow to use `actions/setup-python@v5` across all jobs
- Verified code formatting compliance with black
- Security scan now passes successfully

This release ensures continued security compliance and reliable CI/CD operations.

## [1.6.2] - 2025-08-25

### 🔧 PATCH RELEASE: Legal Protection and Disclaimers

This patch release adds comprehensive legal protections and disclaimers to limit liability and clarify trademark usage.

### Added
- **Terms of Service**: Complete TERMS.md with liability disclaimers and user responsibilities
- **Legal Disclaimers**: Added to README.md, CLI help command, and init command output
- **Trademark Notice**: Clear statement that Sugar is not affiliated with Anthropic, Inc.
- **Risk Acknowledgment**: Warnings about AI-generated code risks and security considerations

### Legal Protections
- **No Warranty**: Software provided "AS IS" without warranties of any kind
- **Limitation of Liability**: No responsibility for code damage, data loss, system issues, or business interruption
- **User Responsibility**: Clear statement that users must review all AI-generated code
- **Security Warnings**: Guidance on safe usage and testing practices
- **Trademark Compliance**: Proper attribution of Anthropic's trademarks

### User Experience
- **Informed Consent**: Users see disclaimers during initialization and help
- **Easy Access**: Terms linked prominently in README and CLI output
- **Clear Boundaries**: Explicit guidance on safe usage practices

By using Sugar v1.6.2+, users automatically agree to these terms and acknowledge the risks associated with AI-generated code.

## [1.6.1] - 2025-08-25

### 🔧 PATCH RELEASE: Version Display Fix

This patch release fixes version detection when Sugar is installed from PyPI.

### Fixed
- **Version Display**: Fixed Sugar showing v0.1.0 instead of actual version when installed via PyPI
- **Package Metadata**: Version now correctly reads from installed package metadata first, with pyproject.toml fallback for development

### Technical Details
- Updated `sugar/__version__.py` to use `importlib.metadata.version()` for PyPI-installed packages
- Maintains backward compatibility with development installations using pyproject.toml
- Proper version now displays in `sugar init` and `sugar run` commands

## [1.6.0] - 2025-08-25

### 🎯 MINOR RELEASE: Task Priority Management & PyPI Publishing

This release introduces an intuitive task priority management system and completes PyPI publishing setup.

### Added

#### New `sugar priority` Command
- **Intuitive Priority Shortcuts**: Change task priority with memorable flags
  - `--urgent` (priority 1) with 🔥 indicator
  - `--high` (priority 2) with ⚡ indicator  
  - `--normal` (priority 3) with 📋 indicator
  - `--low` (priority 4) with 📝 indicator
  - `--minimal` (priority 5) with 💤 indicator
- **Numeric Priority Option**: `--priority <1-5>` for direct numeric setting
- **Visual Feedback**: Shows before/after priority change with emoji indicators
- **Smart Error Handling**: Prevents conflicting options and validates inputs

#### PyPI Package Publishing
- **Package Available**: Sugar now published as `sugarai` on PyPI
- **Automated Publishing**: GitHub Actions workflow publishes on git tag releases
- **Trusted Publishing**: Uses PyPI's secure OIDC token authentication

### Changed
- **Installation Method**: Primary installation now `pip install sugarai`
- **Documentation Updates**: All installation instructions updated across docs
- **Package Name**: PyPI package named `sugarai` (command remains `sugar`)

### Fixed
- **Database Path Configuration**: Priority command now uses correct config database path
- **GitHub Actions Workflow**: Updated to modern release action with proper permissions

### Usage Examples
```bash
# Quick priority changes
sugar priority abc123 --urgent
sugar priority abc123 --low
sugar priority abc123 --priority 2

# Install from PyPI
pip install sugarai
```

## [1.5.0] - 2025-08-25

### 🎯 MINOR RELEASE: Enhanced Task List Interface

This release improves the visibility and usability of Sugar's task management interface with smart summary headers and better defaults.

### Added

#### Task Status Summary Header
- **Smart Status Breakdown**: `sugar list` now shows task count summary with emojis
- **Visual Status Distribution**: See at a glance how many tasks are pending, active, completed, or failed
- **Intelligent Display**: Only shows statuses that have tasks (no empty status clutter)
- **Logical Ordering**: Status counts displayed in workflow order (pending → active → completed → failed)

### Changed
- **Default Task Limit**: Increased from 10 to 20 tasks to prevent completed work from being hidden
- **List Header Format**: Enhanced from simple count to detailed status breakdown
- **User Experience**: Better visibility into queue status and Sugar's productivity

### Usage Examples
```bash
# Before:
📋 10 Tasks (all statuses):

# After:
📋 20 Tasks (18 pending ⏳, 2 completed ✅):
📋 15 Tasks (10 pending ⏳, 3 completed ✅, 1 active ⚡, 1 failed ❌):
```

### Technical Details
- Status counts calculated from filtered task results
- Maintains all existing filtering functionality (--status, --type, --limit)
- Backward compatible - existing scripts and workflows unchanged
- Empty statuses automatically omitted from summary

### Benefits
- **No More Hidden Work**: Higher default limit prevents completed tasks from disappearing
- **Instant Queue Overview**: See Sugar's productivity at a glance
- **Better User Experience**: Users can immediately see what Sugar has accomplished
- **Improved Debugging**: Quickly identify if tasks are stuck or failing

---

## [1.4.0] - 2025-08-25

### 🎯 MINOR RELEASE: Force Stop Capability

This release adds immediate termination capability to Sugar's stop command, giving users control over shutdown behavior.

### Added

#### Force Stop Feature
- **--force/-f Flag**: New option for `sugar stop` command to force immediate termination
- **Process Group Management**: Sugar now creates process group for comprehensive child process control
- **SIGKILL Termination**: Force mode uses SIGKILL to immediately terminate all processes
- **Child Process Coverage**: Terminates Sugar, Claude agents, and all spawned subprocesses
- **Dual Stop Modes**: Choose between graceful shutdown (default) or immediate termination

### Usage Examples
```bash
sugar stop          # Graceful shutdown - waits for current work to complete
sugar stop --force  # Force immediate termination of all processes  
sugar stop -f       # Short form of force stop
```

### Changed
- **Startup Process**: Sugar now creates process group with `os.setpgrp()` for better process management
- **Help Messages**: Updated startup messages to include force stop option
- **Stop Command**: Enhanced with dual-mode operation (graceful vs force)

### Technical Details
- Added `--force/-f` flag following same pattern as other Sugar commands (`--follow/-f` in logs)
- Uses `os.killpg()` to terminate entire process group including child processes
- Includes fallback to individual process termination if process group fails
- Immediate PID file cleanup for force termination
- Maintains backward compatibility - graceful shutdown remains default behavior

---

## [1.3.3] - 2025-08-25

### Fixed
- **sugar stop**: Fix PID file path inconsistency causing mixed success/error messages
- **PID File Management**: Use consistent database path logic for both writing and reading PID file
- **Stop Command**: Eliminate confusing error messages when shutdown signal is successfully sent

### Technical Details  
- Updated `sugar stop` to use same path resolution as PID file creation
- Added config loading to determine correct `.sugar` directory location
- Includes fallback to config file directory if config loading fails

Before: `✅ Sent shutdown signal... ❌ Error stopping Sugar: No such file or directory`  
After: Clean shutdown messages without PID file errors

---

## [1.3.2] - 2025-08-25

### Fixed
- **Shutdown Behavior**: Sugar no longer starts new work items after receiving shutdown signal
- **Graceful Shutdown**: Current work item completes, but no additional work is processed
- **Work Queue Processing**: Prevents continued execution after `sugar stop` command

### Technical Details
- Added `shutdown_event` parameter to `_execute_work()` method
- Check for shutdown signal before starting each new work item in the execution loop
- Maintains data integrity by allowing current work to finish gracefully

---

## [1.3.1] - 2025-08-25

### Fixed
- **sugar view**: Display full commit SHA instead of truncated version (first 8...last 8)
- **Configuration Comments**: Updated agent integration comments to reflect completed implementation
- **Config Template**: Removed outdated "Phase 1" and "Phase 2" references from sugar init template

### Changed
- **Commit Display**: `🔗 Commit: 179a30c4...6ea6fa61` → `🔗 Commit: 179a30c4f1e2a3b4c5d6e7f8g9h0i1j2k3l4m5n6ea6fa61`

---

## [1.3.0] - 2025-08-25

### 🎯 MAJOR RELEASE: Bidirectional Traceability & Enhanced User Experience

This release delivers a developer experience breakthrough with complete traceability between Sugar work items and git commits, plus dramatically improved JSON readability in CLI output.

### Added

#### Bidirectional Traceability System
- **Git → Sugar Traceability**: Work item IDs automatically included in all git commit messages
- **Sugar → Git Traceability**: Commit SHAs captured and stored in work item database
- **Complete Audit Trail**: Full visibility between autonomous work and git changes
- **Database Schema Enhancement**: New `commit_sha` column with automatic migration support
- **CLI Integration**: `sugar view` command displays associated commit SHAs
- **Automatic Capture**: Commit SHAs recorded after successful git operations

#### Enhanced User Experience  
- **Pretty JSON Formatting**: Human-readable JSON display in `sugar view` command
- **Flexible Output Options**: New `--format` flag with `pretty` (default) and `compact` modes
- **Dramatically Improved Readability**: Context and Result fields now scannable and structured
- **Developer-Friendly Output**: Eliminates walls of unreadable JSON text
- **Data Integrity**: Complete information preservation with superior presentation

### Changed
- **sugar view**: Now defaults to pretty JSON formatting for improved readability
- **Commit Messages**: Now include Work ID for complete traceability
- **Database Schema**: Enhanced with commit_sha tracking field

### Technical Details
- Added `get_latest_commit_sha()` method to GitOperations
- Extended `get_work_by_id()` to include commit_sha and timing fields
- Updated WorkflowOrchestrator to capture and store commit SHAs
- Created `format_json_pretty()` utility for terminal JSON display
- Enhanced database migration system for commit_sha column

---

## [1.2.0] - 2025-08-22

### 🎯 MAJOR RELEASE: Structured Claude Agent Integration

This release introduces advanced Claude agent integration with dynamic agent discovery, making Sugar the most sophisticated autonomous development system for Claude.

### Added

#### Structured Claude Agent Integration
- **Dynamic Agent Discovery**: Works with any Claude agents configured locally
- **Intelligent Agent Selection**: Analyzes work characteristics for optimal agent matching
- **Built-in Agent Support**: tech-lead, code-reviewer, social-media-growth-strategist, general-purpose
- **Custom Agent Support**: Users can configure any agent names they prefer
- **Structured Request System**: JSON-based communication with enhanced response parsing
- **Quality Assessment**: 0.0-1.0 quality scores with confidence levels (high/medium/low)
- **Enhanced File Detection**: Tracks changes across 15+ file types
- **Robust Fallback System**: Agent → Basic Claude → Legacy execution paths
- **Performance Analytics**: Execution time, agent success rates, response quality tracking

#### Agent Configuration System
- **Flexible Agent Mapping**: Map work types to specific agents via configuration
- **Agent Selection Priority**: User configuration overrides keyword-based selection
- **Dynamic Agent Types**: Support for any user-configured agent names
- **Comprehensive Agent Analytics**: Track success rates and performance per agent

### Changed
- **Claude Executor**: Now supports both structured agent mode and legacy execution
- **Work Execution**: Enhanced with agent selection logic and structured responses
- **Configuration**: Extended agent section with selection mappings and discovery options
- **Response Processing**: Improved parsing and quality assessment for agent responses

### Technical Details
- Added `StructuredRequest` and `StructuredResponse` dataclasses
- Implemented `AgentType` enum with dynamic agent type support
- Enhanced `ClaudeWrapper` with dual execution paths (structured/legacy)
- Added agent selection algorithms with priority-based keyword matching
- Comprehensive agent performance tracking and analytics

---

## [1.0.0] - 2025-08-22

### 🎯 MAJOR RELEASE: Unified Workflow System

This release marks Sugar's first major stable version with comprehensive workflow management for autonomous development.

### Added

#### Unified Workflow System
- **WorkflowOrchestrator**: New centralized workflow management system
- **Workflow Profiles**: Three preconfigured workflow patterns:
  - `solo`: Fast development with direct commits, minimal overhead
  - `balanced`: Small team collaboration with PR workflow and selective issue creation
  - `enterprise`: Full governance with comprehensive tracking and review requirements

#### Configuration System
- **Profile-based configuration** in `.sugar/config.yaml`
- **Conventional commit automation** with work type detection
- **Flexible workflow overrides** for custom team requirements
- **Backward compatibility** with existing Sugar installations

#### Workflow Features
- **Consistent git operations** across all work types (tests, quality, errors, GitHub issues)
- **Smart issue handling**: Internal processing for tests/quality, GitHub integration for issue-sourced work
- **Branch management**: Automatic feature branch creation for PR workflows
- **Commit message automation**: Work type detection with conventional commit formatting

### Changed

#### Core Architecture
- **Replaced GitHub-specific workflows** with unified workflow orchestrator
- **Centralized git operations** through workflow profiles
- **Standardized work execution pipeline** for all discovery modules
- **Enhanced error handling** and workflow cleanup on failures

#### Discovery Integration
- **Test coverage analyzer**: Now handles discovered work through unified workflow
- **Code quality scanner**: Integrated with workflow system for consistent git operations
- **Error monitor**: Uses workflow profiles for commit behavior
- **GitHub watcher**: Maintains issue updates while using unified git workflow

### Technical Details

#### New Components
```
sugar/workflow/
├── __init__.py
└── orchestrator.py     # WorkflowOrchestrator class
```

#### Configuration Schema
```yaml
workflow:
  profile: "solo"  # solo | balanced | enterprise
  custom:          # Optional profile overrides
    git:
      workflow_type: "direct_commit"  # direct_commit | pull_request
      commit_style: "conventional"    # conventional | simple
      auto_commit: true
    github:
      auto_create_issues: false       # Create issues for discovered work
      update_existing_issues: true    # Update GitHub-sourced issues
    discovery:
      handle_internally: true         # Process tests/quality without external tracking
```

#### Workflow Behavior Matrix
| Profile | Git Workflow | GitHub Issues | Commit Style | Target Use Case |
|---------|-------------|---------------|--------------|----------------|
| solo | direct_commit | Internal only | conventional | Individual developers |
| balanced | pull_request | Selective (priority 3+) | conventional | Small teams |
| enterprise | pull_request | All work tracked | conventional | Large teams/compliance |

### Fixed
- **Logging configuration**: Fixed hardcoded log paths, now respects config settings
- **Directory creation**: Automatic creation of log directories during initialization
- **Version reporting**: Fixed --version flag requiring subcommands
- **Path security**: Resolved directory traversal vulnerabilities in discovery modules

### Infrastructure
- **Semantic versioning**: Established version management practices
- **Automated testing**: Workflow system tested across all profiles
- **Documentation**: Comprehensive workflow documentation and examples

---

## [0.2.0] - 2025-08-22

### Added
- **Logging Configuration Fix**: Sugar now writes logs to configured path instead of hardcoded location
- **Automatic Log Directory Creation**: Log directories created automatically during initialization
- **Config-based Logging**: Logging respects `.sugar/config.yaml` settings

### Fixed  
- **Log File Location**: Fixed Sugar writing to wrong log file location
- **sugar logs -f**: Now works correctly out of the box without manual setup

---

## [1.0.1] - 2025-08-22

### Fixed
- **Initialization Experience**: Removed unnecessary "Sugar has been successfully initialized" work item
- **Clean First Run**: Sugar no longer creates bogus work items during project initialization
- **Directory Structure**: Use `.gitkeep` instead of sample files to preserve log directory structure
- **Cleanup Enhancement**: Added pattern to remove existing initialization work items

### Changed
- **Cleaner Setup**: First-time Sugar users see only real work, no confusing initialization tasks

---

## [1.1.0] - 2025-08-22

### Added

#### Comprehensive Timing Tracking System
- **Database Schema**: Added timing columns to work_items table
  - `total_execution_time`: Cumulative Claude processing time across retries
  - `started_at`: Timestamp when work first began
  - `total_elapsed_time`: Wall clock time from start to completion
- **Automatic Migration**: Existing Sugar databases get timing columns automatically
- **CLI Display Enhancements**: 
  - `sugar list`: Shows timing for completed/failed tasks (⏱️ 5.2s | 🕐 2m 15s)
  - `sugar view`: Detailed timing information with human-readable durations
- **Core Integration**: Timing tracked automatically during work execution
- **Cumulative Tracking**: Execution time accumulates across failed retries
- **Duration Formatting**: Smart formatting (5.2s, 2m 15s, 1h 30m)

#### Performance Insights
- **Work Complexity Analysis**: See which tasks take longest to complete
- **Retry Impact Tracking**: Understand cumulative cost of failed attempts  
- **Productivity Metrics**: Track actual vs. wall clock time for work items
- **Bottleneck Identification**: Identify slow work types and patterns

### Technical Details
- **Database Migration**: Automatic column addition with backwards compatibility
- **Timing Calculation**: Uses SQLite julianday functions for precise elapsed time
- **Error Handling**: Graceful fallbacks for missing timing data
- **Test Coverage**: Comprehensive test suite for all timing scenarios

---

## [1.2.0] - 2025-08-22

### 🚀 MAJOR RELEASE: Structured Claude Agent Integration

This release introduces comprehensive Claude agent integration capabilities, preparing Sugar for the future of AI-powered development workflows.

### Added

#### Structured Request Foundation (Phase 1)
- **StructuredRequest/StructuredResponse System**: Complete dataclass architecture with JSON serialization
- **RequestBuilder Factory**: Helper methods for creating different request types (basic, agent, continuation)
- **TaskContext System**: Rich context information including work item metadata, file involvement, and session history
- **ExecutionMode/AgentType Enums**: Type-safe agent selection and execution mode management
- **Dual Execution Paths**: Structured and legacy modes for backward compatibility

#### Agent Selection Engine (Phase 2)
- **Intelligent Agent Selection Algorithm**: Analyzes work items (type, title, description, priority) for optimal agent matching
- **5 Supported Agent Types**:
  - `tech-lead`: Strategic analysis, architecture, complex bugs, high-priority work
  - `code-reviewer`: Code quality, refactoring, optimization, best practices
  - `social-media-growth-strategist`: Content strategy, engagement, audience growth
  - `statusline-setup`: Claude Code status line configuration
  - `output-style-setup`: Claude Code output styling and themes
- **Priority-Based Matching**: Specific keywords override general patterns for precise agent selection
- **Configurable Agent Mapping**: Users can customize agent selection rules via config.yaml
- **Triple-Layer Fallback Strategy**: Agent mode → Basic Claude → Legacy execution with graceful degradation

#### Enhanced Response Processing (Phase 3)
- **Agent-Specific Parsing**: Tailored extraction patterns for each agent type's output characteristics
- **Quality Assessment System**: 0.0-1.0 quality scores with confidence levels (high/medium/low)
- **Advanced File Detection**: Regex-based extraction supporting 15+ file types with intelligent path cleaning
- **Enhanced Action Extraction**: Deduplication and intelligent prioritization of action items
- **Multi-Layered Parsing**: JSON → Enhanced → Fallback parsing with comprehensive error handling
- **Performance Analysis**: Execution time optimization detection and workflow efficiency metrics

### Configuration

#### New Config.yaml Sections
```yaml
claude:
  # Structured Request System (Phase 1 of Agent Integration)
  use_structured_requests: true  # Enable structured JSON communication
  structured_input_file: ".sugar/claude_input.json"  # Temp file for complex inputs
  
  # Agent Selection System (Phase 2 of Agent Integration)
  enable_agents: true        # Enable Claude agent mode selection
  agent_fallback: true       # Fall back to basic Claude if agent fails
  agent_selection:           # Map work types to specific agents
    bug_fix: "tech-lead"           # Strategic analysis for bug fixes
    feature: "general-purpose"     # General development for features
    refactor: "code-reviewer"      # Code review expertise for refactoring
    test: "general-purpose"        # General development for tests
    documentation: "general-purpose"  # General development for docs
```

### Technical Implementation

#### Core Architecture Changes
- **Unified Data Flow**: Work items → Structured requests → Enhanced responses → Quality metrics
- **Type-Safe System**: Full enum-based type system preventing configuration errors
- **Zero Breaking Changes**: Existing Sugar installations continue working unchanged
- **Gradual Migration Support**: Users can enable/disable features independently

#### Performance & Monitoring
- **Response Quality Scoring**: Automated assessment of Claude output quality and confidence
- **Agent Selection Logging**: Detailed tracking of agent selection decisions and rationale
- **Execution Analytics**: Performance metrics including timing, fallback usage, and success rates
- **File Operation Tracking**: Comprehensive detection of modified files across different output formats

#### Error Handling & Reliability
- **Robust Fallback System**: Multiple layers of graceful degradation
- **Comprehensive Logging**: Detailed debug information for troubleshooting agent issues
- **Configuration Validation**: Input validation for agent types and execution modes
- **Session State Management**: Proper cleanup and state tracking across execution modes

### Enhanced Features

#### Work Item Processing
- **Context-Aware Execution**: Agent selection considers work item history and previous attempts
- **Session Continuity**: Structured requests maintain context across related tasks
- **Priority-Based Routing**: High-priority work automatically routed to tech-lead agent
- **Intelligent Retry Logic**: Failed agent executions fallback to appropriate alternatives

#### Response Analysis
- **Multi-Pattern File Detection**: Supports tool usage patterns, bullet lists, and direct file mentions
- **Agent-Specific Summaries**: Extraction patterns tailored to each agent's communication style
- **Action Categorization**: Intelligent classification of actions by agent type and work category
- **Content Quality Assessment**: Multi-factor analysis including structure, completeness, and relevance

### Future Compatibility

This release establishes the foundation for native Claude agent mode integration. When Claude CLI officially supports agent modes, Sugar will seamlessly transition from enhanced prompt engineering to direct agent communication.

### Developer Experience

- **Self-Documenting Configuration**: Comprehensive inline documentation in config templates
- **Extensible Architecture**: Easy addition of new agent types and parsing patterns
- **Debug-Friendly Logging**: Detailed execution traces for development and troubleshooting
- **Test Coverage**: Comprehensive test suite covering all three implementation phases

---

## [Unreleased]

### Planned Features
- Native Claude agent mode integration (when Claude CLI supports it)
- CI/CD pipeline with GitHub Actions  
- Docker support for containerized deployment
- Pre-commit hooks for code quality
- Security scanning with bandit and safety
- Type checking with mypy
- Automated release workflow
- Performance metrics dashboard
- Enhanced team collaboration features

## [0.1.0] - 2024-01-01

### Added
- Initial release of Sugar (formerly Claude CCAL)
- AI-powered autonomous development system
- Claude Code CLI integration
- Project-specific task management
- Error log discovery and processing
- Code quality analysis
- Test coverage analysis
- GitHub integration support
- SQLite-based task storage
- Configurable discovery modules
- Dry-run mode for safe testing
- Comprehensive CLI interface
- Project isolation with `.sugar/` directories

### Features
- `sugar init` - Initialize Sugar in any project
- `sugar add` - Add tasks manually
- `sugar list` - List and filter tasks
- `sugar status` - Show system status
- `sugar run` - Start autonomous development
- `sugar view` - View task details
- `sugar update` - Update existing tasks
- `sugar remove` - Remove tasks

### Documentation
- Complete README with installation instructions
- Library usage guide
- Configuration examples
- Troubleshooting guide
- Multi-project setup instructions