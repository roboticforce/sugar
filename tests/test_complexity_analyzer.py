"""
Tests for Task Complexity Analyzer

Tests complexity assessment, Ralph mode recommendations, and completion criteria generation.
"""

import pytest
from sugar.triage import (
    ComplexityAnalyzer,
    ComplexityLevel,
    ComplexityScore,
    ExecutionRecommendation,
    TaskAnalysis,
)


class TestComplexityAnalyzer:
    """Test the ComplexityAnalyzer"""

    def setup_method(self):
        """Set up test fixtures"""
        self.analyzer = ComplexityAnalyzer()

    def test_simple_documentation_task(self):
        """Test that simple documentation tasks are correctly assessed"""
        work_item = {
            "type": "documentation",
            "title": "Update README with installation instructions",
            "description": "Add a section about pip install to the README",
            "priority": 3,
        }

        analysis = self.analyzer.analyze(work_item)

        assert analysis.complexity.level in [
            ComplexityLevel.SIMPLE,
            ComplexityLevel.MODERATE,
        ]
        assert analysis.recommendation.use_ralph_mode is False
        assert analysis.recommendation.estimated_iterations <= 2

    def test_complex_refactor_task(self):
        """Test that complex refactor tasks get Ralph mode recommendation"""
        work_item = {
            "type": "refactor",
            "title": "Refactor entire authentication system",
            "description": """
            Migrate the authentication system from session-based to JWT tokens.
            This is a system-wide change affecting multiple files across the codebase.
            Need to:
            - Update login endpoints
            - Refactor middleware
            - Update database schema
            - Migrate existing users
            """,
            "priority": 4,
        }

        analysis = self.analyzer.analyze(work_item)

        assert analysis.complexity.level.value >= 3
        assert analysis.recommendation.use_ralph_mode is True
        assert analysis.recommendation.estimated_iterations >= 3
        assert "TASK_COMPLETE" in str(analysis.recommendation.completion_criteria)

    def test_bug_fix_complexity_varies(self):
        """Test that bug fixes can be simple or complex"""
        simple_bug = {
            "type": "bug_fix",
            "title": "Fix typo in error message",
            "description": "Change 'occured' to 'occurred' in error handler",
            "priority": 2,
        }

        complex_bug = {
            "type": "bug_fix",
            "title": "Fix critical memory leak in request handler",
            "description": """
            Production issue: memory usage grows unbounded under load.
            Need to investigate connection pooling, identify leak source,
            and implement proper cleanup. This is a critical production issue.
            """,
            "priority": 5,
        }

        simple_analysis = self.analyzer.analyze(simple_bug)
        complex_analysis = self.analyzer.analyze(complex_bug)

        # Simple bug should be low complexity
        assert simple_analysis.complexity.level.value <= 2
        assert simple_analysis.recommendation.use_ralph_mode is False

        # Complex bug should use Ralph due to high risk, even if complexity is moderate
        # (High risk factor triggers iterative validation)
        assert complex_analysis.complexity.risk_factor > 0.7
        assert complex_analysis.recommendation.use_ralph_mode is True
        # Should recommend more iterations due to risk
        assert complex_analysis.recommendation.estimated_iterations >= 3

    def test_feature_with_integration(self):
        """Test that features with integrations get higher complexity"""
        work_item = {
            "type": "feature",
            "title": "Add Stripe payment integration",
            "description": """
            Implement Stripe payment processing:
            - Create payment API endpoints
            - Add webhook handlers for payment events
            - Integrate with existing order system
            - Add database tables for transactions
            - Implement OAuth for Stripe Connect
            """,
            "priority": 4,
        }

        analysis = self.analyzer.analyze(work_item)

        assert analysis.complexity.integration_factor > 0.5
        # High integration triggers Ralph mode
        assert analysis.recommendation.use_ralph_mode is True
        # Should have integration-related quality gates
        assert any(
            "integration" in gate or "test" in gate
            for gate in analysis.recommendation.quality_gates
        )

    def test_scope_factor_detection(self):
        """Test detection of wide-scope tasks"""
        work_item = {
            "type": "refactor",
            "title": "Migrate entire codebase to TypeScript",
            "description": "Convert all JavaScript files throughout the project to TypeScript",
            "priority": 3,
        }

        analysis = self.analyzer.analyze(work_item)

        assert analysis.complexity.scope_factor > 0.7
        assert "entire" in analysis.detected_keywords.get(
            "scope", []
        ) or "throughout" in analysis.detected_keywords.get("scope", [])

    def test_risk_factor_detection(self):
        """Test detection of risky tasks"""
        work_item = {
            "type": "feature",
            "title": "Implement breaking change to API",
            "description": """
            Breaking change: modify API response format.
            This is backwards incompatible and requires data migration.
            Critical production change.
            """,
            "priority": 5,
        }

        analysis = self.analyzer.analyze(work_item)

        assert analysis.complexity.risk_factor > 0.7
        assert analysis.recommendation.use_ralph_mode is True
        # High risk should lead to more iterations for validation
        assert analysis.recommendation.estimated_iterations >= 3

    def test_completion_criteria_by_type(self):
        """Test that completion criteria are appropriate for task type"""
        # Bug fix criteria
        bug_item = {
            "type": "bug_fix",
            "title": "Fix login issue",
            "description": "Users can't log in with special characters in password",
            "priority": 3,
        }
        bug_analysis = self.analyzer.analyze(bug_item)
        bug_criteria = bug_analysis.recommendation.completion_criteria

        assert any("regression" in c.lower() for c in bug_criteria)
        assert any("test" in c.lower() for c in bug_criteria)

        # Feature criteria
        feature_item = {
            "type": "feature",
            "title": "Add dark mode",
            "description": "Implement dark mode theme toggle",
            "priority": 3,
        }
        feature_analysis = self.analyzer.analyze(feature_item)
        feature_criteria = feature_analysis.recommendation.completion_criteria

        assert any("implementation" in c.lower() for c in feature_criteria)
        assert any("documentation" in c.lower() for c in feature_criteria)

        # Refactor criteria
        refactor_item = {
            "type": "refactor",
            "title": "Refactor user service",
            "description": "Clean up user service code and improve structure",
            "priority": 3,
        }
        refactor_analysis = self.analyzer.analyze(refactor_item)
        refactor_criteria = refactor_analysis.recommendation.completion_criteria

        assert any("refactor" in c.lower() for c in refactor_criteria)
        assert any("functionality" in c.lower() for c in refactor_criteria)

    def test_quality_gates_generation(self):
        """Test quality gate generation based on complexity"""
        # Simple task - basic gates
        simple_item = {
            "type": "style",
            "title": "Format code",
            "description": "Run prettier on all files",
            "priority": 2,
        }
        simple_analysis = self.analyzer.analyze(simple_item)

        # Complex task - comprehensive gates
        complex_item = {
            "type": "feature",
            "title": "Build new microservice",
            "description": """
            Create a new microservice for user analytics.
            Needs to integrate with existing services via API.
            Requires database, caching, and message queue.
            This is a complex system-wide change.
            """,
            "priority": 4,
        }
        complex_analysis = self.analyzer.analyze(complex_item)

        # Simple tasks should have fewer gates
        assert len(simple_analysis.recommendation.quality_gates) <= 3

        # Complex tasks should have more comprehensive gates
        assert len(complex_analysis.recommendation.quality_gates) >= 3
        # Should have testing-related gates at minimum
        assert any(
            "test" in gate for gate in complex_analysis.recommendation.quality_gates
        )

    def test_strict_mode_reduces_iterations(self):
        """Test that strict mode reduces max iterations"""
        work_item = {
            "type": "refactor",
            "title": "Major refactor",
            "description": "Refactor entire module structure",
            "priority": 3,
        }

        normal_analyzer = ComplexityAnalyzer(strict_mode=False)
        strict_analyzer = ComplexityAnalyzer(strict_mode=True)

        normal_analysis = normal_analyzer.analyze(work_item)
        strict_analysis = strict_analyzer.analyze(work_item)

        # Strict mode should have lower or equal max iterations
        assert (
            strict_analysis.recommendation.max_iterations
            <= normal_analysis.recommendation.max_iterations
        )

    def test_priority_influences_iterations(self):
        """Test that high priority tasks get conservative iteration limits"""
        low_priority_item = {
            "type": "feature",
            "title": "Add feature",
            "description": "Implement new feature with multiple integrations",
            "priority": 1,
        }

        high_priority_item = {
            "type": "feature",
            "title": "Add feature",
            "description": "Implement new feature with multiple integrations",
            "priority": 5,
        }

        low_analysis = self.analyzer.analyze(low_priority_item)
        high_analysis = self.analyzer.analyze(high_priority_item)

        # High priority should have more conservative (lower) max iterations
        if high_analysis.recommendation.use_ralph_mode:
            assert high_analysis.recommendation.max_iterations <= 8

    def test_confidence_assessment(self):
        """Test confidence scoring for recommendations"""
        # Detailed description = higher confidence
        detailed_item = {
            "type": "feature",
            "title": "Add search functionality",
            "description": """
            Implement full-text search:
            1. Add Elasticsearch integration
            2. Create indexing pipeline
            3. Build search API endpoints
            4. Add search UI components
            5. Implement filtering and faceting

            Requirements:
            - Response time < 100ms
            - Support fuzzy matching
            - Handle millions of documents
            """,
            "priority": 3,
        }

        # Vague description = lower confidence
        vague_item = {
            "type": "feature",
            "title": "Search",
            "description": "Add search",
            "priority": 3,
        }

        detailed_analysis = self.analyzer.analyze(detailed_item)
        vague_analysis = self.analyzer.analyze(vague_item)

        assert (
            detailed_analysis.complexity.confidence
            > vague_analysis.complexity.confidence
        )
        assert (
            detailed_analysis.recommendation.confidence
            > vague_analysis.recommendation.confidence
        )

    def test_keyword_detection(self):
        """Test keyword detection and categorization"""
        work_item = {
            "type": "refactor",
            "title": "Migrate and optimize database queries",
            "description": """
            Migrate database queries to use new ORM.
            Optimize performance across multiple files.
            This is a system-wide change.
            """,
            "priority": 3,
        }

        analysis = self.analyzer.analyze(work_item)
        keywords = analysis.detected_keywords

        assert len(keywords["complex"]) > 0  # "migrate", "optimize"
        assert len(keywords["scope"]) > 0  # "system-wide", "multiple"

    def test_characteristics_extraction(self):
        """Test extraction of task characteristics"""
        work_item = {
            "type": "feature",
            "title": "Update user.py module",
            "description": """
            Modify src/models/user.py to add new fields:
            1. Add email verification
            2. Add password reset token
            3. Update tests in tests/test_user.py
            """,
            "priority": 3,
            "context": {"repo": "myapp"},
        }

        analysis = self.analyzer.analyze(work_item)
        chars = analysis.characteristics

        assert chars["task_type"] == "feature"
        assert chars["priority"] == 3
        assert chars["has_context"] is True
        assert chars["has_code_references"] is True  # user.py
        assert chars["has_file_paths"] is True  # src/models/
        assert chars["has_numbered_steps"] is True  # 1., 2., 3.

    def test_to_dict_serialization(self):
        """Test that analysis results can be serialized to dict"""
        work_item = {
            "type": "feature",
            "title": "Add logging",
            "description": "Add structured logging to the application",
            "priority": 3,
        }

        analysis = self.analyzer.analyze(work_item)
        result_dict = analysis.to_dict()

        assert "complexity" in result_dict
        assert "recommendation" in result_dict
        assert "detected_keywords" in result_dict
        assert "characteristics" in result_dict

        # Check nested serialization
        assert "level" in result_dict["complexity"]
        assert "use_ralph_mode" in result_dict["recommendation"]

    def test_performance_optimization_task(self):
        """Test analysis of performance optimization tasks"""
        work_item = {
            "type": "perf",
            "title": "Optimize database query performance",
            "description": """
            Critical performance issue: dashboard queries taking 10+ seconds.
            Need to:
            - Analyze slow queries
            - Add appropriate indexes
            - Optimize N+1 queries
            - Implement caching where appropriate
            - Benchmark improvements
            """,
            "priority": 5,
        }

        analysis = self.analyzer.analyze(work_item)

        # Performance tasks should be complex
        assert analysis.complexity.level.value >= 3
        # Should recommend Ralph for iterative optimization
        assert analysis.recommendation.use_ralph_mode is True
        # Should include benchmarking in criteria
        assert any(
            "benchmark" in c.lower() or "measurable" in c.lower()
            for c in analysis.recommendation.completion_criteria
        )

    def test_security_task_analysis(self):
        """Test analysis of security-related tasks"""
        work_item = {
            "type": "security",
            "title": "Fix SQL injection vulnerability",
            "description": """
            Security vulnerability found in user input handling.
            Critical: need to sanitize all user inputs and use parameterized queries.
            Affects authentication and data access layers.
            """,
            "priority": 5,
        }

        analysis = self.analyzer.analyze(work_item)

        # Security tasks should have high risk factor
        assert analysis.complexity.risk_factor > 0.5
        # Should recommend careful iteration
        assert analysis.recommendation.use_ralph_mode is True
        # Should include risk mitigation in criteria
        assert any(
            "risk" in c.lower() or "security" in c.lower()
            for c in analysis.recommendation.completion_criteria
        )

    def test_test_task_simple_vs_complex(self):
        """Test that test tasks can be simple or complex"""
        simple_test = {
            "type": "test",
            "title": "Add unit test for utils function",
            "description": "Add test for formatDate utility function",
            "priority": 2,
        }

        complex_test = {
            "type": "test",
            "title": "Add comprehensive E2E test suite",
            "description": """
            Create end-to-end test suite covering:
            - User authentication flow
            - Payment processing
            - Order management
            - Admin dashboard
            Need to set up test database, mock external APIs, and handle async operations.
            """,
            "priority": 4,
        }

        simple_analysis = self.analyzer.analyze(simple_test)
        complex_analysis = self.analyzer.analyze(complex_test)

        # Simple test should be simpler
        assert (
            simple_analysis.complexity.level.value
            < complex_analysis.complexity.level.value
        )
        # Complex E2E test with high integration should recommend Ralph
        # OR at minimum should have higher integration factor
        assert (
            complex_analysis.complexity.integration_factor > 0.7
            or complex_analysis.recommendation.use_ralph_mode is True
        )

    def test_ci_cd_task(self):
        """Test analysis of CI/CD tasks"""
        work_item = {
            "type": "ci",
            "title": "Set up GitHub Actions pipeline",
            "description": """
            Configure CI/CD pipeline:
            - Run tests on PR
            - Build Docker images
            - Deploy to staging
            - Run integration tests
            - Deploy to production
            """,
            "priority": 3,
        }

        analysis = self.analyzer.analyze(work_item)

        # CI tasks are usually moderate complexity
        assert 2 <= analysis.complexity.level.value <= 4
        # May or may not need Ralph depending on complexity
        # Should have appropriate quality gates
        assert "syntax_check" in analysis.recommendation.quality_gates

    def test_empty_description_handling(self):
        """Test handling of tasks with minimal description"""
        work_item = {
            "type": "bug_fix",
            "title": "Fix bug",
            "description": "",
            "priority": 3,
        }

        analysis = self.analyzer.analyze(work_item)

        # Should still produce valid analysis
        assert analysis.complexity.level is not None
        assert analysis.recommendation is not None
        # Confidence should be lower
        assert analysis.complexity.confidence < 0.6


