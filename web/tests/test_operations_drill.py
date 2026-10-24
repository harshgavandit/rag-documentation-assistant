"""Live drill must fail safely before credentials or external requests."""
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from operations.verify_live import main


class OperationsDrillTests(unittest.TestCase):
    def test_requires_explicit_live_and_preserves_existing_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'existing.json'
            path.write_text('preserve')
            required = ['--env-file', 'unused.env', '--control-file', 'unused-control.env', '--output', str(path)]
            for args in (required, ['--live', *required]):
                with patch('operations.verify_live.http') as http, redirect_stderr(StringIO()):
                    with self.assertRaises(SystemExit) as caught:
                        main(args)
                    self.assertEqual(caught.exception.code, 2)
                    http.assert_not_called()
            self.assertEqual(path.read_text(), 'preserve')
