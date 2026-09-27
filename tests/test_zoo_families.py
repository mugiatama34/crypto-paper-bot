"""`scripts/zoo_families.py`: aile kurallarının tek kopyası (docs/backtest.md > 6r > 16.1).

ALTIN DEĞER: kurallar `scripts/measure_model_momentum.py`den taşınmadan ÖNCE, §6q'nun 88 tabanının
ağırlık matrisleri iki sentetik piyasada (biri eksik barlı ve geç listelenen sembollü) kuruldu ve
özetleri aşağıya yazıldı. Taşımadan sonra aynı özetler üretilmeli — aksi hâlde §6q'nun koşulmuş
sonucu (`docs/data/model_momentum.json`) artık bu koddan yeniden üretilemez.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from scripts import measure_model_momentum as mm
from scripts import zoo_families as zf
from tests.test_measure_model_momentum import END, START, SYMBOLS, synthetic, to_4h

GOLDEN = {
    "tsmom_1g_ls": "b06564e00e39b28a",
    "tsmom_1g_lo": "3428bb3387788fde",
    "tsmom_3g_ls": "02860a00d2dc0537",
    "tsmom_3g_lo": "7cb7e9d9c59759b6",
    "tsmom_7g_ls": "9b4683683b164cea",
    "tsmom_7g_lo": "79ece6d10c1f611f",
    "tsmom_14g_ls": "43c744d4003571d5",
    "tsmom_14g_lo": "6ba021ecd8b4e181",
    "tsmom_30g_ls": "92f6bb12e5c06bde",
    "tsmom_30g_lo": "81dc0b5e9ddbcad3",
    "ema_stack_5-21-50_1H": "312296065fb157cb",
    "ema_stack_5-21-50_4H": "5f6fe755312ef4c7",
    "ema_stack_8-21-55_1H": "a61113521b0c4096",
    "ema_stack_8-21-55_4H": "0592cc63592f1dd1",
    "ema_stack_10-30-100_1H": "d3cb49b940fa7c8d",
    "ema_stack_10-30-100_4H": "22c4c0e1c13e2519",
    "ma_cross_5-20_1H_ls": "cff8d67c9b9e0683",
    "ma_cross_5-20_1H_lo": "f18a25dd719d794f",
    "ma_cross_5-20_4H_ls": "b656e8531d7b3b41",
    "ma_cross_5-20_4H_lo": "523924f76d91eda3",
    "ma_cross_10-30_1H_ls": "db5afcae11ba0490",
    "ma_cross_10-30_1H_lo": "dfe8f6baab4930da",
    "ma_cross_10-30_4H_ls": "d9284d302b1159d4",
    "ma_cross_10-30_4H_lo": "5480d906317ab582",
    "ma_cross_20-50_1H_ls": "7c3cd670c9620047",
    "ma_cross_20-50_1H_lo": "0c93016c727bdf04",
    "ma_cross_20-50_4H_ls": "d665166861b75cae",
    "ma_cross_20-50_4H_lo": "0ad53eb24cc80484",
    "ma_cross_21-55_1H_ls": "725c7b97e59a5b69",
    "ma_cross_21-55_1H_lo": "c09e406678db39d2",
    "ma_cross_21-55_4H_ls": "034c69fcbc536646",
    "ma_cross_21-55_4H_lo": "7b2072d3903a8fda",
    "ma_cross_50-200_1H_ls": "a4a54c4c0f849f44",
    "ma_cross_50-200_1H_lo": "75d1a51621ef8cab",
    "ma_cross_50-200_4H_ls": "d9db5516bf152ef2",
    "ma_cross_50-200_4H_lo": "ef006748cbf69f86",
    "st_rev_1h_1H_ls": "76a1fa3c44c4baa7",
    "st_rev_1h_1H_lo": "a06c510d75c1c827",
    "st_rev_4h_1H_ls": "cc76f994cd327727",
    "st_rev_4h_1H_lo": "032503612afe249d",
    "st_rev_4h_4H_ls": "7e40c8e99559766d",
    "st_rev_4h_4H_lo": "a43417436d65b948",
    "st_rev_1D_1D_ls": "67204a291c5fc736",
    "st_rev_1D_1D_lo": "2e1e332fdc877e36",
    "donchian_20_1H_ls": "7751130367050cf4",
    "donchian_20_1H_lo": "d7eb46d547be4f91",
    "donchian_20_4H_ls": "8d2dd82bc0c2135b",
    "donchian_20_4H_lo": "f6d76df4a5da1cd8",
    "donchian_55_1H_ls": "38a158688180382e",
    "donchian_55_1H_lo": "a0bf5a5360cef412",
    "donchian_55_4H_ls": "b40fd8223e2d1248",
    "donchian_55_4H_lo": "1db3734205a0f508",
    "donchian_120_1H_ls": "44dcbdcf966aee37",
    "donchian_120_1H_lo": "cae05033fb742324",
    "donchian_120_4H_ls": "1a76db71b56f1524",
    "donchian_120_4H_lo": "88d8af93cd686a64",
    "xsec_1g_k2_ls": "1c8a8112f1012af8",
    "xsec_1g_k2_lo": "e64680c5b467742c",
    "xsec_1g_k4_ls": "eb9f0e981bfa97ec",
    "xsec_1g_k4_lo": "f33724f769f4c01a",
    "xsec_3g_k2_ls": "a4f4e8110b4deb11",
    "xsec_3g_k2_lo": "ee3f96e48e6bc2ba",
    "xsec_3g_k4_ls": "4a49a37b1b610054",
    "xsec_3g_k4_lo": "0ee45b43d46ace72",
    "xsec_7g_k2_ls": "4067f0374aa11cb4",
    "xsec_7g_k2_lo": "221e3ad6b1a1f3b2",
    "xsec_7g_k4_ls": "bcc3352638d1c404",
    "xsec_7g_k4_lo": "c07edb89e717aac5",
    "xsec_14g_k2_ls": "69f3293e99defe5b",
    "xsec_14g_k2_lo": "db121b789ad79e6d",
    "xsec_14g_k4_ls": "087b6c52d4f18428",
    "xsec_14g_k4_lo": "4de434fc5819e1e5",
    "xsec_30g_k2_ls": "e62460a7dfcd1c4e",
    "xsec_30g_k2_lo": "d8df7df017dbfa3c",
    "xsec_30g_k4_ls": "47d3d214d9cad716",
    "xsec_30g_k4_lo": "3b61b9b4d3614fee",
    "rsi_2_10-90_1H": "03acaebe914ad443",
    "rsi_2_10-90_4H": "13ce9b9896ef0fa8",
    "rsi_2_25-75_1H": "a3a7a227daef04fa",
    "rsi_2_25-75_4H": "a481b2a6bfcd32dc",
    "rsi_14_30-70_1H": "6549694a671b9946",
    "rsi_14_30-70_4H": "a6e8c4412f67ff98",
    "rsi_14_20-80_1H": "0e06d209d94d7f07",
    "rsi_14_20-80_4H": "fe950c380632edb0",
    "bollinger_20_2.0_1H": "41038cec7ad60d79",
    "bollinger_20_2.0_4H": "3aaaae7722f1f729",
    "bollinger_20_2.5_1H": "7ab0c275cd3a25d3",
    "bollinger_20_2.5_4H": "9cbe1ca1569e5e85",
}


def _gapped():
    h1, h4 = synthetic(SYMBOLS, START, END, seed=11)
    rng = np.random.default_rng(5)
    for s in SYMBOLS[:4]:
        drop = rng.choice(len(h1[s]), 40, replace=False)
        h1[s] = h1[s].drop(h1[s].index[drop])
        h4[s] = to_4h(h1[s])
    h1["S8"] = h1["S8"][h1["S8"].index >= pd.Timestamp("2022-02-10T00:00Z")]
    h4["S8"] = to_4h(h1["S8"])
    return mm.build_market(h1, h4, SYMBOLS, start=START, end=END)


def test_6q_weights_are_bit_identical_after_the_move():
    h1, h4 = synthetic(SYMBOLS, START, END)
    markets = [mm.build_market(h1, h4, SYMBOLS, start=START, end=END), _gapped()]
    got = {b.id: mm.weight_digest(np.vstack([b.build(m) for m in markets]))[:16] for b in mm.zoo()}
    assert got == GOLDEN


def test_6q_uses_the_shared_rules():
    assert mm.bollinger_bands is zf.bollinger_bands
    assert mm.donchian_bands is zf.donchian_bands
    assert mm.extreme_machine is zf.extreme_machine
    assert mm.KNOWN_PRECISION_WINDOW is zf.KNOWN_PRECISION_WINDOW


def test_to_grid_takes_effect_at_bar_close():
    idx = pd.date_range("2022-01-01", periods=3, freq="4h", tz="UTC")
    grid = pd.date_range("2022-01-01", periods=16, freq="h", tz="UTC")
    out = zf.to_grid(pd.Series([1.0, -1.0, 0.0], index=idx), effective_lag=pd.Timedelta(hours=4), grid=grid)
    assert list(out[:4]) == [0.0] * 4 and list(out[4:8]) == [1.0] * 4 and list(out[8:12]) == [-1.0] * 4


def test_xsec_target_ties_and_minimum():
    row = np.array([0.1, 0.1, -0.2, 0.3])
    t = zf.xsec_target(row, np.ones(4, bool), ["b", "a", "c", "d"], k=1, long_only=False)
    assert t.tolist() == [0.0, 0.0, -0.5, 0.5]
    t = zf.xsec_target(row, np.ones(4, bool), ["b", "a", "c", "d"], k=2, long_only=True)
    assert t.tolist() == [0.0, 0.5, 0.0, 0.5]          # eşitlikte sembol adı: a < b
    assert not zf.xsec_target(row, np.array([1, 1, 1, 0], bool), list("abcd"), k=2, long_only=False).any()
