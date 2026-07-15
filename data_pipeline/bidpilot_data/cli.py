from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich import print

from bidpilot_data.logging import setup_logging

app = typer.Typer(help="BidPilot data pipeline CLI", no_args_is_help=True)
label_app = typer.Typer(help="Labeling commands")
review_app = typer.Typer(help="Human review commands")
db_app = typer.Typer(help="Database import commands")
app.add_typer(label_app, name="label")
app.add_typer(review_app, name="review")
app.add_typer(db_app, name="db")


def _setup(verbose: bool = False) -> None:
    setup_logging("DEBUG" if verbose else "INFO")


@app.command("bootstrap")
def bootstrap(dry_run: bool = False, verbose: bool = False) -> None:
    """Bootstrap isolated demo fixture under datasets/fixtures/demo (not formal training data)."""
    _setup(verbose)
    from bidpilot_data.bootstrap import bootstrap_from_demo

    print(bootstrap_from_demo(dry_run=dry_run))


@app.command("discover")
def discover(
    province: str = typer.Option("广东", help="Province focus"),
    keywords: list[str] = typer.Option(
        None,
        "--keywords",
        help="Discovery keywords; repeatable",
    ),
    start_date: Optional[str] = typer.Option(None, help="YYYY-MM-DD"),
    end_date: Optional[str] = typer.Option(None, help="YYYY-MM-DD"),
    target_projects: int = typer.Option(10, help="Target real projects for this batch"),
    max_list_pages: int = typer.Option(25, help="Max CCGP list pages per category"),
    dry_run: bool = False,
    verbose: bool = False,
) -> None:
    """Discover and download real official public procurement projects."""
    _setup(verbose)
    from bidpilot_data.collectors import discover_and_collect

    kw = keywords or ["信息化", "软件", "运维", "数据治理", "网络安全"]
    print(
        discover_and_collect(
            province=province,
            keywords=kw,
            start_date=start_date,
            end_date=end_date,
            target_projects=target_projects,
            max_list_pages=max_list_pages,
            dry_run=dry_run,
        )
    )


@app.command("collect")
def collect(
    manifest: Path = typer.Option(..., exists=True, dir_okay=False),
    dry_run: bool = False,
    resume: bool = True,
    verbose: bool = False,
) -> None:
    """Collect from seed manifest. Prefer official_source_seeds.jsonl over demo fixtures."""
    _setup(verbose)
    text = manifest.read_text(encoding="utf-8", errors="ignore")
    # Seed manifests use source_url/url rows for official notices.
    if "official_source" in manifest.name or '"source_url": "https://' in text or '"url": "https://' in text:
        from bidpilot_data.collectors import collect_from_seed_manifest

        print(collect_from_seed_manifest(str(manifest), dry_run=dry_run))
        return
    from bidpilot_data.collectors import collect_from_manifest

    print(collect_from_manifest(manifest, dry_run=dry_run, resume=resume))


@app.command("download")
def download(resume: bool = True, dry_run: bool = False, verbose: bool = False) -> None:
    _setup(verbose)
    from bidpilot_data.collectors import download_pending

    print(download_pending(resume=resume, dry_run=dry_run))


@app.command("deduplicate")
def deduplicate(verbose: bool = False) -> None:
    _setup(verbose)
    from bidpilot_data.collectors import deduplicate_raw

    print(deduplicate_raw())


@app.command("parse")
def parse(resume: bool = True, dry_run: bool = False, verbose: bool = False) -> None:
    _setup(verbose)
    from bidpilot_data.parsers import parse_documents

    print(parse_documents(resume=resume, dry_run=dry_run))


@app.command("clean")
def clean(resume: bool = True, dry_run: bool = False, verbose: bool = False) -> None:
    _setup(verbose)
    from bidpilot_data.cleaning import clean_parsed_documents

    print(clean_parsed_documents(resume=resume, dry_run=dry_run))


@app.command("chunk")
def chunk(resume: bool = True, dry_run: bool = False, verbose: bool = False) -> None:
    _setup(verbose)
    from bidpilot_data.chunking import chunk_documents

    print(chunk_documents(resume=resume, dry_run=dry_run))


