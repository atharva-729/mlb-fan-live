import pytest

from fanpulse_live import cli


def test_help_exits_cleanly(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])

    assert exc.value.code == 0
    assert "find-game" in capsys.readouterr().out


def test_find_game_requires_date():
    with pytest.raises(SystemExit) as exc:
        cli.main(["find-game"])

    assert exc.value.code == 2


def test_show_config_lists_threads_and_streams(capsys):
    assert cli.main(["show-config"]) == 0

    out = capsys.readouterr().out
    assert "1ofc2dw" in out
    assert "r/baseball:neutral" in out
