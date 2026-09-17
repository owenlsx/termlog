"""CLI and session storage. Lock ownership, not saved PIDs, determines liveness."""
import argparse
import codecs
import contextlib
import datetime as dt
import json
import os
from pathlib import Path
import queue
import re
import shutil
import signal
import sys
import threading
import time
import uuid

from filelock import FileLock, Timeout


def root():
    path = Path(os.environ.get("TERMLOG_DIR", Path.home() / ".termlog"))
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def name(value):
    if not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9._-]{0,99}", value):
        raise argparse.ArgumentTypeError("use 1–100 letters, numbers, dots, underscores or hyphens; start with a letter, number, underscore or hyphen")
    if value.split('.')[0].upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]} or value.endswith('.'):
        raise argparse.ArgumentTypeError("name is reserved on Windows")
    return value


def lock(n):
    return FileLock(str(root() / (n + ".lock")), timeout=0)


def live(n):
    try:
        with lock(n):
            return False
    except Timeout:
        return True


def meta(n):
    try:
        return json.loads((root() / (n + ".json")).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save(n, data):
    dest = root() / (n + ".json")
    tmp = dest.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(dest)


def clean(text):
    # OSC (titles/links), CSI (colour/cursor), and short escape sequences.
    text = re.sub(r"\x1b\].*?(?:\x07|\x1b\\)", "", text, flags=re.S)
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    text = re.sub(r"\x1b[()][0-9A-Za-z]|\x1b[@-_]", "", text)
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text).replace('\r\n', '\n').replace('\r', '\n')


def logs():
    return sorted(root().glob('*.log'), key=lambda p: p.stat().st_mtime, reverse=True)


def tail(path, count):
    with path.open('rb') as f:
        f.seek(max(0, path.stat().st_size - 400_000))
        return '\n'.join(clean(f.read().decode('utf-8', 'replace')).splitlines()[-count:])


def overview():
    print('termlog — recorded terminal sessions\n')
    print(f'{"NAME":20} {"STATUS":7} {"BYTES":>10}  WHERE')
    for p in logs():
        m = meta(p.stem)
        print(f'{p.stem:20} {"LIVE" if live(p.stem) else "ended":7} {p.stat().st_size:10}  {m.get("host", "?")}:{m.get("cwd", "?")}')
    print('\nRecord: term <name> | Read: termlog <name> [lines] | Search: termlog --grep <pattern>')


def prune(days):
    for p in logs():
        try:
            with lock(p.stem):
                if p.stat().st_mtime < time.time() - days * 86400:
                    for suffix in ('.log', '.json', '.meta', '.stop'):
                        (root() / (p.stem + suffix)).unlink(missing_ok=True)
                    (root() / 'archive' / (p.stem + '.prev.log')).unlink(missing_ok=True)
                    print(f'removed {p.stem}')
        except Timeout:
            pass


def positive(value):
    result = int(value)
    if result < 1:
        raise argparse.ArgumentTypeError('must be a positive integer')
    return result


def configure_output():
    # Redirected Windows output may use a legacy code page. Keep the selected
    # encoding, but never abort recording because a character is unrepresentable.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(errors='replace')


def termlog():
    configure_output()
    p = argparse.ArgumentParser(description='Read terminal recordings (ANSI sequences removed).')
    g = p.add_mutually_exclusive_group()
    g.add_argument('-a', '--all', action='store_true', help='tail every live session')
    g.add_argument('-g', '--grep', metavar='REGEX', help='case-insensitive search of all logs')
    g.add_argument('-c', '--clean', nargs='?', const=7, type=positive, metavar='DAYS', help='delete ended sessions idle for this many days (default 7)')
    p.add_argument('session', nargs='?')
    p.add_argument('lines', nargs='?', type=positive, default=200)
    a = p.parse_args()
    if a.clean is not None:
        prune(a.clean)
    elif a.grep is not None:
        try:
            pattern = re.compile(a.grep, re.I)
        except re.error as e:
            p.error(str(e))
        for path in logs():
            with path.open(encoding='utf-8', errors='replace') as f:
                for i, line in enumerate(f, 1):
                    line = clean(line).rstrip()
                    if pattern.search(line):
                        print(f'{path.stem}:{i}:{line}')
    elif a.all:
        try:
            count = positive(a.session) if a.session else 60
        except (ValueError, argparse.ArgumentTypeError) as e:
            p.error(str(e))
        for path in logs():
            if live(path.stem):
                print(f'===== {path.stem} =====\n{tail(path, count)}')
    elif a.session:
        try:
            n = name(a.session)
        except argparse.ArgumentTypeError as e:
            p.error(str(e))
        path = root() / (n + '.log')
        if not path.exists():
            p.error(f'no session named {n!r}')
        print(f'# session {n!r} — {"LIVE" if live(n) else "ended"}\n{tail(path, a.lines)}')
    else:
        overview()


@contextlib.contextmanager
def terminal_mode():
    if os.name == 'nt':
        import ctypes
        kernel = ctypes.windll.kernel32
        handle = kernel.GetStdHandle(-11)
        old = ctypes.c_ulong()
        ok = kernel.GetConsoleMode(handle, ctypes.byref(old))
        if ok:
            kernel.SetConsoleMode(handle, old.value | 4)
        try:
            yield
        finally:
            if ok:
                kernel.SetConsoleMode(handle, old.value)
    elif sys.stdin.isatty():
        import termios
        import tty
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            yield
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
    else:
        yield


