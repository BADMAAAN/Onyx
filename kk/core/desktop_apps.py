"""Read-only discovery. Only the known local terminal has a launch adapter."""
import os
from pathlib import Path
import shutil
import subprocess

CATALOG = [
    ('claude','Claude Code','claude','Anthropic','External coding CLI','https://code.claude.com/docs/en/overview'),
    ('codex','Codex CLI','codex','OpenAI','External coding CLI','https://developers.openai.com/codex/cli/'),
    ('openclaw','OpenClaw','openclaw','OpenClaw','Personal agent','https://github.com/openclaw/openclaw'),
    ('opencode','OpenCode','opencode','OpenCode','External coding CLI','https://opencode.ai/docs/'),
    ('hermes','Hermes Agent','hermes','Nous Research','Personal agent','https://github.com/NousResearch/hermes-agent'),
    ('hermes-desktop','Hermes Desktop','hermes-desktop','Nous Research','Desktop agent','https://hermes-agent.nousresearch.com/desktop'),
    ('droid','Droid','droid','Factory','External coding CLI','https://docs.factory.ai/droid-cli/quickstart'),
    ('pi','Pi','pi','Earendil','External coding CLI','https://github.com/earendil-works/pi'),
    ('cline','Cline','cline','Cline','External coding agent','https://github.com/cline/cline'),
    ('copilot','Copilot CLI','copilot','GitHub','External coding CLI','https://github.com/github/copilot-cli'),
    ('oh-my-pi','Oh My Pi','omp','Stencil Labs','External coding CLI','https://github.com/can1357/oh-my-pi'),
    ('deepseek-harness','DeepSeek Harness','dsh','DeepSeek','External agent harness','https://www.deepseek.com/harness/en/'),
    ('qwen-code','Qwen Code','qwen','Qwen','External coding CLI','https://github.com/QwenLM/qwen-code'),
    ('terminal','Terminal','powershell.exe','Microsoft','Local shell','https://learn.microsoft.com/powershell/'),
]


def catalog():
    rows=[]
    for id,name,command,publisher,purpose,docs in CATALOG:
        path=shutil.which(command)
        if id=='hermes-desktop' and not path:
            candidate=Path(os.environ.get('LOCALAPPDATA',''))/'Programs'/'Hermes Desktop'/'Hermes Desktop.exe'
            if candidate.is_file():path=str(candidate)
        if id=='cline' and not path:
            extensions=Path.home()/'.vscode'/'extensions'
            if extensions.is_dir():
                path=next((str(p) for p in extensions.glob('saoudrizwan.claude-dev-*') if (p/'package.json').is_file()),None)
        rows.append(dict(id=id,name=name,publisher=publisher,purpose=purpose,docs=docs,installed=bool(path),
                         path=path or '',connected=id=='terminal' and bool(path),launchable=id=='terminal' and bool(path)))
    return rows

def launch(id,settings):
    row=next((r for r in catalog() if r['id']==id and r['launchable']),None)
    if not row:raise ValueError('No verified launch adapter. Configure this external application separately.')
    # Terminal is an explicit interactive user action, never a cloud/model call.
    root=Path(settings.get('git_project', str(Path.home()))).resolve(strict=True)
    subprocess.Popen([row['path'],'-NoLogo','-NoProfile'],cwd=root,
                     creationflags=getattr(subprocess,'CREATE_NEW_CONSOLE',0))
    return dict(opened=id,scope='User-controlled terminal; its commands are outside Onyx network enforcement')
