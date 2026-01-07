"""
Tests for Ralph Wiggum integration module

Tests completion criteria validation and iterative execution profile.
"""

import pytest
import pytest_asyncio
from typing import Dict, Any

from sugar.ralph import (
    CompletionCriteriaValidator,
    ValidationResult,
    RalphWiggumProfile,
    RalphConfig,
    CompletionSignal,
    CompletionType,
    CompletionSignalDetector,
    detect_completion,
    has_completion_signal,
    extract_signal_text,
)


class TestRalphConfig:
    """Tests for RalphConfig dataclass"""

    def test_config_defaults(self):
        config = RalphConfig()
        assert config.max_iterations == 10
        assert config.completion_promise == "DONE"
        assert config.require_completion_criteria is True
        assert config.min_confidence == 0.8
        assert config.iteration_timeout == 300
        assert config.quality_gates_enabled is True

    def test_config_custom(self):
        config = RalphConfig(
            max_iterations=20,
            completion_promise="TASK COMPLETE",
            require_completion_criteria=False,
            min_confidence=0.9,
        )
        assert config.max_iterations == 20
        assert config.completion_promise == "TASK COMPLETE"
        assert config.require_completion_criteria is False
        assert config.min_confidence == 0.9

    def test_config_validation_valid(self):
        config = RalphConfig()
        errors = config.validate()
        assert errors == []

    def test_config_validation_invalid_iterations(self):
        config = RalphConfig(max_iterations=0)
        errors = config.validate()
        assert any("max_iterations must be at least 1" in e for e in errors)

    def test_config_validation_excessive_iterations(self):
        config = RalphConfig(max_iterations=500)
        errors = config.validate()
        assert any("should not exceed 100" in e for e in errors)

    def test_config_validation_invalid_timeout(self):
        config = RalphConfig(iteration_timeout=10)
        errors = config.validate()
        assert any("iteration_timeout must be at least 30" in e for e in errors)

    def test_config_validation_invalid_confidence(self):
        config = RalphConfig(min_confidence=1.5)
        errors = config.validate()
        assert any("min_confidence must be between" in e for e in errors)


class TestCompletionCriteriaValidator:
    """Tests for CompletionCriteriaValidator"""

    @pytest.fixture
    def validator(self):
        return CompletionCriteriaValidator(strict=True)

    @pytest.fixture
    def lenient_validator(self):
        return CompletionCriteriaValidator(strict=False)

    def test_empty_prompt_invalid(self, validator):
        result = validator.validate("")
        assert result.is_valid is False
        assert any("empty" in e.lower() for e in result.errors)

    def test_prompt_with_promise_tag_valid(self, validator):
        prompt = "Fix the bug. Output: <promise>DONE</promise>"
        result = validator.validate(prompt)
        assert result.is_valid is True
        assert result.completion_type == "promise"
        assert result.promise_text == "DONE"

    def test_prompt_with_multiline_promise(self, validator):
        prompt = """
        Complete the task.

        When done, output:
        <promise>TASK COMPLETE</promise>
        """
        result = validator.validate(prompt)
        assert result.is_valid is True
        assert result.promise_text == "TASK COMPLETE"

    def test_prompt_with_max_iterations_config(self, validator):
        prompt = "Fix the bug without explicit completion criteria"
        result = validator.validate(prompt, {"max_iterations": 10})
        assert result.is_valid is True
        assert result.completion_type == "max_iterations"
        assert result.max_iterations == 10

    def test_prompt_with_max_iterations_in_text(self, validator):
        prompt = "Fix the bug --max-iterations 15"
        result = validator.validate(prompt)
        assert result.is_valid is True
        assert result.max_iterations == 15

    def test_prompt_with_when_complete_section(self, validator):
        prompt = """
        Implement the feature.

        When complete:
        - All tests pass
        - Documentation updated
        - Output: <promise>DONE</promise>
        """
        result = validator.validate(prompt)
        assert result.is_valid is True
        assert len(result.success_criteria) >= 2
        assert any("tests pass" in c.lower() for c in result.success_criteria)

    def test_prompt_without_criteria_invalid_strict(self, validator):
        prompt = "Just fix the bug somehow"
        result = validator.validate(prompt)
        assert result.is_valid is False
        assert len(result.suggestions) > 0

    def test_prompt_with_implicit_criteria_lenient(self, lenient_validator):
        prompt = "Fix the bug until all tests pass"
        result = lenient_validator.validate(prompt)
        assert result.is_valid is True
        assert result.completion_type == "implicit"
        assert len(result.warnings) > 0

    def test_generic_promise_warning(self, validator):
        prompt = "Do the thing. <promise>done</promise>"
        result = validator.validate(prompt)
        assert result.is_valid is True
        assert any("generic" in w.lower() for w in result.warnings)

    def test_no_max_iterations_warning(self, validator):
        prompt = "Fix bug. <promise>FIXED</promise>"
        result = validator.validate(prompt)
        assert result.is_valid is True
        assert any("max_iterations" in w.lower() for w in result.warnings)

    def test_extract_completion_signal_found(self, validator):
        output = "I completed the task. <promise>DONE</promise> All good."
        is_complete, text = validator.extract_completion_signal(output)
        assert is_complete is True
        assert text == "DONE"

    def test_extract_completion_signal_not_found(self, validator):
        output = "I'm still working on it."
        is_complete, text = validator.extract_completion_signal(output)
        assert is_complete is False
        assert text is None

    def test_format_validation_error(self, validator):
        result = validator.validate("vague prompt")
        error_msg = validator.format_validation_error(result)
        assert "failed" in error_msg.lower()
        assert "suggestions" in error_msg.lower()

    def test_validation_result_to_dict(self, validator):
        result = validator.validate("Fix bug <promise>DONE</promise>")
        d = result.to_dict()
        assert isinstance(d, dict)
        assert "is_valid" in d
        assert "completion_type" in d
        assert "promise_text" in d