def record(n, command):
    if os.name == 'nt':
        from winpty import PtyProcess
    else:
        from ptyprocess import PtyProcess
    path = root() / (n + '.log')
    stop = root() / (n + '.stop')
    stop.unlink(missing_ok=True)
    if path.exists() and path.stat().st_size > 50 * 1024 * 1024:
        archive = root() / 'archive'
        archive.mkdir(exist_ok=True)
        path.replace(archive / (n + '.prev.log'))
    session = dict(name=n, token=uuid.uuid4().hex, started=dt.datetime.now(dt.timezone.utc).isoformat(), cwd=os.getcwd(), host=__import__('socket').gethostname())
    env = dict(os.environ, TERMLOG_SESSION=n)
    env.pop('TERMLOG_PROMPT_SET', None)
    size = shutil.get_terminal_size()
    proc = PtyProcess.spawn(command, env=env, dimensions=(size.lines, size.columns))
    session['pid'] = proc.pid
    save(n, session)
    events = queue.Queue(maxsize=256)

    def reader():
        try:
            while True:
                events.put(proc.read(4096))
        except EOFError:
            events.put(None)
        except Exception as e:
            events.put(e)

    threading.Thread(target=reader, daemon=True).start()
    print(f'● recording {n!r} → {path}\nRead with: termlog {n}\nExit the shell to stop.', flush=True)
    decoder = codecs.getincrementaldecoder('utf-8')('replace')
    handlers = {}

    def interrupt(signum, frame):
        proc.write('\x03' if os.name == 'nt' else b'\x03')

    def terminate(signum, frame):
        raise SystemExit(128 + signum)

    for sig in (signal.SIGINT, signal.SIGTERM, getattr(signal, 'SIGHUP', None)):
        if sig is not None:
            handlers[sig] = signal.signal(sig, interrupt if sig == signal.SIGINT else terminate)
    try:
        with path.open('ab', buffering=0) as log, terminal_mode():
            log.write(f'\n----- started {session["started"]} -----\n'.encode())
            while True:
                if stop.exists() and stop.read_text(encoding='utf-8') == session['token']:
                    proc.terminate(force=True)
                    break
                try:
                    output = events.get(timeout=0.02)
                except queue.Empty:
                    output = b''
                if output is None:
                    break
                if isinstance(output, Exception):
                    raise output
                if output:
                    raw = output.encode('utf-8') if isinstance(output, str) else output
                    log.write(raw)
                    sys.stdout.write(decoder.decode(raw))
                    sys.stdout.flush()
                new_size = shutil.get_terminal_size()
                if new_size != size:
                    proc.setwinsize(new_size.lines, new_size.columns)
                    size = new_size
                if os.name == 'nt':
                    import msvcrt
                    if msvcrt.kbhit():
                        char = msvcrt.getwch()
                        if char in ('\x00', '\xe0'):
                            char = {'H': '\x1b[A', 'P': '\x1b[B', 'M': '\x1b[C', 'K': '\x1b[D', 'G': '\x1b[H', 'O': '\x1b[F', 'S': '\x1b[3~'}.get(msvcrt.getwch(), '')
                        proc.write(char)
                else:
                    import select
                    if select.select([sys.stdin], [], [], 0)[0]:
                        data = os.read(sys.stdin.fileno(), 4096)
                        if data:
                            proc.write(data)
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
        if proc.isalive():
            proc.terminate(force=True)
        session['ended'] = dt.datetime.now(dt.timezone.utc).isoformat()
        save(n, session)
        stop.unlink(missing_ok=True)
    print(f'\n■ stopped {n!r}; log kept at {path}')
    return proc.exitstatus or 0


def term():
    configure_output()
    p = argparse.ArgumentParser(description='Record a named interactive shell for humans and AI agents.')
    p.add_argument('session', nargs='?', type=name)
    p.add_argument('-l', '--list', action='store_true')
    p.add_argument('-s', '--stop', '--end', type=name, metavar='NAME')
    p.add_argument('--command', nargs=argparse.REMAINDER, help='record a command instead of your shell; place last')
    a = p.parse_args()
    if a.list:
        overview()
        return
    if a.stop:
        if not live(a.stop):
            print(f'{a.stop!r} is not running')
            return
        token = meta(a.stop).get('token')
        if not token:
            p.error('session metadata unavailable; retry shortly')
        (root() / (a.stop + '.stop')).write_text(token, encoding='utf-8')
        for _ in range(100):
            if not live(a.stop):
                print(f'stopped {a.stop!r} (log kept)')
                return
            time.sleep(.05)
        p.error('stop requested, but recorder has not yet exited')
    if os.environ.get('TERMLOG_SESSION'):
        p.error('already recording in this shell; exit before starting another session')
    n = a.session or re.sub(r'[^A-Za-z0-9_-]', '-', Path.cwd().name) or 'session'
    try:
        n = name(n)
        shell = (shutil.which('pwsh') or os.environ.get('COMSPEC', 'cmd.exe')) if os.name == 'nt' else os.environ.get('SHELL', '/bin/sh')
        with lock(n):
            code = record(n, a.command or [shell])
        raise SystemExit(code)
    except Timeout:
        p.error(f'session {n!r} is already recording')
    except (OSError, argparse.ArgumentTypeError) as e:
        p.error(str(e))
