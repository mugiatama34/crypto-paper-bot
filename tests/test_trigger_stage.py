"""`scripts/trigger_stage.py`: tek seferlik bir aşama başka bir daldan TAŞINAMAZ.

Testler gerçek bir git deposu kurar (bare `origin` + klon) ve Actions'ın gördüğü durumu
üretir: push edilmiş dal, `origin/*` uzak referansları, `github.event.before`. Taklit bir
git katmanı, kapatılmak istenen hatayı (merge'ün getirdiği dosyanın "eklenmiş" görünmesi)
üretemezdi.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.trigger_stage import ZERO_SHA, main, resolve

STAGES = (("preflight", "regime-preflight*.run"), ("measure", "regime-measure*.run"))


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


class Repo:
    def __init__(self, tmp: Path) -> None:
        self.origin = tmp / "origin.git"
        self.path = tmp / "work"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.origin)], check=True)
        subprocess.run(["git", "clone", "-q", str(self.origin), str(self.path)], check=True,
                       capture_output=True)
        for key, value in (("user.name", "t"), ("user.email", "t@t"),
                           ("commit.gpgsign", "false")):
            _git(self.path, "config", key, value)
        _git(self.path, "checkout", "-q", "-b", "main")
        self.commit("README", "x")
        self.push("main")

    def commit(self, name: str, text: str = "1") -> str:
        target = self.path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        _git(self.path, "add", name)
        _git(self.path, "commit", "-q", "-m", f"add {name}")
        return self.head()

    def trigger(self, name: str) -> str:
        return self.commit(f".github/triggers/{name}")

    def head(self) -> str:
        return _git(self.path, "rev-parse", "HEAD")

    def push(self, branch: str) -> None:
        _git(self.path, "push", "-q", "origin", f"HEAD:refs/heads/{branch}")
        _git(self.path, "fetch", "-q", "origin")

    def resolve(self, *, before: str, branch: str):
        return resolve(self.path, before=before, sha=self.head(), branch=branch, stages=STAGES)


@pytest.fixture()
def repo(tmp_path: Path) -> Repo:
    return Repo(tmp_path)


def test_own_trigger_starts_its_stage(repo: Repo) -> None:
    _git(repo.path, "checkout", "-q", "-b", "claude/x")
    repo.push("claude/x")
    before = repo.head()
    repo.trigger("regime-preflight.run")
    repo.push("claude/x")
    result = repo.resolve(before=before, branch="claude/x")
    assert result.stage == "preflight" and result.error is None
    assert not result.carried


def test_a_single_trigger_carried_from_main_does_NOT_run(repo: Repo) -> None:
    """Kapatılan açığın kendisi: main'e eklenmiş TEK bir tetikleyici merge ile gelir."""
    _git(repo.path, "checkout", "-q", "-b", "claude/x")
    repo.commit("work.txt")
    repo.push("claude/x")
    _git(repo.path, "checkout", "-q", "main")
    repo.trigger("regime-measure.run")        # başka bir dalda koşmuş, main'e birleşmiş
    repo.push("main")
    _git(repo.path, "checkout", "-q", "claude/x")
    before = repo.head()
    _git(repo.path, "merge", "-q", "--no-edit", "main")
    repo.push("claude/x")

    result = repo.resolve(before=before, branch="claude/x")
    assert result.stage is None
    assert result.error is None                # kırmızı DEĞİL: doğru davranış
    assert result.carried == {".github/triggers/regime-measure.run": ("origin/main",)}


def test_carried_trigger_from_another_branch_is_also_ignored(repo: Repo) -> None:
    _git(repo.path, "checkout", "-q", "-b", "claude/a")
    repo.trigger("regime-measure.run")
    repo.push("claude/a")
    _git(repo.path, "checkout", "-q", "main")
    _git(repo.path, "checkout", "-q", "-b", "claude/b")
    repo.push("claude/b")
    before = repo.head()
    _git(repo.path, "merge", "-q", "--no-edit", "claude/a")
    repo.push("claude/b")
    result = repo.resolve(before=before, branch="claude/b")
    assert result.stage is None and result.error is None
    assert "origin/claude/a" in result.carried[".github/triggers/regime-measure.run"]


def test_own_trigger_wins_over_carried_ones_in_the_same_push(repo: Repo) -> None:
    _git(repo.path, "checkout", "-q", "-b", "claude/x")
    repo.push("claude/x")
    before = repo.head()
    _git(repo.path, "checkout", "-q", "main")
    repo.trigger("regime-measure.run")
    repo.push("main")
    _git(repo.path, "checkout", "-q", "claude/x")
    _git(repo.path, "merge", "-q", "--no-edit", "main")
    repo.trigger("regime-preflight-2.run")
    repo.push("claude/x")
    result = repo.resolve(before=before, branch="claude/x")
    assert result.stage == "preflight"
    assert ".github/triggers/regime-measure.run" in result.carried