class TestRalphWiggumProfile:
    """Tests for RalphWiggumProfile"""

    @pytest.fixture
    def profile(self):
        return RalphWiggumProfile()

    @pytest.fixture
    def custom_profile(self):
        config = RalphConfig(
            max_iterations=5,
            completion_promise="TASK COMPLETE",
        )
        return RalphWiggumProfile(ralph_config=config)

    def test_profile_init_defaults(self, profile):
        assert profile.name == "ralph_wiggum"
        assert profile.ralph_config.max_iterations == 10
        assert profile._current_iteration == 0
        assert profile._is_complete is False

    def test_profile_init_custom(self, custom_profile):
        assert custom_profile.ralph_config.max_iterations == 5
        assert custom_profile.ralph_config.completion_promise == "TASK COMPLETE"

    def test_get_system_prompt_basic(self, profile):
        prompt = profile.get_system_prompt()
        assert "Ralph Wiggum" in prompt
        assert "iteration" in prompt.lower()
        assert "<promise>" in prompt

    def test_get_system_prompt_with_context(self, profile):
        context = {
            "iteration": 3,
            "max_iterations": 10,
            "completion_promise": "DONE",
        }
        prompt = profile.get_system_prompt(context)
        assert "4" in prompt  # iteration + 1
        assert "10" in prompt

    @pytest.mark.asyncio
    async def test_process_input_valid_prompt(self, profile):
        input_data = {
            "prompt": "Fix the bug. <promise>FIXED</promise>",
        }
        result = await profile.process_input(input_data)
        assert result["valid"] is True
        assert result["completion_promise"] == "FIXED"

    @pytest.mark.asyncio
    async def test_process_input_uses_default_max_iterations(self, profile):
        """Profile uses default max_iterations, making prompts valid even without explicit criteria"""
        input_data = {
            "prompt": "Vague task without criteria",
        }
        result = await profile.process_input(input_data)
        # Profile provides default max_iterations (10), so validation passes
        assert result["valid"] is True
        assert result["max_iterations"] == 10

    @pytest.mark.asyncio
    async def test_process_input_invalid_when_strict_no_defaults(self):
        """Test that validation fails when strict and no defaults provided"""
        # Create profile with strict validation and no default max_iterations
        config = RalphConfig(require_completion_criteria=True)
        profile = RalphWiggumProfile(ralph_config=config)

        # Manually bypass the default max_iterations by setting it to None
        input_data = {
            "prompt": "Vague task without criteria",
            "config": {"max_iterations": None},  # Explicitly no limit
        }

        # Validate directly with the validator (bypassing profile defaults)
        validation = profile.validator.validate(input_data["prompt"], {})
        assert validation.is_valid is False

    @pytest.mark.asyncio
    async def test_process_input_with_max_iterations(self, profile):
        input_data = {
            "prompt": "Fix the bug",
            "config": {"max_iterations": 15},
        }
        result = await profile.process_input(input_data)
        assert result["valid"] is True
        assert result["max_iterations"] == 15

    @pytest.mark.asyncio
    async def test_process_output_with_completion(self, profile):
        output_data = {
            "content": "Task done! <promise>DONE</promise>",
            "iteration": 2,
        }
        result = await profile.process_output(output_data)
        assert result["success"] is True
        assert result["complete"] is True
        assert result["promise_text"] == "DONE"

    @pytest.mark.asyncio
    async def test_process_output_without_completion(self, profile):
        output_data = {
            "content": "Still working on it...",
            "iteration": 1,
        }
        result = await profile.process_output(output_data)
        assert result["success"] is False
        assert result["complete"] is False

    @pytest.mark.asyncio
    async def test_process_output_stuck_detection(self, profile):
        output_data = {
            "content": "I cannot proceed without database access.",
            "iteration": 3,
        }
        result = await profile.process_output(output_data)
        assert result["stuck"] is True
        assert result["complete"] is True

    def test_should_continue_initial(self, profile):
        assert profile.should_continue() is True

    def test_should_continue_after_completion(self, profile):
        profile._is_complete = True
        assert profile.should_continue() is False

    def test_should_continue_max_iterations(self, profile):
        profile._current_iteration = 10
        assert profile.should_continue() is False

    def test_reset_clears_state(self, profile):
        profile._current_iteration = 5
        profile._is_complete = True
        profile._iteration_history = [{"test": 1}]
        profile._completion_reason = "Done"

        profile.reset()

        assert profile._current_iteration == 0
        assert profile._is_complete is False
        assert profile._iteration_history == []
        assert profile._completion_reason is None

    def test_get_iteration_stats(self, profile):
        profile._current_iteration = 3
        profile._iteration_history = [
            {"iteration": 0, "success": False},
            {"iteration": 1, "success": False},
            {"iteration": 2, "success": True},
        ]
        profile._is_complete = True

        stats = profile.get_iteration_stats()

        assert stats["current_iteration"] == 3
        assert stats["total_iterations"] == 3
        assert stats["is_complete"] is True
        assert stats["successful_iterations"] == 1

    def test_get_tools(self, profile):
        tools = profile.get_tools()
        assert "Read" in tools
        assert "Write" in tools
        assert "Edit" in tools
        assert "Bash" in tools

    @pytest.mark.asyncio
    async def test_iteration_history_accumulates(self, profile):
        # Process multiple outputs
        for i in range(3):
            await profile.process_output(
                {
                    "content": f"Working on iteration {i}...",
                    "iteration": i,
                }
            )

        assert len(profile._iteration_history) == 3

    @pytest.mark.asyncio
    async def test_completion_stops_further_iterations(self, profile):
        await profile.process_output(
            {
                "content": "Done! <promise>DONE</promise>",
                "iteration": 2,
            }
        )

        assert profile.should_continue() is False
        assert profile._completion_reason is not None


