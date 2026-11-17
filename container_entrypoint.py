"""Prepare the dedicated volume, then drop root before starting the bot."""
import os
import pwd
import runpy
from pathlib import Path

if os.getuid() == 0:
    account = pwd.getpwnam('lospi')
    directory = Path('/music-data')
    directory.mkdir(exist_ok=True)
    os.chown(directory, account.pw_uid, account.pw_gid)
    os.chmod(directory, 0o700)
    os.initgroups(account.pw_name, account.pw_gid)
    os.setgid(account.pw_gid)
    os.setuid(account.pw_uid)

runpy.run_module('shared_server', run_name='__main__')
