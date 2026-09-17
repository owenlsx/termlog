import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from termlog import cli


class Sessions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'TERMLOG_DIR': self.tmp.name})
        self.env.start()
        os.environ.pop('TERMLOG_SESSION', None)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def run_cli(self, function, *args):
        return subprocess.run([sys.executable, '-c', f'from termlog.cli import {function}; {function}()', *args], capture_output=True, text=True, timeout=20)

    def test_names(self):
        for invalid in ('../x', '.', '..', '/tmp/x', 'CON', 'nul.txt', 'x.', 'a/b'):
            with self.assertRaises(Exception):
                cli.name(invalid)
        self.assertEqual(cli.name('backend-1'), 'backend-1')

    def test_clean(self):
        self.assertEqual(cli.clean('\x1b[31mhello\x1b[0m\r\n\x1b]0;title\x07world'), 'hello\nworld')

    def test_lock_and_prune(self):
        path = cli.root() / 'test.log'
        path.write_text('kept')
        os.utime(path, (0, 0))
        with cli.lock('test'):
            self.assertTrue(cli.live('test'))
            cli.prune(1)
            self.assertTrue(path.exists())
        self.assertFalse(cli.live('test'))
        cli.prune(1)
        self.assertFalse(path.exists())

    def test_record_resume_read_search(self):
        for marker in ('first-marker', 'second-marker'):
            r = self.run_cli('term', 'demo', '--command', sys.executable, '-c', f'print("{marker}")')
            self.assertEqual(r.returncode, 0, r.stderr)
        r = self.run_cli('termlog', 'demo')
        self.assertIn('first-marker', r.stdout)
        self.assertIn('second-marker', r.stdout)
        r = self.run_cli('termlog', '--grep', 'SECOND-marker')
        self.assertIn('second-marker', r.stdout)
        self.assertFalse(cli.live('demo'))
        self.assertIn('ended', cli.meta('demo'))

    def test_legacy_output_encoding(self):
        with patch.dict(os.environ, {'PYTHONIOENCODING': 'ascii'}):
            result = self.run_cli('term', 'unicode', '--command', sys.executable, '-c', 'print("marker")')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('marker', (cli.root() / 'unicode.log').read_text())

    def test_idle_warning_on_start(self):
        old = cli.root() / 'old.log'
        old.write_text('preserve this history')
        stale = time.time() - 8 * 86400
        os.utime(old, (stale, stale))
        result = self.run_cli('term', 'new', '--command', sys.executable, '-c', 'pass')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr.count('warn:'), 1)
        self.assertIn('termlog --clean 7', result.stderr)
        self.assertEqual(old.read_text(), 'preserve this history')
        self.assertNotIn('warn:', self.run_cli('termlog').stderr)
        with cli.lock('old'):
            result = self.run_cli('term', 'other', '--command', sys.executable, '-c', 'pass')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('warn:', result.stderr)
        result = self.run_cli('term', 'old', '--command', sys.executable, '-c', 'pass')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('warn:', result.stderr)

    def test_recent_logs_do_not_warn(self):
        path = cli.root() / 'recent.log'
        path.write_text('recent history')
        recent = time.time() - 6 * 86400
        os.utime(path, (recent, recent))
        result = self.run_cli('term', 'new', '--command', sys.executable, '-c', 'pass')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('warn:', result.stderr)

    def test_exit_code(self):
        self.assertEqual(self.run_cli('term', 'failed', '--command', sys.executable, '-c', 'raise SystemExit(7)').returncode, 7)

    def test_stop_and_duplicate(self):
        proc = subprocess.Popen([sys.executable, '-c', 'from termlog.cli import term; term()', 'running', '--command', sys.executable, '-c', 'import time; print("ready", flush=True); time.sleep(60)'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 15
            while not cli.meta('running').get('token') and time.monotonic() < deadline:
                time.sleep(.05)
            self.assertTrue(cli.live('running'))
            duplicate = self.run_cli('term', 'running')
            self.assertNotEqual(duplicate.returncode, 0)
            self.assertIn('already recording', duplicate.stderr)
            stopped = self.run_cli('term', '--stop', 'running')
            self.assertEqual(stopped.returncode, 0, stopped.stderr)
            proc.communicate(timeout=10)
            self.assertFalse(cli.live('running'))
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.communicate()


if __name__ == '__main__':
    unittest.main()
