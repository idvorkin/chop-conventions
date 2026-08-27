"""Unit tests for the adversarial-review machine pre-pass.

Pure functions only — no subprocess, no network, no filesystem beyond a tmpdir
for the link checker. Runs under system python3 via `just fast-test`.
"""

import tempfile
import unittest
from pathlib import Path

import prepass


class TestLeaks(unittest.TestCase):
    def rules(self, text, **kw):
        return [f["rule"] for f in prepass.scan_leaks(text, **kw)]

    def test_catches_tailscale_cgnat(self):
        self.assertIn("cgnat-ip", self.rules("ssh 100.64.12.34 works"))
        self.assertIn("cgnat-ip", self.rules("host 100.127.255.1"))

    def test_ignores_public_100_range_outside_cgnat(self):
        # 100.63.x and 100.128.x are NOT in 100.64.0.0/10.
        self.assertEqual([], self.rules("ip 100.63.0.1 and 100.128.0.1"))

    def test_catches_rfc1918_and_tailnet_host(self):
        self.assertIn("rfc1918-ip", self.rules("gateway 192.168.1.1"))
        self.assertIn("rfc1918-ip", self.rules("gateway 172.20.0.5"))
        self.assertIn("tailnet-host", self.rules("box.tail1234.ts.net"))

    def test_catches_credentials(self):
        self.assertIn("github-token", self.rules("ghp_" + "a" * 30))
        self.assertIn("anthropic-key", self.rules("sk-ant-" + "a" * 30))
        self.assertIn("secret-assignment", self.rules('api_key: "abcdefghijklmnop123"'))
        self.assertIn(
            "private-key-block", self.rules("-----BEGIN RSA PRIVATE KEY-----")
        )

    def test_severity_split(self):
        findings = prepass.scan_leaks("me@example.com at /home/developer/x")
        self.assertEqual({prepass.WARN}, {f["severity"] for f in findings})

    def test_allow_regex_suppresses(self):
        self.assertEqual([], self.rules("100.64.12.34", allow=[r"^100\.64\.12\.34$"]))

    def test_redaction_placeholder_is_not_a_leak(self):
        self.assertEqual([], self.rules("host <internal-host> and /home/<user>"))

    def test_reports_line_numbers(self):
        findings = prepass.scan_leaks("clean\nclean\n100.64.0.1\n")
        self.assertEqual(3, findings[0]["line"])

    def test_one_span_reports_once(self):
        # A token that also matches secret-assignment must not double-report.
        findings = prepass.scan_leaks("token = " + "gh" + "p_" + "b" * 30)
        spans = [f["line"] for f in findings]
        self.assertEqual(1, len(spans))


class TestNumbers(unittest.TestCase):
    def test_strips_ordered_list_markers(self):
        nums = [n for _, n in prepass.extract_numbers("12. a thing costing 45 dollars")]
        self.assertEqual(["45"], nums)

    def test_normalises_commas(self):
        nums = [n for _, n in prepass.extract_numbers("1,234 items")]
        self.assertEqual(["1234"], nums)

    def test_min_digits_drops_single_digits(self):
        nums = [n for _, n in prepass.extract_numbers("ran 7 passes over 42 files")]
        self.assertEqual(["42"], nums)

    def test_unsourced_number_flagged(self):
        findings = prepass.unsourced_numbers(
            "cut 17% of 900 words", "the cut was 17% of 800 words"
        )
        self.assertEqual(["900"], [f["text"] for f in findings])

    def test_sourced_numbers_are_silent(self):
        self.assertEqual([], prepass.unsourced_numbers("42 and 17", "42 17"))


