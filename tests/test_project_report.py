from scripts import project_report as report


def test_the_report_is_built_from_the_numbers_it_is_given():
    stats = {
        "commits": 10, "first_commit": "2026-05-03", "src_lines": 1234, "test_lines": 567, "test_files": 3,
        "posts": [("instagram", 2, "2026-09-22 10:00:00", "2026-10-23 10:00:00")],
        "queue": [("published", 2)],
        "usage_days": [("2026-10-08", 34, 0.14), ("2026-10-09", 71, 0.47)],
        "runs": (5, 3, 0.04),
        "retries": [("CLAIM_NUMBER_UNSUPPORTED", 7)],
        "regression": [("20261018-153509", 11, 0.636, 1.098, 36)],
    }
    text = report.render(stats)
    assert "커밋 10개" in text and "소스 1,234줄" in text
    assert "instagram: 2건" in text and "합계 $0.61" in text
    assert "CLAIM_NUMBER_UNSUPPORTED: 7회" in text and "성공률 64%" in text
    assert "성공률로 읽지 마세요" in text                    # run statuses include cancelled/regression runs


def test_a_missing_database_yields_an_empty_section_not_a_crash(tmp_path):
    assert report._rows(tmp_path / "missing.db", "select 1") == []
