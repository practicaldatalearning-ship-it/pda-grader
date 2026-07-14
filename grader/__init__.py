"""pda-grader — free, no-server auto-grader for heavy ML/DS notebook assignments.

Runs in GitHub Actions: claim queued submissions from Supabase, execute each in a
locked-down Docker sandbox, grade every answer cell by its tag, write results back.
No secret ever lives in this package — all credentials come from the environment.
"""

__version__ = "0.1.0"