class TestComplexityScore:
    """Test ComplexityScore dataclass"""

    def test_complexity_score_to_dict(self):
        """Test ComplexityScore serialization"""
        score = ComplexityScore(
            level=ComplexityLevel.COMPLEX,
            scope_factor=0.8,
            logic_factor=0.7,
            integration_factor=0.6,
            risk_factor=0.5,
            confidence=0.8,
            reasoning=["Test reasoning"],
        )

        result = score.to_dict()

        assert result["level"] == 4
        assert result["level_name"] == "COMPLEX"
        assert result["scope_factor"] == 0.8
        assert result["confidence"] == 0.8


class TestExecutionRecommendation:
    """Test ExecutionRecommendation dataclass"""

    def test_execution_recommendation_to_dict(self):
        """Test ExecutionRecommendation serialization"""
        rec = ExecutionRecommendation(
            use_ralph_mode=True,
            estimated_iterations=5,
            max_iterations=10,
            completion_criteria=["Tests pass", "Code reviewed"],
            quality_gates=["syntax_check", "test_execution"],
            reasoning=["High complexity"],
            confidence=0.85,
        )

        result = rec.to_dict()

        assert result["use_ralph_mode"] is True
        assert result["estimated_iterations"] == 5
        assert len(result["completion_criteria"]) == 2
        assert len(result["quality_gates"]) == 2


