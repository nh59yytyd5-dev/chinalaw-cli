import json

import pytest

from chinalaw.remote_check import CheckError, failure, settings


def test_credentials_are_parsed_without_shell_execution(tmp_path):
    path = tmp_path / 'remote.env'
    path.write_text('CHINALAW_REMOTE_URL=https://example.com\nCHINALAW_QUERY_TOKEN="$(not-a-command)"\n')
    url, token = settings(path, {})
    assert url == 'https://example.com/mcp'
    assert token == '$(not-a-command)'
    assert settings(path, {'CHINALAW_QUERY_TOKEN': 'override'})[1] == 'override'


def test_missing_credentials_and_unknown_errors_are_explicit(tmp_path):
    with pytest.raises(CheckError) as caught:
        settings(tmp_path / 'missing', {})
    assert failure(caught.value)['error'] == 'credentials_missing'
    result = failure(RuntimeError('secret-token'))
    assert result['ok'] is False
    assert 'secret-token' not in json.dumps(result)
