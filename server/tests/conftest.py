"""WS 测试的引擎隔离（审查 A1）。

问题背景：test_websocket 直接操作 main.engine——导入 main 即触发
lifespan → engine.start()（读真实存档 + 起真实主循环 task），测试改的
玩家坐标/时钟可能被 60s autosave 写回真实库。原防线是"测试套件跑得快 +
monkeypatch 纪律"的侥幸组合，任何一处变动都可能写坏真实存档。

机制化隔离：autouse fixture 在每个 WS 测试前把 main.engine 换成独立
实例（不 start 主循环、存档读写全部打桩为空操作），测试结束还原。
测试代码继续写 main.engine.xxx，零改动成本，但碰的永远是替身。
"""

from pathlib import Path

import pytest

import main
from world import engine as we


@pytest.fixture(autouse=True)
def _isolated_engine(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """每个测试一个干净引擎替身：不读档、不存档、不起主循环。"""
    sandbox = we.WorldEngine()
    # 存档三口全堵：读档返回 None（新世界）、存档永远"成功"但不落真实库
    monkeypatch.setattr(sandbox, "_load_from_save", lambda: False)
    monkeypatch.setattr(sandbox, "save_now", lambda: "day1-08:00")
    # 记忆写入同样不落真实库（engine 层薄封装，拦截一处即全拦）
    monkeypatch.setattr(sandbox, "_write_memory", lambda *a, **k: None)
    # 测试默认无订阅者：broadcast 早退，不起任何异步任务
    monkeypatch.setattr(main, "engine", sandbox)
    yield
    # monkeypatch 自动还原 main.engine；sandbox 的 task 从未启动，无需清理