@label_app.command("requirements")
def label_requirements_cmd(
    mode: str = typer.Option("rules", help="rules|llm"),
    resume: bool = True,
    dry_run: bool = False,
    verbose: bool = False,
) -> None:
    _setup(verbose)
    from bidpilot_data.labeling import label_requirements

    print(label_requirements(mode=mode, resume=resume, dry_run=dry_run))


@label_app.command("matches")
def label_matches(dry_run: bool = False, verbose: bool = False) -> None:
    """Build evidence-bound matches from disclosed official filings only."""
    _setup(verbose)
    from bidpilot_data.labeling.disclosed_matches import build_disclosed_matches

    print(build_disclosed_matches(dry_run=dry_run))


@label_app.command("synthetic")
def label_synthetic(dry_run: bool = False, verbose: bool = False) -> None:
    """Disabled. Synthetic companies/materials are forbidden."""
    _setup(verbose)
    from bidpilot_data.labeling.synthetic_companies import build_synthetic_companies_and_matches

    try:
        print(build_synthetic_companies_and_matches(dry_run=dry_run))
    except Exception as exc:  # noqa: BLE001
        print({"ok": False, "error": str(exc)})
        raise typer.Exit(code=2)


@review_app.command("export")
def review_export(output: Optional[Path] = None, verbose: bool = False) -> None:
    _setup(verbose)
    from bidpilot_data.review import export_review_csv

    print(export_review_csv(output))


@review_app.command("import")
def review_import(
    file: Path = typer.Option(..., exists=True, dir_okay=False),
    dry_run: bool = False,
    verbose: bool = False,
) -> None:
    _setup(verbose)
    from bidpilot_data.review import import_review_csv

    print(import_review_csv(file, dry_run=dry_run))


@app.command("build-rag")
def build_rag(dry_run: bool = False, limit: int = 40, verbose: bool = False) -> None:
    _setup(verbose)
    from bidpilot_data.rag_eval import build_rag_eval

    print(build_rag_eval(dry_run=dry_run, limit=limit))


@app.command("build-agent")
def build_agent(dry_run: bool = False, limit: int = 36, verbose: bool = False) -> None:
    _setup(verbose)
    from bidpilot_data.agent_data import build_agent_tasks

    print(build_agent_tasks(dry_run=dry_run, limit=limit))


@app.command("build-sft")
def build_sft(dry_run: bool = False, verbose: bool = False) -> None:
    _setup(verbose)
    from bidpilot_data.sft import build_sft_dataset

    print(build_sft_dataset(dry_run=dry_run))


@app.command("validate")
def validate(target: str = typer.Argument("all"), verbose: bool = False) -> None:
    _setup(verbose)
    from bidpilot_data.validation import validate_all

    if target != "all":
        raise typer.BadParameter("only 'all' is supported currently")
    report = validate_all()
    print(report)
    if not report.get("ok"):
        raise typer.Exit(code=1)


@app.command("report")
def report(verbose: bool = False) -> None:
    _setup(verbose)
    from bidpilot_data.reporting import build_reports

    print(build_reports())


