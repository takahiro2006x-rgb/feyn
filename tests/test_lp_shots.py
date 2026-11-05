# -*- coding: utf-8 -*-
"""LP用のデモ画面を撮るスクリプト（scripts/lp_shots.py）が、本番の環境では動かないことのテスト"""
import os
import subprocess
import sys

import pytest

SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts', 'lp_shots.py')


@pytest.mark.parametrize('env', [{'RENDER': 'true'}, {'DATABASE_URL': 'postgresql://example/db'}])
def test_demo_script_refuses_to_run_in_production(env):
    result = subprocess.run([sys.executable, SCRIPT, 'app'], env={**os.environ, 'PYTHONIOENCODING': 'utf-8', **env},
                            capture_output=True, text=True, encoding='utf-8', timeout=60)
    assert result.returncode != 0
    assert '本番' in result.stderr


def test_demo_script_uses_fictional_names_only():
    src = open(SCRIPT, encoding='utf-8').read()
    assert "DB_PATH = os.path.join(_tmpdir" in src     # 使い捨てのDBに切り替えてから app を読み込む
    assert src.index('db_module.DB_PATH = ') < src.index('import app as app_module')
    assert '@example.com' in src and 'デモ生徒' in src