class TestLinks(unittest.TestCase):
    def test_dead_anchor(self):
        text = "# Real Heading\n\nSee [x](#real-heading) and [y](#ghost)."
        findings = prepass.check_links(text, Path("."))
        self.assertEqual(["#ghost"], [f["text"] for f in findings])

    def test_slugify_matches_github_style(self):
        self.assertEqual("why-prose-not-code", prepass.slugify("Why prose, not code"))
        self.assertEqual("passes-2n", prepass.slugify("Passes 2..N"))

    def test_slugify_does_not_collapse_space_runs(self):
        # GitHub maps each space to its own hyphen: "A : B" -> "a--b".
        # Collapsing produced false dead-anchor reports on real posts.
        self.assertEqual(
            "strategy--defined-vs-emergent",
            prepass.slugify("Strategy : Defined vs Emergent"),
        )

    def test_slugify_uses_rendered_text_of_linked_headings(self):
        # GitHub slugs what the reader sees, not the raw markdown.
        self.assertEqual("be-curious", prepass.slugify("Be [Curious](/grandmother)"))
        self.assertEqual(
            "the-70-ai-coding-problem",
            prepass.slugify("[The 70% AI coding problem](https://x/y):"),
        )

    def test_duplicate_headings_get_github_suffixes(self):
        text = "## Conclusion\n## Conclusion\n## Conclusion\n"
        self.assertEqual(
            {"conclusion", "conclusion-1", "conclusion-2"},
            prepass.heading_anchors(text),
        )

    def test_repeated_heading_anchors_are_not_dead(self):
        text = "[a](#conclusion-1)\n\n## Conclusion\n\n## Conclusion\n"
        self.assertEqual([], prepass.check_links(text, Path(".")))

    def test_headings_inside_code_fences_are_not_anchors(self):
        self.assertEqual(set(), prepass.heading_anchors("```\n# Not A Heading\n```\n"))

    def test_extensionless_relative_route_is_skipped(self):
        # `td/data-systems` is a site route, not a file on disk.
        self.assertEqual([], prepass.check_links("[a](td/data-systems)", Path(".")))

    def test_site_absolute_links_are_skipped(self):
        # Jekyll permalinks resolve against a site root this tool can't know.
        self.assertEqual([], prepass.check_links("[a](/mortality-software)", Path(".")))

    def test_http_links_are_skipped(self):
        findings = prepass.check_links("[a](https://example.com/x)", Path("."))
        self.assertEqual([], findings)

    def test_local_file_link(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            (base / "there.md").write_text("hi")
            findings = prepass.check_links("[a](there.md) [b](gone.md)", base)
            self.assertEqual(["gone.md"], [f["text"] for f in findings])


class TestRubric(unittest.TestCase):
    RUBRIC = """
## Avoiding AI Writing Patterns

**❌ Undue emphasis phrases:**

- "stands as", "serves as"
- Instead: Be direct and specific

**❌ Editorializing phrases:**

- "it's important to note that", "notably"

**✅ Better approaches:**

- "use specific numbers" is good, not banned

## Other section

- "not a banned phrase either"
"""

    def test_extracts_only_banned_blocks(self):
        phrases = prepass.parse_rubric(self.RUBRIC)
        self.assertIn("stands as", phrases)
        self.assertIn("notably", phrases)
        self.assertNotIn("use specific numbers", phrases)
        self.assertNotIn("not a banned phrase either", phrases)

    def test_skips_instead_bullets(self):
        self.assertNotIn("Be direct and specific", prepass.parse_rubric(self.RUBRIC))

    def test_min_phrase_len_drops_pronouns(self):
        phrases = prepass.parse_rubric('- ❌ Switching between "I" and "we" mid-post')
        self.assertEqual([], phrases)

    def test_skips_fenced_code_examples(self):
        # A "wrong structure" code sample inside a ❌ block quotes strings that
        # illustrate layout, not phrasing. Regression: these leaked as phrases.
        rubric = '**❌ Wrong structure:**\n\n```markdown\ntitle: "Post Title"\n```\n\n- "banned phrase"\n'
        self.assertEqual(["banned phrase"], prepass.parse_rubric(rubric))

    def test_only_bullets_yield_phrases(self):
        rubric = (
            '**❌ x:**\n\nProse mentioning "not a bullet" here.\n\n- "is a bullet"\n'
        )
        self.assertEqual(["is a bullet"], prepass.parse_rubric(rubric))

    def test_dedupes_case_insensitively(self):
        phrases = prepass.parse_rubric('**❌ x:**\n- "Notably"\n- "notably"')
        self.assertEqual(["Notably"], phrases)

    def test_scan_is_word_bounded(self):
        # "notably" must not fire inside "unnotablyish".
        self.assertEqual([], prepass.scan_rubric("unnotablyish", ["notably"]))
        self.assertEqual(1, len(prepass.scan_rubric("Notably, it works.", ["notably"])))


class TestFrozen(unittest.TestCase):
    BASE = "---\ntitle: x\n---\n\n| a | b |\nWe measured 42 things over 17 days and it was quite good.\n"

    def rules(self, target, baseline=None):
        return {f["rule"] for f in prepass.check_frozen(baseline or self.BASE, target)}

    def test_clean_shorter_rewrite_passes(self):
        target = (
            "---\ntitle: x\n---\n\n| a | b |\nWe measured 42 things over 17 days.\n"
        )
        rules = self.rules(target)
        self.assertEqual({"word-count"}, rules)

    def test_dropped_number_flagged(self):
        target = "---\ntitle: x\n---\n\n| a | b |\nWe measured 42 things.\n"
        self.assertIn("number-dropped", self.rules(target))

    def test_changed_number_flags_both_directions(self):
        target = (
            "---\ntitle: x\n---\n\n| a | b |\nWe measured 43 things over 17 days.\n"
        )
        self.assertLessEqual({"number-dropped", "number-added"}, self.rules(target))

    def test_front_matter_and_table_must_be_identical(self):
        target = (
            "---\ntitle: y\n---\n\n| a | c |\nWe measured 42 things over 17 days.\n"
        )
        self.assertLessEqual(
            {"front-matter-changed", "table-changed"}, self.rules(target)
        )

    def test_word_count_must_go_down(self):
        target = self.BASE + "\nAnd then some more words were added here.\n"
        self.assertIn("word-count-not-reduced", self.rules(target))

    def test_code_blocks_must_be_identical(self):
        base = "```\nrun --this\n```\nand some prose words here to trim later\n"
        target = "```\nrun --that\n```\nand prose\n"
        self.assertIn("code-changed", self.rules(target, baseline=base))


class TestExitCodes(unittest.TestCase):
    def test_error_finding_exits_1(self):
        self.assertEqual(
            1, prepass.emit([{"severity": prepass.ERROR}], as_json=True, strict=False)
        )

    def test_warn_alone_exits_0_unless_strict(self):
        warn = [{"severity": prepass.WARN}]
        self.assertEqual(0, prepass.emit(warn, as_json=True, strict=False))
        self.assertEqual(1, prepass.emit(warn, as_json=True, strict=True))

    def test_info_is_not_a_finding(self):
        self.assertEqual(
            0, prepass.emit([{"severity": "info"}], as_json=True, strict=True)
        )


if __name__ == "__main__":
    unittest.main()
