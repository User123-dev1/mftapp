"""The ``whmi`` CLI against a real receiver process."""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from .conftest import EXAMPLE

ROOT = Path(__file__).resolve().parents[1]


def whmi(*args, check=True):
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    r = subprocess.run([sys.executable, "-m", "whmi.tools.cli", *args], capture_output=True, text=True,
                       env=env, timeout=60)
    if check and r.returncode != 0:
        raise AssertionError(f"whmi {' '.join(args)} failed:\n{r.stdout}\n{r.stderr}")
    return r


def test_offline_commands(tmp_path):
    assert "0 error(s)" in whmi("validate", str(EXAMPLE)).stdout
    out = tmp_path / "demo.whc"
    assert "sha256" in whmi("build", str(EXAMPLE), "-o", str(out)).stdout
    assert "Tank.Level" in whmi("tags", str(out)).stdout
    assert len(whmi("keygen").stdout.strip()) == 64
    whmi("new", str(tmp_path / "new.whp"), "--name", "Fresh")
    assert "0 error(s)" in whmi("validate", str(tmp_path / "new.whp")).stdout


def test_build_reports_errors(tmp_path):
    bad = tmp_path / "bad.whp"
    bad.write_text(EXAMPLE.read_text().replace('"Slot3.AI.0"', '"Slot9.AI.0"'))
    r = whmi("build", str(bad), check=False)
    assert r.returncode == 1
    assert "slot 9 has no module" in r.stderr


@pytest.fixture
def receiver_proc(tmp_path):
    data = tmp_path / "rx"
    whmi("receiver", "init", "--data-dir", str(data), "--id", "RX-CLI", "--port", "47911")
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    proc = subprocess.Popen([sys.executable, "-m", "whmi.receiver", "--config", str(data / "receiver.json")],
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    deadline = time.time() + 10
    while time.time() < deadline:
        if whmi("info", "--host", "127.0.0.1", "--port", "47911", "--client-id", "panel1",
                "--key-file", str(data / "panel1.key"), check=False).returncode == 0:
            break
        time.sleep(0.2)
    yield data
    proc.terminate()
    proc.wait(10)


def test_download_read_write(receiver_proc):
    data = receiver_proc
    conn = ["--host", "127.0.0.1", "--port", "47911"]
    studio = conn + ["--client-id", "studio", "--key-file", str(data / "studio.key")]
    panel = conn + ["--client-id", "panel1", "--key-file", str(data / "panel1.key")]

    r = whmi("download", *panel, str(EXAMPLE), check=False)
    assert r.returncode == 1 and "ENGINEER" in r.stderr
    assert "download OK" in whmi("download", *studio, str(EXAMPLE)).stdout
    assert "already runs" in whmi("download", *studio, str(EXAMPLE)).stdout

    assert "OK" in whmi("write", *panel, "Batch.Count", "12").stdout
    assert "Batch.Count = 12" in whmi("read", *panel, "Batch.Count").stdout
    assert "OK" in whmi("write", *panel, "Batch.Operator_Note", "hello").stdout
    assert "'hello'" in whmi("read", *panel, "Batch.Operator_Note").stdout
    r = whmi("write", *panel, "Conveyor.Speed_SP", "500", check=False)
    assert r.returncode == 1 and "OUT_OF_RANGE" in r.stdout
    assert "DemoLine" in whmi("info", *panel).stdout