class TestEdgeCases:
    """Test edge cases and boundary conditions"""

    def setup_method(self):
        """Set up test fixtures"""
        self.analyzer = ComplexityAnalyzer()

    def test_unknown_task_type(self):
        """Test handling of unknown task types"""
        work_item = {
            "type": "unknown_type",
            "title": "Do something",
            "description": "Some task",
            "priority": 3,
        }

        analysis = self.analyzer.analyze(work_item)

        # Should still work with default complexity
        assert analysis.complexity.level is not None
        assert analysis.recommendation is not None

    def test_very_long_description(self):
        """Test handling of very long descriptions"""
        work_item = {
            "type": "feature",
            "title": "Complex feature",
            "description": "A" * 10000,  # Very long description
            "priority": 3,
        }

        analysis = self.analyzer.analyze(work_item)

        # Should handle without errors
        assert analysis.complexity.level is not None
        # Long description should increase confidence
        assert analysis.complexity.confidence > 0.5

    def test_special_characters_in_text(self):
        """Test handling of special characters"""
        work_item = {
            "type": "feature",
            "title": "Add emojis 🎉",
            "description": "Support unicode: émojis, ñoño, 中文",
            "priority": 3,
        }

        analysis = self.analyzer.analyze(work_item)

        # Should handle without errors
        assert analysis.complexity.level is not None

    def test_missing_optional_fields(self):
        """Test handling of missing optional fields"""
        work_item = {
            "type": "feature",
            "title": "Add feature",
            # Missing description, priority, context
        }

        analysis = self.analyzer.analyze(work_item)

        # Should use defaults
        assert analysis.complexity.level is not None
        assert analysis.characteristics["priority"] == 3  # Default priority
