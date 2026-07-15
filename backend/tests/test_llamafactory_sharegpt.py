import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TRAINING = ROOT / "training" / "llamafactory"
EXPORT = TRAINING / "scripts" / "export_sft_dataset.py"
VALIDATE = TRAINING / "scripts" / "validate_sft_dataset.py"
SAMPLE = TRAINING / "data" / "sample_sharegpt.json"
DATASET_INFO = TRAINING / "data" / "dataset_info.json"


def test_sample_sharegpt_format_and_dataset_info():
    result = subprocess.run(
        [
            sys.executable,
            str(VALIDATE),
            "--dataset-file",
            str(SAMPLE),
            "--dataset-info",
            str(DATASET_INFO),
            "--dataset-name",
            "bidpilot_sample_sharegpt",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["records"] >= 1


def test_export_prevents_train_test_project_leakage():
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        source = out / "source.json"
        source.write_text(
            json.dumps(
                [
                    {
                        "messages": [
                            {"role": "system", "content": "sys"},
                            {"role": "user", "content": "u1"},
                            {"role": "assistant", "content": '{"category":"qualification"}'},
                        ],
                        "project_id": "proj-train-a",
                        "task_type": "requirement_classify",
                    },
                    {
                        "messages": [
                            {"role": "user", "content": "u2"},
                            {"role": "assistant", "content": '{"category":"deadline"}'},
                        ],
                        "project_id": "proj-test-b",
                        "task_type": "requirement_classify",
                        "is_test_project": True,
                    },
                    {
                        "messages": [
                            {"role": "user", "content": "u3"},
                            {"role": "assistant", "content": '{"category":"scoring"}'},
                        ],
                        "project_id": "proj-train-c",
                        "task_type": "requirement_classify",
                    },
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        result = subprocess.run(
            [
                sys.executable,
                str(EXPORT),
                "--input",
                str(source),
                "--output-dir",
                str(out / "exported"),
                "--task-type",
                "requirement_classify",
                "--require-json-assistant",
                "--train-ratio",
                "0.5",
                "--val-ratio",
                "0.0",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        stats = json.loads(result.stdout)
        train_projects = set(stats["train_projects"])
        test_projects = set(stats["test_projects"])
        assert train_projects.isdisjoint(test_projects)
        assert "proj-test-b" in test_projects
        assert "proj-test-b" not in train_projects