def test_two_own_stages_in_one_push_is_an_error(repo: Repo) -> None:
    _git(repo.path, "checkout", "-q", "-b", "claude/x")
    repo.push("claude/x")
    before = repo.head()
    repo.trigger("regime-preflight.run")
    repo.trigger("regime-measure.run")
    repo.push("claude/x")
    result = repo.resolve(before=before, branch="claude/x")
    assert result.stage is None and "birden çok aşama" in (result.error or "")


def test_modified_trigger_is_not_an_addition(repo: Repo) -> None:
    _git(repo.path, "checkout", "-q", "-b", "claude/x")
    repo.trigger("regime-preflight.run")
    repo.push("claude/x")
    before = repo.head()
    repo.commit(".github/triggers/regime-preflight.run", "değişti")
    repo.push("claude/x")
    result = repo.resolve(before=before, branch="claude/x")
    assert result.stage is None and "EKLENMEDİ" in (result.error or "")


def test_new_branch_counts_only_its_head_commit(repo: Repo) -> None:
    _git(repo.path, "checkout", "-q", "-b", "claude/x")
    repo.trigger("regime-preflight.run")
    repo.push("claude/x")
    result = repo.resolve(before=ZERO_SHA, branch="claude/x")
    assert result.stage == "preflight"


def test_new_branch_whose_head_is_a_merge_starts_nothing(repo: Repo) -> None:
    _git(repo.path, "checkout", "-q", "-b", "claude/a")
    repo.trigger("regime-measure.run")
    repo.push("claude/a")
    _git(repo.path, "checkout", "-q", "main")
    repo.commit("other.txt")
    _git(repo.path, "checkout", "-q", "-b", "claude/new")
    _git(repo.path, "merge", "-q", "--no-edit", "--no-ff", "claude/a")
    repo.push("claude/new")
    result = repo.resolve(before=ZERO_SHA, branch="claude/new")
    assert result.stage is None and result.error is not None


def test_cli_writes_the_stage_to_github_output(repo: Repo, tmp_path: Path,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    _git(repo.path, "checkout", "-q", "-b", "claude/x")
    repo.push("claude/x")
    before = repo.head()
    repo.trigger("regime-measure.run")
    repo.push("claude/x")
    out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    code = main(["--repo", str(repo.path), "--before", before, "--sha", repo.head(),
                 "--branch", "claude/x", "--stage", "preflight=regime-preflight*.run",
                 "--stage", "measure=regime-measure*.run"])
    assert code == 0
    assert out.read_text() == "stage=measure\n"


def test_cli_carried_only_is_green_with_an_empty_stage(repo: Repo, tmp_path: Path,
                                                       monkeypatch: pytest.MonkeyPatch,
                                                       capsys: pytest.CaptureFixture[str]) -> None:
    _git(repo.path, "checkout", "-q", "-b", "claude/x")
    repo.push("claude/x")
    before = repo.head()
    _git(repo.path, "checkout", "-q", "main")
    repo.trigger("regime-measure.run")
    repo.push("main")
    _git(repo.path, "checkout", "-q", "claude/x")
    _git(repo.path, "merge", "-q", "--no-edit", "main")
    repo.push("claude/x")
    out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    code = main(["--repo", str(repo.path), "--before", before, "--sha", repo.head(),
                 "--branch", "claude/x", "--stage", "measure=regime-measure*.run"])
    assert code == 0
    assert out.read_text() == "stage=\n"
    assert "::notice::TAŞINMIŞ" in capsys.readouterr().out


def test_every_push_triggered_workflow_resolves_through_this_script() -> None:
    """Kapı: `.github/triggers/`e push ile dinleyen HER workflow bu betikten geçer.

    Liste workflow dosyalarından okunur, elle yazılmaz — yeni bir ölçüm workflow'u eski kabuk
    kalıbını kopyalarsa (aralıkta "eklenen" dosyaya bakan), taşınan tetikleyici açığı geri gelir.
    """
    import yaml

    from core.config import PROJECT_ROOT

    listening = []
    for path in sorted((PROJECT_ROOT / ".github" / "workflows").glob("*.yml")):
        spec = yaml.safe_load(path.read_text(encoding="utf-8"))
        on = spec.get("on", spec.get(True, {})) or {}
        push = on.get("push") if isinstance(on, dict) else None
        paths = (push or {}).get("paths", []) if isinstance(push, dict) else []
        if any(p.startswith(".github/triggers/") for p in paths):
            listening.append(path)
            text = path.read_text(encoding="utf-8")
            assert "scripts/trigger_stage.py" in text, path.name
            assert "--diff-filter=A" not in text, f"{path.name}: eski kabuk kalıbı duruyor"
    assert len(listening) >= 4, listening
