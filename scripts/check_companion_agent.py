"""Real Qwen planning with registry actions confined to a temporary fixture."""
from pathlib import Path
import json
import logging
import sys
from tempfile import TemporaryDirectory
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))

def main():
    from kk.config import Config
    from kk.runtime import build_runtime
    from kk.permissions.policy import PermissionLevel
    runtime=build_runtime(Config.load(ROOT/'config.toml'),logging.getLogger('agent-check'))
    runtime.context.sample()
    results=[]
    try:
        questions=['Покажи git status проекта C:\\ai\\_agent.',
                   'Что сейчас находится на обоих экранах?']
        for question in questions:
            answer=runtime.agent.ask(question)
            task=runtime.agent.orchestrator.last_task
            results.append(dict(question=question,answer=answer,status=task.status.value,
                                actions=[dict(tool=a['tool'],ok=a.get('result',{}).get('ok')) for a in task.actions]))
        # Qwen must respect an in-turn correction; do not alter user's real volume.
        levels=[]
        runtime.tools._tools['set_volume'].handler=lambda level: (levels.append(level) or dict(actual_level=level))
        answer=runtime.agent.ask('Поставь громкость 50, нет, 30.')
        results.append(dict(question='self-correction',answer=answer,requested_levels=levels,passed=levels==[30]))
        with TemporaryDirectory(prefix='KK-agent-') as folder:
            target=Path(folder)/'Тест с пробелами.txt'
            # Explicit permission for this isolated test; configuration stays untouched.
            runtime.policy.autonomy=PermissionLevel.DANGEROUS
            answer=runtime.agent.ask(f'Создай файл {target} с точным текстом KK работает. Затем прочитай его и проверь содержимое.')
            results.append(dict(question='create-read-verify',answer=answer,
                passed=target.exists() and target.read_text(encoding='utf-8').strip()=='KK работает',
                tools=[a['tool'] for a in runtime.agent.orchestrator.last_task.actions]))
            result=runtime.tools.execute('run_terminal',dict(command='Write-Output "KK работает"; exit 0',cwd=folder))
            results.append(dict(question='terminal',passed=result.ok and result.data['exit_code']==0 and 'KK работает' in result.data['stdout']))
        results.append(dict(question='codex-status',result=runtime.tools.execute('codex_status').as_dict()))
    finally: runtime.context.stop(); runtime.memory.close()
    (ROOT/'data/companion_agent_acceptance.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(results,ensure_ascii=False,indent=2))

if __name__=='__main__': main()