class TestValidationPatterns:
    """Tests for various completion criteria patterns"""

    @pytest.fixture
    def validator(self):
        return CompletionCriteriaValidator(strict=True)

    @pytest.mark.parametrize(
        "prompt,expected_valid",
        [
            # Valid prompts
            ("Fix bug <promise>DONE</promise>", True),
            ("Task --max-iterations 10", True),
            ("max_iterations: 5", True),
            ("When complete:\n- Tests pass\n- <promise>DONE</promise>", True),
            # Invalid prompts (strict mode)
            ("Just fix it", False),
            ("Do something", False),
            ("Complete the task", False),
        ],
    )
    def test_various_patterns(self, validator, prompt, expected_valid):
        result = validator.validate(prompt)
        assert result.is_valid is expected_valid

    @pytest.mark.parametrize(
        "iterations_text,expected",
        [
            ("--max-iterations 10", 10),
            ("max_iterations: 15", 15),
            ("maximum of 20 iterations", 20),
            ("5 iterations max", 5),
        ],
    )
    def test_max_iterations_patterns(self, validator, iterations_text, expected):
        result = validator.validate(iterations_text)
        assert result.max_iterations == expected


class TestCompletionType:
    """Tests for CompletionType enum"""

    def test_explicit_signal_types(self):
        """Test that explicit signal types are correctly identified"""
        assert CompletionType.PROMISE.is_explicit_signal() is True
        assert CompletionType.COMPLETE.is_explicit_signal() is True
        assert CompletionType.DONE.is_explicit_signal() is True
        assert CompletionType.TASK_COMPLETE_PREFIX.is_explicit_signal() is True

    def test_non_explicit_signal_types(self):
        """Test that non-explicit types are correctly identified"""
        assert CompletionType.MAX_ITERATIONS.is_explicit_signal() is False
        assert CompletionType.CRITERIA.is_explicit_signal() is False
        assert CompletionType.IMPLICIT.is_explicit_signal() is False
        assert CompletionType.STUCK.is_explicit_signal() is False
        assert CompletionType.ERROR.is_explicit_signal() is False

    def test_success_signal_types(self):
        """Test success signal type identification"""
        assert CompletionType.PROMISE.is_success_signal() is True
        assert CompletionType.COMPLETE.is_success_signal() is True
        assert CompletionType.DONE.is_success_signal() is True
        assert CompletionType.TASK_COMPLETE_PREFIX.is_success_signal() is True
        assert CompletionType.CRITERIA.is_success_signal() is True

    def test_non_success_signal_types(self):
        """Test non-success signal type identification"""
        assert CompletionType.MAX_ITERATIONS.is_success_signal() is False
        assert CompletionType.IMPLICIT.is_success_signal() is False
        assert CompletionType.STUCK.is_success_signal() is False
        assert CompletionType.ERROR.is_success_signal() is False


