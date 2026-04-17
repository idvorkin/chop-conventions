"""chop_bulk — bulk-parallel CLIs for Claude Code sessions.

One tool call firing N parallel sub-calls beats N sequential calls on
wall-clock, and it keeps main-thread context cleaner. Console scripts
(see `pyproject.toml`):

    bulk-gh-pr-details  -> chop_bulk.gh_pr_details:main
    bulk-gh-prs-open    -> chop_bulk.gh_prs_open:main
    bulk-bd-show        -> chop_bulk.bd_show:main
    bulk-up-to-date     -> chop_bulk.up_to_date:main
    bulk-file-read      -> chop_bulk.file_read:main
"""
