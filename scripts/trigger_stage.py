"""Tetikleyici dosyadan AŞAMA çözümü: tek seferlik bir aşama başka bir daldan TAŞINAMAZ.

`claude/**` dallarında dinleyen ölçüm workflow'ları (`measure-market-direction`,
`measure-regime`, `measure-timesfm`, `backtest-dc`) aşamayı, push aralığında EKLENEN
`.github/triggers/*.run` dosyasının adından okur. Eski kural "aralıkta eklenen dosya"ya
bakıyordu ve bir açığı vardı (ölçüldü, PR #57): `main`i bir `claude/**` dalına birleştirmek,
`main`e o arada eklenmiş tetikleyicileri bu dala da "eklenmiş" gösterir. Üç dosya birlikte
geldiği için kapı o gün "birden çok aşama" diyerek reddetti; TEK dosya gelseydi `measure`
gibi TEK SEFERLİK bir aşama başka bir dalda İKİNCİ KEZ koşardı — ön-kayıt disiplininin
(§7: "sonucu görüp tekrar koşma") sessizce delinmesi.

**Kural:** bir tetikleyici ancak onu ekleyen commit (merge commit'leri hariç) push'un
aralığındaysa VE hiçbir BAŞKA uzak dalda bulunmuyorsa sayılır. `origin/main`de ya da başka
bir dalda da duran bir commit'in eklediği dosya TAŞINMIŞTIR: aşama başlatmaz, bir `notice`
ile adı ve onu taşıyan dallar yazılır. Tetikleyicinin kimliği dosyanın yolu değil, onu
ekleyen commit'tir — "dosyanın EKLENDİĞİ commit koşunun kendisidir" sözünün kelimesi kelimesine
uygulanması.

Liste git'ten çıkarılır, olay yükünden değil: push olayının `head_commit.added` alanı boş
geliyor (`measure-timesfm` #35853769262 bu yüzden hiçbir şey hesaplamadan durdu).

Çıktı ve çıkış kodları (her workflow aynı sözleşmeyi okur):

- tek aşama → `stage=<ad>` (GITHUB_OUTPUT'a da yazılır), çıkış 0;
- yalnızca TAŞINMIŞ tetikleyici → `stage=` (boş), `::notice`, çıkış 0 — bu doğru davranıştır,
  kırmızı değildir: kırmızı her merge'de gerçek bir arızanın görünürlüğünü tüketirdi;
- hiç tetikleyici eklenmemiş (ör. değiştirilmiş) ya da birden çok aşama → `::error`, çıkış 1
  (eski kuralın aynısı — "koşu yok" sessiz olamaz);
- kullanım hatası → çıkış 2.

Salt okunur: yalnızca `git` okur, hiçbir şey yazmaz (GITHUB_OUTPUT hariç). Ölçümün parçası
değildir ve hiçbir modelin davranışına dokunmaz.
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

# Öteki betiklerin kalıbı (tests/test_script_entrypoints.py): `core` ithal edilmese de tek kalıp.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TRIGGER_DIR = ".github/triggers/"
ZERO_SHA = "0" * 40


@dataclass(frozen=True)
class Resolution:
    stage: str | None
    own: tuple[str, ...] = ()
    carried: dict[str, tuple[str, ...]] = field(default_factory=dict)
    error: str | None = None


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


def _added_by(repo: Path, commit: str) -> list[str]:
    out = _git(
        repo, "diff-tree", "--no-commit-id", "--name-only", "--diff-filter=A", "-r",
        commit, "--", TRIGGER_DIR,
    )
    return [line for line in out.splitlines() if line]


def _push_commits(repo: Path, before: str, sha: str) -> list[str]:
    """Push'un GETİRDİĞİ merge-dışı commit'ler.

    Yeni dal (before boş/sıfır) için yalnızca baş commit — eski kuralın aynısı: dalın tüm
    geçmişini taramak, dal açılırken miras alınan her tetikleyiciyi yeniden sayardı.
    Merge commit'leri hiçbir koşulda tetikleyici sayılmaz; birleştirilen taraftaki
    commit'ler ise aralıkta görünür ve aşağıdaki dal kuralına takılır.
    """
    if not before or before == ZERO_SHA:
        parents = _git(repo, "rev-list", "--parents", "-n", "1", sha).split()
        return [sha] if len(parents) <= 2 else []
    out = _git(repo, "rev-list", "--no-merges", f"{before}..{sha}")
    return [line for line in out.splitlines() if line]


def _other_branches(repo: Path, commit: str, *, remote: str, branch: str) -> tuple[str, ...]:
    out = _git(repo, "branch", "-r", "--contains", commit, "--format=%(refname:short)")
    own = f"{remote}/{branch}"
    return tuple(
        ref for ref in (line.strip() for line in out.splitlines())
        if ref and ref != own and ref.startswith(f"{remote}/") and ref != f"{remote}/HEAD"
    )


def resolve(
    repo: Path,
    *,
    before: str,
    sha: str,
    branch: str,
    stages: Sequence[tuple[str, str]],
    remote: str = "origin",
) -> Resolution:
    """Push aralığındaki tetikleyicilerden aşamayı çözer (bkz. modül docstring'i)."""
    own: list[str] = []
    carried: dict[str, tuple[str, ...]] = {}
    for commit in _push_commits(repo, before, sha):
        for path in _added_by(repo, commit):
            elsewhere = _other_branches(repo, commit, remote=remote, branch=branch)
            if elsewhere:
                carried[path] = elsewhere
            else:
                own.append(path)

    matched = sorted({
        name for path in own for name, pattern in stages
        if fnmatch.fnmatch(Path(path).name, pattern)
    })
    if len(matched) > 1:
        return Resolution(None, tuple(own), carried,
                          f"aynı push'ta birden çok aşama eklendi ({', '.join(matched)}) — koşu yok")
    if matched:
        return Resolution(matched[0], tuple(own), carried)
    if carried:
        return Resolution(None, tuple(own), carried)
    return Resolution(None, tuple(own), carried,
                      "bu push'ta tetikleyici dosya EKLENMEDİ (değiştirilmiş olabilir) — koşu yok")


def _stage_arg(text: str) -> tuple[str, str]:
    name, sep, pattern = text.partition("=")
    if not sep or not name or not pattern:
        raise argparse.ArgumentTypeError(f"--stage AD=DESEN bekleniyordu: {text!r}")
    return name, pattern


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--before", default="", help="github.event.before")
    parser.add_argument("--sha", required=True, help="github.sha")
    parser.add_argument("--branch", required=True, help="github.ref_name")
    parser.add_argument("--stage", action="append", type=_stage_arg, required=True,
                        help="AD=DOSYA_DESENİ (ör. measure=regime-measure*.run); tekrarlanır")
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--repo", default=".")
    args = parser.parse_args(argv)

    result = resolve(
        Path(args.repo), before=args.before, sha=args.sha, branch=args.branch,
        stages=args.stage, remote=args.remote,
    )
    print(f"bu dalda eklenen tetikleyiciler: {', '.join(result.own) or '(yok)'}")
    for path, refs in sorted(result.carried.items()):
        print(f"::notice::TAŞINMIŞ tetikleyici yok sayıldı: {path} — ekleyen commit "
              f"başka dallarda da var ({', '.join(refs)}); aşama BAŞLATILMAZ")
    if result.error:
        print(f"::error::{result.error}")
        return 1

    stage = result.stage or ""
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"stage={stage}\n")
    print(f"aşama: {stage or '(yok — koşu başlatılmaz)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