class TestCompletionSignal:
    """Tests for CompletionSignal dataclass"""

    def test_default_signal(self):
        """Test default signal is not detected"""
        signal = CompletionSignal()
        assert signal.detected is False
        assert signal.signal_type is None
        assert signal.signal_text is None
        assert bool(signal) is False

    def test_detected_signal(self):
        """Test detected signal with all fields"""
        signal = CompletionSignal(
            detected=True,
            signal_type=CompletionType.PROMISE,
            signal_text="DONE",
            raw_match="<promise>DONE</promise>",
            confidence=1.0,
        )
        assert signal.detected is True
        assert signal.signal_type == CompletionType.PROMISE
        assert signal.signal_text == "DONE"
        assert bool(signal) is True

    def test_is_successful(self):
        """Test is_successful method"""
        # Successful signal
        signal = CompletionSignal(
            detected=True,
            signal_type=CompletionType.PROMISE,
            signal_text="DONE",
        )
        assert signal.is_successful() is True

        # Non-detected signal
        signal = CompletionSignal(detected=False)
        assert signal.is_successful() is False

        # Detected but error type
        signal = CompletionSignal(
            detected=True,
            signal_type=CompletionType.ERROR,
        )
        assert signal.is_successful() is False

    def test_to_dict(self):
        """Test conversion to dictionary"""
        signal = CompletionSignal(
            detected=True,
            signal_type=CompletionType.COMPLETE,
            signal_text="Task finished",
            raw_match="<complete>Task finished</complete>",
        )
        d = signal.to_dict()

        assert d["detected"] is True
        assert d["signal_type"] == "COMPLETE"
        assert d["signal_text"] == "Task finished"
        assert d["is_successful"] is True


