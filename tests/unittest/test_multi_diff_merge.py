"""
Unit tests for PRReviewer multi-diff mode merge logic.

Tests the actual _merge_review_chunks implementation via a minimal wrapper.
"""
import pytest
from typing import List


def get_merge_function():
    """
    Get the actual _merge_review_chunks method from PRReviewer.

    We wrap it to avoid needing to instantiate the full PRReviewer class.
    """
    from pr_agent.tools.pr_reviewer import PRReviewer

    class MergeWrapper:
        """Minimal wrapper to call _merge_review_chunks without full PRReviewer init."""
        def merge(self, chunk_reviews: List[dict]) -> dict:
            return PRReviewer._merge_review_chunks(self, chunk_reviews)

    return MergeWrapper().merge


class TestMultiDiffMergeChunks:
    """Tests for _merge_review_chunks method using the actual implementation."""

    @pytest.fixture
    def merge(self):
        """Provide the actual merge function."""
        return get_merge_function()

    def test_single_chunk_passthrough(self, merge):
        """Single chunk should be returned as-is."""
        chunk = {'review': {'estimated_effort_to_review_[1-5]': '2, simple change'}}
        result = merge([chunk])
        assert result == chunk

    def test_empty_chunks_returns_none(self, merge):
        """Empty list should return None."""
        result = merge([])
        assert result is None

    def test_effort_takes_maximum(self, merge):
        """Effort should be the maximum across chunks."""
        chunks = [
            {'review': {'estimated_effort_to_review_[1-5]': '2, simple'}},
            {'review': {'estimated_effort_to_review_[1-5]': '4, complex'}},
            {'review': {'estimated_effort_to_review_[1-5]': '3, medium'}},
        ]
        result = merge(chunks)
        assert '4' in result['review']['estimated_effort_to_review_[1-5]']
        assert 'aggregated' in result['review']['estimated_effort_to_review_[1-5]']

    def test_score_takes_minimum(self, merge):
        """Score should be the minimum (worst) across chunks."""
        chunks = [
            {'review': {'score': '80'}},
            {'review': {'score': '60'}},
            {'review': {'score': '75'}},
        ]
        result = merge(chunks)
        assert '60' in result['review']['score']
        assert 'aggregated' in result['review']['score']

    def test_score_handles_various_formats(self, merge):
        """Score parsing should handle various formats."""
        chunks = [
            {'review': {'score': '85/100'}},
            {'review': {'score': '70, good overall'}},
            {'review': {'score': '90'}},
        ]
        result = merge(chunks)
        # Should take minimum: 70
        assert '70' in result['review']['score']

    def test_security_concerns_or_logic(self, merge):
        """Security concerns should use OR logic."""
        chunks = [
            {'review': {'security_concerns': 'No'}},
            {'review': {'security_concerns': 'Yes, SQL injection risk'}},
            {'review': {'security_concerns': 'No'}},
        ]
        result = merge(chunks)
        assert 'Yes' in result['review']['security_concerns']
        assert 'SQL injection' in result['review']['security_concerns']

    def test_security_concerns_all_no(self, merge):
        """When all chunks say No, result should be No."""
        chunks = [
            {'review': {'security_concerns': 'No'}},
            {'review': {'security_concerns': 'No'}},
        ]
        result = merge(chunks)
        assert result['review']['security_concerns'] == 'No'

    def test_key_issues_concatenated_and_deduped(self, merge):
        """Key issues should be concatenated and de-duplicated."""
        chunks = [
            {'review': {'key_issues_to_review': [
                {'relevant_file': 'a.py', 'start_line': 10, 'end_line': 20, 'issue_header': 'Bug'},
            ]}},
            {'review': {'key_issues_to_review': [
                {'relevant_file': 'b.py', 'start_line': 5, 'end_line': 15, 'issue_header': 'Style'},
                # Duplicate of first chunk's issue
                {'relevant_file': 'a.py', 'start_line': 10, 'end_line': 20, 'issue_header': 'Bug'},
            ]}},
        ]
        result = merge(chunks)
        issues = result['review']['key_issues_to_review']
        # Should have 2 unique issues (not 3)
        assert len(issues) == 2
        files = {i['relevant_file'] for i in issues}
        assert files == {'a.py', 'b.py'}

    def test_relevant_tests_or_logic(self, merge):
        """Relevant tests should use OR logic."""
        chunks = [
            {'review': {'relevant_tests': 'No'}},
            {'review': {'relevant_tests': 'Yes, found unit tests'}},
        ]
        result = merge(chunks)
        assert result['review']['relevant_tests'] == 'Yes'

    def test_possible_issues_collected(self, merge):
        """Possible issues should be collected from all chunks."""
        chunks = [
            {'review': {'possible_issues': 'Memory leak in handler'}},
            {'review': {'possible_issues': 'No'}},
            {'review': {'possible_issues': 'Race condition possible'}},
        ]
        result = merge(chunks)
        issues = result['review']['possible_issues']
        assert 'Memory leak' in issues
        assert 'Race condition' in issues

    def test_ticket_compliance_merged(self, merge):
        """Ticket compliance checks should be merged."""
        chunks = [
            {'review': {'ticket_compliance_check': [
                {'ticket_url': 'https://jira.example.com/PROJ-123'}
            ]}},
            {'review': {'ticket_compliance_check': [
                {'ticket_url': 'https://jira.example.com/PROJ-456'}
            ]}},
        ]
        result = merge(chunks)
        tickets = result['review']['ticket_compliance_check']
        assert len(tickets) == 2

    def test_can_be_split_merged(self, merge):
        """Can be split suggestions should be merged."""
        chunks = [
            {'review': {'can_be_split': [
                {'title': 'Refactoring', 'relevant_files': ['a.py']}
            ]}},
            {'review': {'can_be_split': [
                {'title': 'Bug fix', 'relevant_files': ['b.py']}
            ]}},
        ]
        result = merge(chunks)
        splits = result['review']['can_be_split']
        assert len(splits) == 2

    def test_mixed_fields(self, merge):
        """Test merging with a realistic mix of all field types."""
        chunks = [
            {
                'review': {
                    'estimated_effort_to_review_[1-5]': '2, chunk 1',
                    'score': '85',
                    'security_concerns': 'No',
                    'relevant_tests': 'No',
                    'possible_issues': 'No',
                    'key_issues_to_review': [
                        {'relevant_file': 'api.py', 'start_line': 10, 'end_line': 20, 'issue_header': 'Error handling'}
                    ]
                }
            },
            {
                'review': {
                    'estimated_effort_to_review_[1-5]': '4, chunk 2',
                    'score': '65',
                    'security_concerns': 'Yes, auth bypass possible',
                    'relevant_tests': 'Yes',
                    'possible_issues': 'Performance in query loop',
                    'key_issues_to_review': [
                        {'relevant_file': 'db.py', 'start_line': 50, 'end_line': 60, 'issue_header': 'SQL issue'}
                    ]
                }
            }
        ]
        result = merge(chunks)
        review = result['review']

        assert '4' in review['estimated_effort_to_review_[1-5]']  # max effort
        assert '65' in review['score']  # min score
        assert 'Yes' in review['security_concerns']  # OR
        assert review['relevant_tests'] == 'Yes'  # OR
        assert 'Performance' in review['possible_issues']
        assert len(review['key_issues_to_review']) == 2
