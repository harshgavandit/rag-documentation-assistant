"""Durable, fail-closed accounting for explicitly authorized evaluation calls.

Reservations count attempts, including network failures and interrupted runs.
This local ledger enforces one operator-run allowance, not an account billing cap.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile


class RequestBudget:
    def __init__(self, path, *, authorization, limit=24, output_cap=800):
        if (type(limit) is not int or not 1 <= limit <= 24 or
                type(output_cap) is not int or not 1 <= output_cap <= 800 or
                not isinstance(authorization, str) or not authorization.strip()):
            raise ValueError('Invalid request authorization limits')
        self.path = Path(path)
        self.lock = self.path.with_suffix(self.path.suffix + '.lock')
        self.policy = {'schema_version': 1, 'authorization': authorization,
                       'request_limit': limit, 'max_completion_tokens': output_cap}
        if not self.path.is_file():
            try:
                with self.path.open('x', encoding='utf-8') as handle:
                    json.dump(dict(self.policy, attempts=[]), handle, indent=2)
                    handle.flush()
                    os.fsync(handle.fileno())
            except FileExistsError:
                pass
        self._read()

    def _read(self):
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if (set(data) != set(self.policy) | {'attempts'} or
                    any(data[key] != value for key, value in self.policy.items()) or
                    not isinstance(data['attempts'], list) or
                    len(data['attempts']) > self.policy['request_limit']):
                raise ValueError('Invalid request ledger or changed authorization')
            cases = [row['case_id'] for row in data['attempts']]
            if (len(cases) != len(set(cases)) or
                    any(not isinstance(case, str) or not case for case in cases)):
                raise ValueError('Invalid ledger cases')
            for index, row in enumerate(data['attempts'], 1):
                if (set(row) != {'case_id', 'attempt', 'reserved_at_utc'} or row['attempt'] != index or
                        not isinstance(row['reserved_at_utc'], str)):
                    raise ValueError('Invalid ledger attempt')
            return data
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
            raise ValueError('Invalid request ledger') from error

    def reserve(self, case_id):
        if not isinstance(case_id, str) or not case_id.strip():
            raise ValueError('Invalid case identity')
        # Exclusive reservation prevents two workers paying for the same slot.
        # A leftover lock after a crash blocks execution; it never refunds a call.
        lock_fd = os.open(self.lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        temporary = None
        try:
            os.close(lock_fd)
            data = self._read()
            if any(row['case_id'] == case_id for row in data['attempts']):
                raise ValueError('Case already reserved')
            if len(data['attempts']) >= self.policy['request_limit']:
                raise ValueError('Request budget exhausted')
            row = {'case_id': case_id, 'attempt': len(data['attempts']) + 1,
                   'reserved_at_utc': datetime.now(timezone.utc).isoformat()}
            data['attempts'].append(row)
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.path.parent,
                                             prefix=self.path.name + '.', suffix='.tmp', delete=False) as handle:
                temporary = Path(handle.name)
                json.dump(data, handle, indent=2)
                handle.write('\n')
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(self.path)
            return row['attempt']
        finally:
            if temporary and temporary.exists():
                temporary.unlink()
            self.lock.unlink()

    def snapshot(self):
        return self._read()
