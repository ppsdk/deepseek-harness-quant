"""Historical compatibility and stale-evidence protection for replay APIs."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backtest import research_trade_replay as replay


class TradeReplayTests(unittest.TestCase):
    def test_unknown_valid_archive_has_explicit_unavailable_state(self):
        with patch.object(replay,'read_config',return_value=dict(studies={},title='复盘',unavailable_note='证据未生成')):
            result=replay.get(dict(id='research_example_'+'a'*32))
        self.assertFalse(result['available'])
        self.assertEqual(result['message'],'证据未生成')

    def test_invalid_run_id_is_rejected(self):
        with patch.object(replay,'read_config',return_value=dict(studies={})):
            with self.assertRaises(ValueError):replay.get(dict(id='../../private'))

    def test_artifact_path_cannot_escape_its_folder(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(ValueError):replay.safe_file(Path(root),'../outside.json')

    def test_date_lookup_and_changed_archive_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);folder=root/'study/replay';folder.mkdir(parents=True)
            archive_dir=root/'archives';archive_dir.mkdir();key='research_example_'+'b'*32
            archive=archive_dir/(key+'.json');archive.write_text('{"nav":[1]}',encoding='utf-8')
            fingerprint=hashlib.sha256(archive.read_bytes()).hexdigest()
            entry=dict(run=key,title='fixture',url='/backtests',round_id='fixture',summary='summary.json',days='days.json')
            (folder/'index.json').write_text(json.dumps(dict(entries=[entry])),encoding='utf-8')
            (folder/'summary.json').write_text(json.dumps(dict(dates=['2025-01-02'],archive_sha256=fingerprint)),encoding='utf-8')
            (folder/'days.json').write_text(json.dumps({'2025-01-02':dict(date='2025-01-02',cash=100)}),encoding='utf-8')
            cfg=dict(studies={'fixture':dict(config='fixture',output_subdir='replay')},title='复盘',stale_note='归档已变化')
            def config(name):return cfg if name=='research_trade_replay' else dict(output_dir='study')
            with patch.object(replay,'ROOT',root),patch.object(replay,'read_config',side_effect=config),patch('backtest.research_runner.ARCHIVES',archive_dir):
                self.assertEqual(len(replay.get({})['entries']),1)
                result=replay.get(dict(id=key,date='2025-01-02'))
                self.assertEqual(result['detail']['cash'],100)
                with self.assertRaises(ValueError):replay.get(dict(id=key,date='2024-01-02'))
                archive.write_text('{"nav":[1,2]}',encoding='utf-8')
                result=replay.get(dict(id=key,date='2025-01-02'))
                self.assertFalse(result['available']);self.assertEqual(result['state'],'stale')


if __name__=='__main__':unittest.main()
