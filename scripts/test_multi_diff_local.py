#!/usr/bin/env python3
"""
Manual test script for multi-diff review mode.

This script validates the multi-diff implementation against a local LLM server
(e.g., llama.cpp serving Qwen2.5-Coder).

Usage:
    # From pr-agent-fork directory with venv activated:
    python scripts/test_multi_diff_local.py --pr-url <gitea_pr_url>

    # Or simulate with a mock:
    python scripts/test_multi_diff_local.py --mock

Environment:
    Requires GITEA_TOKEN or .secrets.toml configured.
    Expects local LLM at http://127.0.0.1:8080/v1 (or override with --api-base).
"""

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

# Add pr_agent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s'
)
logger = logging.getLogger(__name__)


def setup_settings(args):
    """Configure pr-agent settings for local multi-diff testing."""
    from pr_agent.config_loader import get_settings

    settings = get_settings()

    # Enable multi-diff mode
    settings.pr_reviewer.enable_multi_diff = True
    settings.pr_reviewer.max_diff_calls = args.max_chunks

    # Configure for local LLM
    settings.config.model = args.model
    settings.config.max_model_tokens = args.max_tokens
    settings.config.custom_model_max_tokens = args.max_tokens
    settings.config.temperature = 0.15

    # OpenAI-compatible endpoint
    settings.openai.api_base = args.api_base
    settings.openai.key = args.api_key or "dummy"

    logger.info(f"Settings configured:")
    logger.info(f"  enable_multi_diff: {settings.pr_reviewer.enable_multi_diff}")
    logger.info(f"  max_diff_calls: {settings.pr_reviewer.max_diff_calls}")
    logger.info(f"  model: {settings.config.model}")
    logger.info(f"  max_model_tokens: {settings.config.max_model_tokens}")
    logger.info(f"  api_base: {settings.openai.api_base}")

    return settings


async def test_with_real_pr(pr_url: str, settings):
    """Run multi-diff review on a real PR."""
    from pr_agent.tools.pr_reviewer import PRReviewer

    logger.info(f"Testing multi-diff review on: {pr_url}")

    try:
        reviewer = PRReviewer(pr_url)

        # Log PR info
        logger.info(f"PR title: {reviewer.git_provider.pr.title}")
        logger.info(f"PR files: {len(reviewer.git_provider.get_diff_files())}")

        # Run the review
        await reviewer.run()

        if reviewer.prediction:
            logger.info("Review completed successfully!")
            logger.info(f"Prediction length: {len(reviewer.prediction)}")

            # Check if multi-diff was actually used
            if hasattr(reviewer, 'patches_diff') and reviewer.patches_diff:
                logger.info(f"Patches diff set (length: {len(reviewer.patches_diff)})")
        else:
            logger.warning("Review returned no result")

        return bool(reviewer.prediction)

    except Exception as e:
        logger.error(f"Review failed: {e}", exc_info=True)
        return None


async def test_with_mock():
    """Test multi-diff merge logic with mock data (no LLM calls)."""
    from pr_agent.tools.pr_reviewer import PRReviewer

    logger.info("Testing merge logic with mock data...")

    # Create mock chunk reviews
    chunk_reviews = [
        {
            'review': {
                'estimated_effort_to_review_[1-5]': '2, simple changes in chunk 1',
                'score': '75',
                'security_concerns': 'No',
                'relevant_tests': 'No',
                'possible_issues': 'No',
                'key_issues_to_review': [
                    {
                        'relevant_file': 'src/api.py',
                        'start_line': 10,
                        'end_line': 20,
                        'issue_header': 'Error handling',
                        'issue_content': 'Missing try-except block'
                    }
                ]
            }
        },
        {
            'review': {
                'estimated_effort_to_review_[1-5]': '4, complex refactoring in chunk 2',
                'score': '60',
                'security_concerns': 'Yes, potential SQL injection in query builder',
                'relevant_tests': 'Yes, found unit tests',
                'possible_issues': 'Performance concern in loop',
                'key_issues_to_review': [
                    {
                        'relevant_file': 'src/db.py',
                        'start_line': 50,
                        'end_line': 75,
                        'issue_header': 'SQL Injection',
                        'issue_content': 'User input not sanitized'
                    },
                    # Duplicate from chunk 1 (should be de-duplicated)
                    {
                        'relevant_file': 'src/api.py',
                        'start_line': 10,
                        'end_line': 20,
                        'issue_header': 'Error handling',
                        'issue_content': 'Missing try-except block'
                    }
                ]
            }
        },
        {
            'review': {
                'estimated_effort_to_review_[1-5]': '3, medium complexity in chunk 3',
                'score': '80',
                'security_concerns': 'No',
                'relevant_tests': 'No',
                'possible_issues': 'No',
                'key_issues_to_review': []
            }
        }
    ]

    # Test the merge function directly
    # We need to create a minimal PRReviewer-like object
    class MockReviewer:
        def _merge_review_chunks(self, chunks):
            # Import the actual implementation
            from pr_agent.tools.pr_reviewer import PRReviewer
            return PRReviewer._merge_review_chunks(self, chunks)

    mock = MockReviewer()
    merged = mock._merge_review_chunks(chunk_reviews)

    logger.info("Merge results:")
    logger.info(f"  Effort: {merged['review'].get('estimated_effort_to_review_[1-5]')}")
    logger.info(f"  Score: {merged['review'].get('score', 'NOT MERGED')}")
    logger.info(f"  Security: {merged['review'].get('security_concerns')}")
    logger.info(f"  Relevant tests: {merged['review'].get('relevant_tests')}")
    logger.info(f"  Possible issues: {merged['review'].get('possible_issues')}")
    logger.info(f"  Key issues count: {len(merged['review'].get('key_issues_to_review', []))}")

    # Validate expectations
    review = merged['review']

    assert '4' in review['estimated_effort_to_review_[1-5]'], "Effort should be max (4)"
    assert 'Yes' in review['security_concerns'], "Security should be Yes (OR logic)"
    assert review['relevant_tests'] == 'Yes', "Tests should be Yes (OR logic)"
    assert 'Performance' in review['possible_issues'], "Issues should include performance"
    assert len(review['key_issues_to_review']) == 2, f"Should have 2 unique issues, got {len(review['key_issues_to_review'])}"

    # Check if score was merged (it won't be yet - this tests current behavior)
    if 'score' not in review:
        logger.warning("EXPECTED: 'score' field not merged - needs implementation")
    else:
        logger.info(f"Score merged: {review['score']}")

    logger.info("Mock test completed successfully!")
    return True


def main():
    parser = argparse.ArgumentParser(description='Test multi-diff review mode')
    parser.add_argument('--pr-url', help='Gitea PR URL to test')
    parser.add_argument('--mock', action='store_true', help='Run with mock data (no LLM)')
    parser.add_argument('--api-base', default='http://127.0.0.1:8080/v1',
                        help='LLM API base URL')
    parser.add_argument('--api-key', default='dummy', help='API key')
    parser.add_argument('--model', default='local-qwen25-coder-7b',
                        help='Model name for pr-agent')
    parser.add_argument('--max-tokens', type=int, default=14000,
                        help='Max model tokens')
    parser.add_argument('--max-chunks', type=int, default=3,
                        help='Max diff chunks to process')

    args = parser.parse_args()

    if not args.pr_url and not args.mock:
        parser.error("Must specify either --pr-url or --mock")

    if args.mock:
        asyncio.run(test_with_mock())
    else:
        setup_settings(args)
        asyncio.run(test_with_real_pr(args.pr_url, args))


if __name__ == '__main__':
    main()