class TestCompletionSignalDetector:
    """Tests for CompletionSignalDetector"""

    @pytest.fixture
    def detector(self):
        return CompletionSignalDetector()

    def test_detect_promise_tag(self, detector):
        """Test detection of <promise> tags"""
        text = "Task done! <promise>DONE</promise>"
        signal = detector.detect(text)

        assert signal.detected is True
        assert signal.signal_type == CompletionType.PROMISE
        assert signal.signal_text == "DONE"

    def test_detect_complete_tag(self, detector):
        """Test detection of <complete> tags"""
        text = "All finished. <complete>SUCCESS</complete>"
        signal = detector.detect(text)

        assert signal.detected is True
        assert signal.signal_type == CompletionType.COMPLETE
        assert signal.signal_text == "SUCCESS"

    def test_detect_done_tag(self, detector):
        """Test detection of <done> tags"""
        text = "Everything ready. <done>FINISHED</done>"
        signal = detector.detect(text)

        assert signal.detected is True
        assert signal.signal_type == CompletionType.DONE
        assert signal.signal_text == "FINISHED"

    def test_detect_task_complete_prefix(self, detector):
        """Test detection of TASK_COMPLETE: prefix"""
        text = "TASK_COMPLETE: All tests pass and build succeeds"
        signal = detector.detect(text)

        assert signal.detected is True
        assert signal.signal_type == CompletionType.TASK_COMPLETE_PREFIX
        assert "All tests pass" in signal.signal_text

    def test_detect_case_insensitive(self, detector):
        """Test case insensitivity of detection"""
        # Uppercase
        signal = detector.detect("<PROMISE>DONE</PROMISE>")
        assert signal.detected is True

        # Mixed case
        signal = detector.detect("<Promise>Done</Promise>")
        assert signal.detected is True

        # Lowercase prefix
        signal = detector.detect("task_complete: finished")
        assert signal.detected is True

    def test_detect_no_signal(self, detector):
        """Test when no signal is present"""
        text = "Still working on the task..."
        signal = detector.detect(text)

        assert signal.detected is False
        assert signal.signal_type is None
        assert signal.signal_text is None

    def test_detect_empty_text(self, detector):
        """Test with empty text"""
        signal = detector.detect("")
        assert signal.detected is False

        signal = detector.detect(None)
        assert signal.detected is False

    def test_detect_multiline_signal(self, detector):
        """Test detection in multiline text"""
        text = """
        Working on the task...
        Making progress...
        <promise>
            TASK COMPLETE
        </promise>
        """
        signal = detector.detect(text)

        assert signal.detected is True
        assert signal.signal_type == CompletionType.PROMISE
        assert "TASK COMPLETE" in signal.signal_text

    def test_detect_priority_order(self, detector):
        """Test that promise takes priority when multiple signals present"""
        # Promise should be detected first (highest priority)
        text = "<promise>DONE</promise> <complete>ALSO DONE</complete>"
        signal = detector.detect(text)

        assert signal.detected is True
        assert signal.signal_type == CompletionType.PROMISE
        assert signal.signal_text == "DONE"

    def test_detect_all_signals(self, detector):
        """Test detecting all signals in text"""
        text = "<promise>ONE</promise> <complete>TWO</complete> <done>THREE</done>"
        signals = detector.detect_all(text)

        assert len(signals) == 3
        types = [s.signal_type for s in signals]
        assert CompletionType.PROMISE in types
        assert CompletionType.COMPLETE in types
        assert CompletionType.DONE in types

    def test_has_signal(self, detector):
        """Test has_signal convenience method"""
        assert detector.has_signal("<promise>DONE</promise>") is True
        assert detector.has_signal("<complete>DONE</complete>") is True
        assert detector.has_signal("<done>DONE</done>") is True
        assert detector.has_signal("TASK_COMPLETE: done") is True
        assert detector.has_signal("no signal here") is False

    def test_create_pattern(self):
        """Test creating custom tag patterns"""
        pattern = CompletionSignalDetector.create_pattern("custom")
        match = pattern.search("<custom>MY SIGNAL</custom>")

        assert match is not None
        assert match.group(1).strip() == "MY SIGNAL"

    def test_custom_patterns_initialization(self):
        """Test initialization with custom patterns"""
        custom_patterns = [
            ("finished", r"<finished>\s*(.+?)\s*</finished>"),
        ]
        detector = CompletionSignalDetector(custom_patterns=custom_patterns)

        # Should still detect default patterns
        signal = detector.detect("<promise>DONE</promise>")
        assert signal.detected is True

        # Should also detect custom pattern
        signal = detector.detect("<finished>ALL DONE</finished>")
        assert signal.detected is True
        assert signal.signal_text == "ALL DONE"


