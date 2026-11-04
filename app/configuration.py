"""Owner-only app configuration, parsed as data and replaced atomically."""
import os
from pathlib import Path
import shlex
import stat
import tempfile
import threading

ALLOWED_KEYS={'KITE_API_KEY','KITE_API_SECRET','KITE_ACCESS_TOKEN','ARTHA_AI_API_KEY','ARTHA_AI_MODEL','ARTHA_TELEGRAM_BOT_TOKEN','ARTHA_TELEGRAM_CHAT_ID'}

class PrivateConfig:
    def __init__(self,path):self.path=Path(path);self.lock=threading.RLock()

    def read(self):
        with self.lock:
            try:fd=os.open(self.path,os.O_RDONLY|os.O_NOFOLLOW)
            except FileNotFoundError:return {}
            except OSError:raise ValueError('Private configuration must be a regular owner-only file') from None
            try:
                info=os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_mode&0o077 or info.st_uid!=os.geteuid():
                    raise ValueError('Restrict private configuration to its owner with mode 600')
                with os.fdopen(fd,'r',encoding='utf-8') as stream:
                    fd=None;raw=stream.read(65537)
                if len(raw)>65536:raise ValueError('Private configuration exceeds the size limit')
                values={}
                for raw_line in raw.splitlines():
                    line=raw_line.strip()
                    if not line or line.startswith('#'):continue
                    key,separator,value=line.partition('=');key=key.strip()
                    if not separator or key not in ALLOWED_KEYS or key in values:raise ValueError('Unsupported or duplicate private configuration entry')
                    parts=shlex.split(value,comments=True)
                    if len(parts)>1:raise ValueError('Quote private configuration values containing spaces')
                    values[key]=parts[0] if parts else ''
                return values
            except (UnicodeError,ValueError) as exc:
                if isinstance(exc,ValueError) and str(exc).startswith(('Restrict','Unsupported','Quote','Private')):raise
                raise ValueError('Private configuration could not be parsed; check its format') from None
            finally:
                if fd is not None:os.close(fd)

    def save_kite(self,key,secret):
        self._save_values(dict(KITE_API_KEY=key,KITE_API_SECRET=secret,KITE_ACCESS_TOKEN=''))

    def save_ai(self,key,model):
        self._save_values(dict(ARTHA_AI_API_KEY=key,ARTHA_AI_MODEL=model))

    def save_alerts(self,token,chat):
        self._save_values(dict(ARTHA_TELEGRAM_BOT_TOKEN=token,ARTHA_TELEGRAM_CHAT_ID=chat))

    def _save_values(self,updates):
        with self.lock:
            values=self.read();values.update(updates)
            content='# Private application credentials. Parsed as data; never served to the browser.\n'+''.join(name+'='+shlex.quote(value)+'\n' for name,value in sorted(values.items()))
            fd,name=tempfile.mkstemp(prefix='.artha-config-',dir=self.path.parent)
            temporary=Path(name)
            try:
                os.fchmod(fd,0o600)
                with os.fdopen(fd,'w',encoding='utf-8') as stream:
                    fd=None;stream.write(content);stream.flush();os.fsync(stream.fileno())
                os.replace(temporary,self.path)
                directory=os.open(self.path.parent,os.O_RDONLY|os.O_DIRECTORY)
                try:os.fsync(directory)
                finally:os.close(directory)
            finally:
                if fd is not None:os.close(fd)
                temporary.unlink(missing_ok=True)
