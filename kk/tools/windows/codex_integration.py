"""Explicit, bounded Codex CLI invocation; no implicit paid delegation."""
from pathlib import Path
import os
import re
import shutil
import subprocess
from .development import run_process


def executable():
    direct=shutil.which('codex.exe')
    if direct: return Path(direct)
    npm=Path(os.environ.get('APPDATA',''))/'npm/node_modules/@openai/codex/node_modules/@openai'
    candidates=list(npm.glob('codex-win32-*/vendor/*/bin/codex.exe'))
    if len(candidates)==1: return candidates[0]
    raise FileNotFoundError('Codex CLI unavailable; use existing UI tools')


def explicit_task_request(text):
    folded=text.casefold()
    if re.search(r'\b(?:не|not|never|don.t)\b',folded): return False
    return bool(re.search(r'\b(?:codex|кодекс)\b',folded) and
        (re.search(r'\b(?:передай|поручи|отправь|ask|send|delegate)\b',folded) or
         (re.search(r'\b(?:выполни|запусти|run)\b',folded) and
          re.search(r'\b(?:задач\w*|задани\w*|task|job|prompt)\b',folded))))


def explicit_draft_request(text):
    return bool(re.search(r'(?i)\b(?:codex|кодекс)\b', text) and
                re.search(r'(?i)\b(?:напиши|введи|вставь|type|write|draft)\b', text))


def submission_requested(text):
    return bool(re.search(r'(?i)\b(?:выполни|выполнить|отправь|отправить|execute|send|submit|run)\b', text)
                and not re.search(r'(?i)\b(?:не\s+(?:выполняй|отправляй)|do not (?:send|run))\b', text))


class CodexIntegration:
    def __init__(self, registry):
        self.registry=registry
        self.result_dir=registry.memory.path.parent/'codex_results'

    def status(self):
        try:
            exe=executable()
            result=run_process([str(exe),'--version'],exe.parent,10,check=self.registry.check_cancel)
            return dict(available=True,executable=str(exe),version=result['stdout'].strip())
        except FileNotFoundError: return dict(available=False,fallback='Windows UI Automation')

    def run(self, cwd, task, timeout=300):
        token=self.registry.cancellation.get()
        if not explicit_task_request(getattr(token,'user_request','')):
            raise PermissionError('A direct user instruction to send/run a Codex task is required')
        directory=Path(cwd)
        if not directory.is_absolute() or not directory.is_dir(): raise ValueError('Absolute project directory required')
        self.result_dir.mkdir(parents=True,exist_ok=True)
        result=run_process([str(executable()),'exec','--sandbox','workspace-write','--json',
            '--output-last-message',str(self.result_dir/'last.md'),'-C',str(directory),'--',task],
            directory,timeout,check=self.registry.check_cancel)
        (self.result_dir/'last-events.jsonl').write_text(result['stdout'],encoding='utf-8')
        return result

    def last(self):
        path=self.result_dir/'last.md'
        return dict(available=path.exists(),text=path.read_text(encoding='utf-8')[:100000] if path.exists() else '')

    def open(self, cwd):
        directory=Path(cwd)
        if not directory.is_absolute() or not directory.is_dir(): raise ValueError('Absolute project directory required')
        self.registry.check_cancel()
        process=subprocess.Popen([str(executable()),'-C',str(directory)],cwd=directory,
            creationflags=subprocess.CREATE_NEW_CONSOLE)
        return dict(pid=process.pid,project=str(directory),opened=True)