class TestConvenienceFunctions:
    """Tests for module-level convenience functions"""

    def test_detect_completion(self):
        """Test detect_completion function"""
        signal = detect_completion("<promise>DONE</promise>")
        assert signal.detected is True
        assert signal.signal_type == CompletionType.PROMISE

        signal = detect_completion("no signal")
        assert signal.detected is False

    def test_has_completion_signal(self):
        """Test has_completion_signal function"""
        assert has_completion_signal("<promise>DONE</promise>") is True
        assert has_completion_signal("<complete>DONE</complete>") is True
        assert has_completion_signal("<done>DONE</done>") is True
        assert has_completion_signal("TASK_COMPLETE: done") is True
        assert has_completion_signal("no signal") is False

    def test_extract_signal_text(self):
        """Test extract_signal_text function"""
        assert extract_signal_text("<promise>DONE</promise>") == "DONE"
        assert extract_signal_text("<complete>SUCCESS</complete>") == "SUCCESS"
        assert extract_signal_text("no signal") is None


class TestValidatorWithNewSignals:
    """Tests for validator using new signal patterns"""

    @pytest.fixture
    def validator(self):
        return CompletionCriteriaValidator(strict=True)

    def test_validate_complete_tag(self, validator):
        """Test validation with <complete> tag"""
        prompt = "Fix the bug. <complete>FIXED</complete>"
        result = validator.validate(prompt)

        assert result.is_valid is True
        assert result.completion_type == "complete"
        assert result.promise_text == "FIXED"
        assert result.completion_signal is not None
        assert result.completion_signal.signal_type == CompletionType.COMPLETE

    def test_validate_done_tag(self, validator):
        """Test validation with <done> tag"""
        prompt = "Implement feature. <done>IMPLEMENTED</done>"
        result = validator.validate(prompt)

        assert result.is_valid is True
        assert result.completion_type == "done"
        assert result.promise_text == "IMPLEMENTED"

    def test_validate_task_complete_prefix(self, validator):
        """Test validation with TASK_COMPLETE prefix"""
        prompt = "Build the project. TASK_COMPLETE: build successful"
        result = validator.validate(prompt)

        assert result.is_valid is True
        assert result.completion_type == "task_complete"
        assert "build successful" in result.promise_text

    def test_detect_completion_signal_method(self, validator):
        """Test the new detect_completion_signal method"""
        output = "<complete>All tests pass</complete>"
        signal = validator.detect_completion_signal(output)

        assert signal.detected is True
        assert signal.signal_type == CompletionType.COMPLETE
        assert signal.signal_text == "All tests pass"

    def test_detect_all_completion_signals_method(self, validator):
        """Test the new detect_all_completion_signals method"""
        output = "<promise>ONE</promise> and <done>TWO</done>"
        signals = validator.detect_all_completion_signals(output)

        assert len(signals) == 2

    def test_backward_compatible_extract_signal(self, validator):
        """Test that extract_completion_signal still works with new patterns"""
        # Promise tag (original pattern)
        is_complete, text = validator.extract_completion_signal(
            "<promise>DONE</promise>"
        )
        assert is_complete is True
        assert text == "DONE"

        # Complete tag (new pattern)
        is_complete, text = validator.extract_completion_signal(
            "<complete>SUCCESS</complete>"
        )
        assert is_complete is True
        assert text == "SUCCESS"

        # Done tag (new pattern)
        is_complete, text = validator.extract_completion_signal("<done>FINISHED</done>")
        assert is_complete is True
        assert text == "FINISHED"

        # TASK_COMPLETE prefix (new pattern)
        is_complete, text = validator.extract_completion_signal(
            "TASK_COMPLETE: all done"
        )
        assert is_complete is True
        assert "all done" in text

    def test_validation_result_to_dict_with_signal(self, validator):
        """Test that ValidationResult.to_dict includes completion_signal"""
        result = validator.validate("Fix bug <complete>FIXED</complete>")
        d = result.to_dict()

        assert "completion_signal" in d
        assert d["completion_signal"] is not None
        assert d["completion_signal"]["signal_type"] == "COMPLETE"
        assert d["completion_signal"]["signal_text"] == "FIXED"
