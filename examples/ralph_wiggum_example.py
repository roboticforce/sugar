#!/usr/bin/env python3
"""
Ralph Wiggum Integration Example

Demonstrates how to use Sugar's Ralph Wiggum integration for
iterative task execution with self-correction.
"""

import asyncio
from sugar.ralph import (
    CompletionCriteriaValidator,
    RalphWiggumProfile,
    RalphConfig,
    ValidationResult,
)


def example_validation():
    """Example: Validating prompts before Ralph execution"""
    print("=" * 60)
    print("Example 1: Completion Criteria Validation")
    print("=" * 60)

    validator = CompletionCriteriaValidator(strict=True)

    # Valid prompt with promise tag
    valid_prompt = """
    Fix the authentication timeout bug.

    When complete:
    - All auth tests pass
    - No timeout errors in logs
    - Output: <promise>AUTH FIXED</promise>
    """

    result = validator.validate(valid_prompt)
    print(f"\nValid prompt result:")
    print(f"  is_valid: {result.is_valid}")
    print(f"  completion_type: {result.completion_type}")
    print(f"  promise_text: {result.promise_text}")
    print(f"  success_criteria: {result.success_criteria}")

    # Invalid prompt without completion criteria
    invalid_prompt = "Just fix the bug somehow"

    result = validator.validate(invalid_prompt)
    print(f"\nInvalid prompt result:")
    print(f"  is_valid: {result.is_valid}")
    print(f"  errors: {result.errors}")
    print(f"\nSuggestions to fix:")
    for suggestion in result.suggestions:
        print(f"  - {suggestion}")


async def example_profile_usage():
    """Example: Using RalphWiggumProfile for iterative execution"""
    print("\n" + "=" * 60)
    print("Example 2: RalphWiggumProfile Usage")
    print("=" * 60)

    # Create profile with custom config
    config = RalphConfig(
        max_iterations=5,
        completion_promise="TASK COMPLETE",
        require_completion_criteria=True,
        min_confidence=0.8,
    )
    profile = RalphWiggumProfile(ralph_config=config)

    print(f"\nProfile config:")
    print(f"  max_iterations: {config.max_iterations}")
    print(f"  completion_promise: {config.completion_promise}")

    # Process input (validates and prepares prompt)
    input_data = {
        "prompt": """
        Implement rate limiting for the API.

        Requirements:
        - 100 requests/minute per IP
        - Redis-backed storage
        - 429 response when exceeded

        When complete:
        - All rate limit tests pass
        - Integration test verifies Redis
        - Output: <promise>TASK COMPLETE</promise>
        """,
        "config": {"max_iterations": 10},
    }

    result = await profile.process_input(input_data)
    print(f"\nInput processing result:")
    print(f"  valid: {result['valid']}")
    print(f"  completion_promise: {result.get('completion_promise')}")
    print(f"  max_iterations: {result.get('max_iterations')}")
    print(f"  success_criteria: {result.get('success_criteria')}")

    # Simulate iteration outputs
    print("\nSimulating iterations:")

    # Iteration 1: Still working
    output1 = await profile.process_output({
        "content": "Started implementing rate limiter. Created RateLimiter class.",
        "iteration": 0,
        "files_changed": ["src/rate_limiter.py"],
    })
    print(f"  Iteration 1: complete={output1['complete']}, summary={output1['summary'][:50]}...")

    # Iteration 2: Still working
    output2 = await profile.process_output({
        "content": "Added Redis backend and tests. Some tests failing.",
        "iteration": 1,
        "files_changed": ["src/rate_limiter.py", "tests/test_rate_limiter.py"],
    })
    print(f"  Iteration 2: complete={output2['complete']}, summary={output2['summary'][:50]}...")

    # Iteration 3: Complete!
    output3 = await profile.process_output({
        "content": "All tests passing! Rate limiting fully implemented. <promise>TASK COMPLETE</promise>",
        "iteration": 2,
        "files_changed": ["src/rate_limiter.py", "tests/test_rate_limiter.py", "docs/api.md"],
    })
    print(f"  Iteration 3: complete={output3['complete']}, promise={output3['promise_text']}")

    # Check statistics
    stats = profile.get_iteration_stats()
    print(f"\nFinal statistics:")
    print(f"  total_iterations: {stats['total_iterations']}")
    print(f"  is_complete: {stats['is_complete']}")
    print(f"  completion_reason: {stats['completion_reason']}")


def example_prompt_patterns():
    """Example: Different prompt patterns that pass validation"""
    print("\n" + "=" * 60)
    print("Example 3: Valid Prompt Patterns")
    print("=" * 60)

    validator = CompletionCriteriaValidator(strict=True)

    patterns = [
        # Pattern 1: Promise tag
        ("Promise tag", "Fix the bug. <promise>DONE</promise>"),

        # Pattern 2: Max iterations flag
        ("Max iterations", "Fix the bug --max-iterations 10"),

        # Pattern 3: When complete section
        ("When complete section", """
            Implement feature.

            When complete:
            - Tests pass
            - Docs updated
            - Output: <promise>DONE</promise>
        """),

        # Pattern 4: Config-provided iterations
        ("Config iterations", "Fix the bug"),
    ]

    for name, prompt in patterns:
        # For the last one, provide max_iterations via config
        config = {"max_iterations": 10} if name == "Config iterations" else {}
        result = validator.validate(prompt, config)
        print(f"\n{name}:")
        print(f"  is_valid: {result.is_valid}")
        print(f"  completion_type: {result.completion_type}")


async def example_stuck_detection():
    """Example: Stuck detection stops infinite loops"""
    print("\n" + "=" * 60)
    print("Example 4: Stuck Detection")
    print("=" * 60)

    profile = RalphWiggumProfile()

    # Simulate getting stuck
    output = await profile.process_output({
        "content": "I cannot proceed without database credentials. Need human intervention.",
        "iteration": 3,
    })

    print(f"\nStuck detection result:")
    print(f"  stuck: {output['stuck']}")
    print(f"  complete: {output['complete']}")
    print(f"  should_continue: {profile.should_continue()}")
    print(f"  completion_reason: {profile._completion_reason}")


def example_config_validation():
    """Example: RalphConfig validation"""
    print("\n" + "=" * 60)
    print("Example 5: Config Validation")
    print("=" * 60)

    # Valid config
    valid_config = RalphConfig(max_iterations=10, min_confidence=0.8)
    errors = valid_config.validate()
    print(f"\nValid config errors: {errors}")

    # Invalid config
    invalid_config = RalphConfig(
        max_iterations=0,       # Invalid: must be >= 1
        iteration_timeout=10,   # Invalid: must be >= 30
        min_confidence=1.5,     # Invalid: must be 0.0-1.0
    )
    errors = invalid_config.validate()
    print(f"\nInvalid config errors:")
    for error in errors:
        print(f"  - {error}")


async def main():
    """Run all examples"""
    print("\n" + "#" * 60)
    print("# Ralph Wiggum Integration Examples")
    print("#" * 60)

    example_validation()
    await example_profile_usage()
    example_prompt_patterns()
    await example_stuck_detection()
    example_config_validation()

    print("\n" + "=" * 60)
    print("Examples complete!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
