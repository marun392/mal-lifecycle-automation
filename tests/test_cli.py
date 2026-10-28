from __future__ import annotations

import json

from mal_lifecycle.cli import main


def test_demo_runs_end_to_end(tmp_path, capsys):
    out = tmp_path / "result.json"
    code = main(["--env-file", str(tmp_path / "missing.env"), "--json", str(out), "demo"])
    assert code == 0
    results = json.loads(out.read_text())
    assert [r["workflow"] for r in results] == ["onboarding", "offboarding"]
    assert all(r["ok"] for r in results)
    captured = capsys.readouterr()
    assert "ONBOARDING ada.lovelace@example.com -> OK" in captured.out
    assert "OFFBOARDING bob.departing@example.com -> OK" in captured.out


def test_offboard_unknown_user_exits_nonzero(tmp_path, capsys):
    code = main(
        ["--mock", "--env-file", str(tmp_path / "none.env"), "offboard", "--email", "ghost@example.com"]
    )
    assert code == 1
    assert "FAILURES" in capsys.readouterr().out


def test_onboard_custom_group(tmp_path, capsys):
    code = main(
        [
            "--mock",
            "--env-file",
            str(tmp_path / "none.env"),
            "onboard",
            "--first",
            "Grace",
            "--last",
            "Hopper",
            "--email",
            "grace.hopper@example.com",
            "--group",
            "finance",
        ]
    )
    assert code == 0
    assert "added to 'finance'" in capsys.readouterr().out
