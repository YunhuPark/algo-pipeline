from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_powershell_startup_discovers_project_python_and_checks_ports():
    source = (ROOT / "start_services.ps1").read_text(encoding="utf-8")

    assert "C:\\Python313" not in source
    assert "venv\\Scripts\\python.exe" in source
    assert "Get-NetTCPConnection" in source
    assert "$scriptArgument" in source
    assert "throw \"$Name 시작 실패" in source


def test_dashboard_loads_dotenv_before_database_import():
    source = (ROOT / "src" / "dashboard" / "app.py").read_text(encoding="utf-8")

    dotenv_load = 'load_dotenv(ROOT / ".env", override=False)'
    database_import = "from src.db import ("

    assert dotenv_load in source
    assert database_import in source
    assert source.index(dotenv_load) < source.index(database_import)


def test_batch_startup_uses_repository_relative_path():
    source = (ROOT / "start_services.bat").read_text(encoding="utf-8")

    assert 'cd /d "%~dp0"' in source
    assert "C:\\projects\\cardnews" not in source


def test_metadata_git_push_is_opt_in():
    source = (ROOT / "scripts" / "export_meta.py").read_text(encoding="utf-8")

    assert "AUTO_EXPORT_META_GIT_PUSH" in source
    assert '["git", "push", "origin", "HEAD"]' in source


def test_weekly_review_is_scheduled_as_a_non_publishing_job():
    scheduler = (ROOT / "src" / "scheduler.py").read_text(encoding="utf-8")
    review = (ROOT / "src" / "analytics" / "weekly_review.py").read_text(
        encoding="utf-8"
    )

    assert 'id="weekly_quality_review"' in scheduler
    assert "--quality-review" in scheduler
    assert "job_weekly_quality_review" in scheduler
    assert "ig_publisher" not in review
    assert "activate_policy(" not in review


def test_weekly_insights_run_before_quality_review():
    scheduler = (ROOT / "src" / "scheduler.py").read_text(encoding="utf-8")

    assert 'WEEKLY_INSIGHTS_HOUR = int(os.getenv("WEEKLY_INSIGHTS_HOUR", "8"))' in scheduler
    assert 'WEEKLY_REVIEW_HOUR = int(os.getenv("WEEKLY_REVIEW_HOUR", "9"))' in scheduler
    assert scheduler.index('id="weekly_analysis"') < scheduler.index('id="weekly_quality_review"')


def test_brand_template_is_the_pipeline_default():
    templates = (ROOT / "src" / "agents" / "design_templates.py").read_text(
        encoding="utf-8"
    )
    pipeline = (ROOT / "src" / "pipeline.py").read_text(encoding="utf-8")

    assert '"brand": {' in templates
    assert 'os.getenv("DEFAULT_CARD_TEMPLATE", "brand")' in pipeline
