"""Workflow'dan çağrılan her betik, ÇAĞRILDIĞI GİBİ koşabilmeli.

**Neden bu test var (ölçülmüş bir arıza).** `scripts/proximity.py` `sys.path`e depo
kökünü eklemeyi atlamıştı. Testlerde görünmüyordu: `tests/` bir pakettir, yani pytest
depo kökünü `sys.path`e koyar ve `from core... import` her zaman çalışır. Workflow ise
betiği `python scripts/proximity.py` ile çağırır — o kipte `sys.path[0]` **`scripts/`**
olur, çalışma dizini değil, ve `core` hiç görünmez. Sonuç: adım her turda
`ModuleNotFoundError` ile çöküyordu ve `continue-on-error: true` yüzünden job YEŞİL
dönüyordu. Yani ortada bir test vardı, bir workflow vardı, ikisi de yeşildi ve betik hiç
koşmuyordu.

**Bu yüzden kapı `import` değil `subprocess`tir.** Aynı süreç içinde `import scripts.x`
yapmak tam da maskeleyen şeyi tekrarlardı; buradaki tek geçerli ölçüt betiği ayrı bir
süreçte, workflow'un kullandığı komut biçimiyle çalıştırmaktır. `PYTHONPATH` de
temizlenir: mirasla gelen bir kök, hatayı ikinci kez gizlerdi.

**Betik listesi ELLE YAZILMAZ**, `.github/workflows/*.yml`den okunur. Elle yazılan bir
liste, yeni eklenen bir betiği sessizce kapsam dışında bırakırdı — yani testin
kapatmak için var olduğu hatanın aynısını üretirdi. Listenin boş çıkması ya da bilinen
bir betiği kaçırması ayrıca sınanır: veriyi kendi tarayan bir testte "hiç eşleşme yok"
her zaman yeşil görünür (karar 51'in "boş rapor yeşil dönmez" kuralının buradaki hâli).

Ölçüt `--help`tir, tam bir koşu değil: hepsi `argparse` kullanır, yani `--help` modülün
İTHAL EDİLMESİNİ ve ayrıştırıcının KURULMASINI gerektirir ama ağa, deftere ya da
config'e dokunmaz. Aranan hata tam olarak orada, ithal aşamasındadır.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

import pytest

from core.config import PROJECT_ROOT

WORKFLOWS = PROJECT_ROOT / ".github" / "workflows"

# `python scripts/<ad>.py` — satır sonundaki `\` ile çok satıra yayılan çağrılar da
# yakalanır, çünkü aranan şey yalnızca komutun BAŞIDIR.
_INVOCATION = re.compile(r"python\s+(scripts/[A-Za-z0-9_]+\.py)")


def workflow_scripts() -> list[str]:
    """Workflow dosyalarında `python scripts/...` ile çağrılan betikler, sıralı."""
    found: set[str] = set()
    for path in sorted(WORKFLOWS.glob("*.yml")):
        found.update(_INVOCATION.findall(path.read_text(encoding="utf-8")))
    return sorted(found)


SCRIPTS = workflow_scripts()


def test_the_scan_actually_found_the_invocations() -> None:
    """Tarama kendini de sınar: boş ya da eksik bir liste sessizce yeşil dönemez."""
    assert SCRIPTS, f"{WORKFLOWS} içinde `python scripts/...` çağrısı bulunamadı"
    # Kapının doğduğu betik: regex bozulursa bu satır kırmızıya döner.
    assert "scripts/proximity.py" in SCRIPTS


@pytest.mark.parametrize("script", SCRIPTS)
def test_script_runs_the_way_the_workflow_calls_it(script: str) -> None:
    """`python scripts/<ad>.py --help` depo kökünden çıkış kodu 0 ile bitmeli.

    Miras alınan `PYTHONPATH` SİLİNİR: içinde depo kökü varsa eksik bir
    `sys.path.insert` görünmez kalırdı — testin ölçtüğü şey tam olarak o eksiklik.
    """
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    result = subprocess.run(
        [sys.executable, script, "--help"],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, (
        f"{script} workflow'un çağırdığı biçimde koşamıyor "
        f"(çıkış kodu {result.returncode}):\n{result.stderr[-2000:]}"
    )


@pytest.mark.parametrize("script", SCRIPTS)
def test_script_bootstraps_the_repo_root(script: str) -> None:
    """Kalıp TEK olmalı: `sys.path`e depo kökü, `core` ithalinden ÖNCE.

    `--help` kapısı davranışı ölçer; bu kapı NEDENİ sabitler. İkisi ayrı durur çünkü
    bir betik kökü başka bir yolla (ör. bir `conftest`, bir `PYTHONPATH`) bulup
    `--help`i geçebilir ve o gün kalıp sessizce ayrışmış olurdu.
    """
    text = (PROJECT_ROOT / script).read_text(encoding="utf-8")
    bootstrap = text.find("sys.path.insert(0, str(Path(__file__).resolve().parent.parent))")
    if bootstrap == -1:
        bootstrap = text.find("sys.path.insert(0, str(REPO_ROOT))")
    assert bootstrap != -1, f"{script} depo kökünü sys.path'e eklemiyor"

    first_core = min(
        (index for index in (text.find("\nfrom core."), text.find("\nfrom strategies."),
                             text.find("\nfrom main "), text.find("\nfrom scripts."))
         if index != -1),
        default=-1,
    )
    if first_core != -1:
        assert bootstrap < first_core, (
            f"{script}: sys.path kurulumu core/strategies/main ithalinden SONRA geliyor"
        )
