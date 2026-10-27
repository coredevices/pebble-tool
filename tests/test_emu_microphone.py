"""
:Description: `pebble emu-mic` feeds the emulated watch microphone; `pebble emu-permission` grants or
denies an app a permission in the emulator's pypkjs.
"""
from __future__ import annotations

import argparse
import array
import json
import math
import struct
import wave
from types import SimpleNamespace

import pytest

from pebble_tool.commands import emucontrol
from pebble_tool.commands.emucontrol import EmuMicCommand, EmuPermissionCommand
from pebble_tool.exceptions import ToolError
from pebble_tool.util import mic_audio


def rms(pcm):
    samples = array.array('h', pcm)
    return math.sqrt(sum(s * s for s in samples) / len(samples))


def write_wav(path, rate, channels, width, frames):
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(frames)


def test_tone_is_16khz_pcm_at_the_asked_amplitude() -> None:
    blocks = mic_audio.tone(440, 8000)
    pcm = b''.join(next(blocks) for _ in range(50))
    assert len(pcm) == 16000 * 2
    assert abs(rms(pcm) - 8000 / math.sqrt(2)) < 10


def test_wav_is_mixed_down_and_resampled(tmp_path) -> None:
    path = tmp_path / 'stereo.wav'
    frames = b''.join(struct.pack('<hh', 1000, 3000) for _ in range(44100))
    write_wav(path, 44100, 2, 2, frames)
    pcm = mic_audio.read_wav(str(path))
    samples = array.array('h', pcm)
    assert len(samples) == 16000
    assert set(samples) == {2000}


def test_8bit_wav(tmp_path) -> None:
    path = tmp_path / 'u8.wav'
    write_wav(path, 16000, 1, 1, bytes([128, 255, 0]))
    assert list(array.array('h', mic_audio.read_wav(str(path)))) == [0, 127 << 8, -128 << 8]


def test_not_a_wav(tmp_path) -> None:
    path = tmp_path / 'nope.wav'
    path.write_bytes(b'not audio')
    with pytest.raises(ValueError):
        mic_audio.read_wav(str(path))


def parse(command, argv):
    parser = argparse.ArgumentParser()
    command.add_parser(parser.add_subparsers())
    return parser.parse_args([command.command] + argv)


def test_emu_mic_sends_the_file_on_protocol_14(monkeypatch, tmp_path) -> None:
    path = tmp_path / 'clip.wav'
    write_wav(path, 16000, 1, 2, struct.pack('<1000h', *range(1000)))
    sent = []
    monkeypatch.setattr(emucontrol, 'send_raw_to_qemu', lambda transport, protocol, payload: sent.append((protocol, payload)))
    monkeypatch.setattr(emucontrol.time, 'sleep', lambda seconds: None)
    monkeypatch.setattr('pebble_tool.commands.base.PebbleCommand.__call__', lambda self, args: None)
    cmd = EmuMicCommand()
    cmd.pebble = SimpleNamespace(transport=None)
    cmd(parse(EmuMicCommand, ['--file', str(path)]))
    assert {protocol for protocol, _ in sent} == {14}
    # 40 ms to a message, which fits the QEMU channel's 2048-byte limit.
    assert all(len(payload) <= 1280 for _, payload in sent)
    assert b''.join(payload for _, payload in sent) == struct.pack('<1000h', *range(1000))


def run_permission(monkeypatch, argv, reply):
    sent = []

    def request(transport, opcode, payload=b''):
        sent.append((opcode, json.loads(payload)))
        return json.dumps(reply).encode('utf-8')

    monkeypatch.setattr('pebble_tool.commands.emucontrol.phonesim.request', request)
    monkeypatch.setattr('pebble_tool.commands.base.PebbleCommand.__call__', lambda self, args: None)
    cmd = EmuPermissionCommand()
    cmd.pebble = SimpleNamespace(transport=None)
    cmd(parse(EmuPermissionCommand, argv))
    return sent


ALLOWED = {'uuid': 'x', 'name': 'App', 'granted': True, 'reason': 'App is allowed the microphone'}


@pytest.mark.parametrize('flag, state', [([], None), (['--deny'], 'deny'), (['--grant'], 'grant'),
                                         (['--reset'], 'reset')])
def test_emu_permission(monkeypatch, capsys, flag, state) -> None:
    sent = run_permission(monkeypatch, ['WatchMicrophone'] + flag, ALLOWED)
    assert sent == [(0x0f, {'permission': 'WatchMicrophone', 'uuid': None, 'state': state})]
    assert capsys.readouterr().out == 'WatchMicrophone: App is allowed the microphone.\n'


def test_emu_permission_for_another_app(monkeypatch) -> None:
    uuid = '6b1c3a52-0d4e-4f5a-9c33-2f0e7a1d8b40'
    sent = run_permission(monkeypatch, ['WatchMicrophone', '--deny', '--uuid', uuid], ALLOWED)
    assert sent[0][1]['uuid'] == uuid


def test_emu_permission_knows_only_the_microphone() -> None:
    with pytest.raises(SystemExit):
        parse(EmuPermissionCommand, ['Location'])


class FakeStream:
    def __init__(self, blocks):
        self.blocks = iter(blocks)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, frames):
        return next(self.blocks), False


def test_live_relays_the_recording_and_warns_about_pure_silence(capsys) -> None:
    tone = struct.pack('<320h', *([1000] * 320))
    recording = mic_audio._record(FakeStream([bytes(640)] * mic_audio.SILENT_BLOCKS + [tone]))
    blocks = [next(recording) for _ in range(mic_audio.SILENT_BLOCKS + 1)]
    assert blocks[-1] == tone
    assert 'pure silence' in capsys.readouterr().err


def test_live_stays_quiet_for_a_quiet_room(capsys) -> None:
    hiss = struct.pack('<320h', *([1] * 320))
    recording = mic_audio._record(FakeStream([hiss] * (mic_audio.SILENT_BLOCKS + 1)))
    for _ in range(mic_audio.SILENT_BLOCKS + 1):
        next(recording)
    assert capsys.readouterr().err == ''


def test_live_without_sounddevice(monkeypatch) -> None:
    monkeypatch.setitem(__import__('sys').modules, 'sounddevice', None)
    with pytest.raises(ValueError, match='mic extra'):
        mic_audio.live()