@app.command("run-demo")
def run_demo(dry_run: bool = False, verbose: bool = False) -> None:
    """Fixture-only demo pipeline. Writes under datasets/fixtures/demo, never formal train sets."""
    _setup(verbose)
    from bidpilot_data.bootstrap import bootstrap_from_demo, use_demo_fixture_root
    from bidpilot_data.chunking import chunk_documents
    from bidpilot_data.cleaning import clean_parsed_documents
    from bidpilot_data.labeling import label_requirements
    from bidpilot_data.parsers import parse_documents
    from bidpilot_data.rag_eval import build_rag_eval
    from bidpilot_data.agent_data import build_agent_tasks
    from bidpilot_data.review import export_review_csv
    from bidpilot_data.sft import build_sft_dataset
    from bidpilot_data.validation import validate_all
    from bidpilot_data.reporting import build_reports

    with use_demo_fixture_root():
        steps = {}
        steps["bootstrap"] = bootstrap_from_demo(dry_run=dry_run)
        if not dry_run:
            steps["parse"] = parse_documents(resume=False)
            steps["clean"] = clean_parsed_documents(resume=False)
            steps["chunk"] = chunk_documents(resume=False)
            steps["label"] = label_requirements(mode="rules", resume=False)
            steps["review_export"] = export_review_csv()
            steps["rag"] = build_rag_eval(limit=30)
            steps["agent"] = build_agent_tasks(limit=24)
            steps["sft"] = build_sft_dataset()
            steps["validate"] = validate_all(allow_demo_fixture=True)
            steps["report"] = build_reports()
        print(steps)
        if not dry_run and steps.get("validate") and not steps["validate"].get("ok"):
            raise typer.Exit(code=1)


@app.command("run-real-mini")
def run_real_mini(
    target_projects: int = 10,
    dry_run: bool = False,
    verbose: bool = False,
) -> None:
    """Discover/download a Guangdong mini-batch, then parse/chunk/label/evidence/validate."""
    _setup(verbose)
    from bidpilot_data.collectors import discover_and_collect
    from bidpilot_data.parsers import parse_documents
    from bidpilot_data.cleaning import clean_parsed_documents
    from bidpilot_data.chunking import chunk_documents
    from bidpilot_data.labeling import label_requirements
    from bidpilot_data.labeling.disclosed_matches import build_disclosed_matches
    from bidpilot_data.rag_eval import build_rag_eval
    from bidpilot_data.sft import build_sft_dataset
    from bidpilot_data.review import export_review_csv
    from bidpilot_data.validation import validate_all
    from bidpilot_data.reporting import build_reports

    steps = {
        "discover": discover_and_collect(
            province="广东",
            keywords=["信息化", "软件", "运维", "数据治理", "网络安全", "信息系统", "机房", "数据中心"],
            start_date="2023-01-01",
            end_date="2026-12-31",
            target_projects=target_projects,
            max_list_pages=24,
            dry_run=dry_run,
        )
    }
    if not dry_run:
        steps["parse"] = parse_documents(resume=False)
        steps["clean"] = clean_parsed_documents(resume=False)
        steps["chunk"] = chunk_documents(resume=False)
        steps["label"] = label_requirements(mode="rules", resume=False)
        steps["matches"] = build_disclosed_matches()
        steps["rag"] = build_rag_eval(limit=40)
        steps["sft"] = build_sft_dataset()
        steps["review_export"] = export_review_csv()
        steps["validate"] = validate_all()
        steps["report"] = build_reports()
    print(steps)
    if not dry_run and steps.get("validate") and not steps["validate"].get("ok"):
        raise typer.Exit(code=1)


@db_app.command("import-documents")
def db_import_documents(dry_run: bool = False, verbose: bool = False) -> None:
    _setup(verbose)
    from bidpilot_data.database import import_documents

    print(import_documents(dry_run=dry_run))


@db_app.command("import-chunks")
def db_import_chunks(dry_run: bool = False, verbose: bool = False) -> None:
    _setup(verbose)
    from bidpilot_data.database import import_chunks

    print(import_chunks(dry_run=dry_run))


@db_app.command("import-requirements")
def db_import_requirements(dry_run: bool = False, verbose: bool = False) -> None:
    _setup(verbose)
    from bidpilot_data.database import import_requirements

    print(import_requirements(dry_run=dry_run))


@db_app.command("import-company-profiles")
def db_import_company_profiles(dry_run: bool = False, verbose: bool = False) -> None:
    _setup(verbose)
    from bidpilot_data.database import import_company_profiles

    print(import_company_profiles(dry_run=dry_run))


@db_app.command("import-matches")
def db_import_matches(dry_run: bool = False, verbose: bool = False) -> None:
    _setup(verbose)
    from bidpilot_data.database import import_matches

    print(import_matches(dry_run=dry_run))


if __name__ == "__main__":
    app()
