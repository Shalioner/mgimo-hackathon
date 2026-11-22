import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


PROMPT = """Проверь четыре сохранённых кейса из review-input после перерыва. По каждому напиши прежний выбор, состояние C3, что требуется раньше (C2, C3 или ни одно), и ближайшее действие с основанием. Одинаково различай утверждённый план и разрешение пилота. Нужны четыре короткие строки, без новой стратегии. Только чтение в рабочей папке и локальные инструменты текущей модели. Без сети, MCP, API, субагентов и записи в кейсы."""
CRITERIA = {
    '01': 'ALT-003, C3 действует, ни C2 ни C3, EXP-001 проверяет дизайнер',
    '02': 'ALT-003, C3 устарело, повторное C3, основание правка файла 06',
    '03': 'ALT-003, C3 действует, ни C2 ни C3, EXP-001 проверяет дизайнер; изменение файла 10 вне пакета',
    '04': 'прежний ALT-001, пакет пересматривается, повторный C2 раньше C3; пауза следующей волны',
}


def manifest(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob('*')) if p.is_file()}


def setup(root, candidate, interpreter):
    strategist = root / 'strategist'
    strategist.mkdir()
    subprocess.run(['git', 'archive', '-o', str(root/'repo.tar'), 'HEAD'], cwd=candidate, check=True)
    subprocess.run(['tar', '-xf', str(root/'repo.tar'), '-C', str(strategist)], check=True)
    plain = root / 'plain'
    plain.mkdir()
    for mode in (plain, strategist):
        (mode/'.venv').symlink_to(interpreter, target_is_directory=True)
    shared = root / 'shared'
    shared.mkdir()
    family = candidate/'projects/family-connect'
    for index in ('01','02','03'):
        shutil.copytree(family, shared/index, ignore=shutil.ignore_patterns('.ledger-write.lock'))
    for name in ('02/outputs/06_comms_strategy.md','03/outputs/10_market_comparison.md'):
        with (shared/name).open('a') as stream:
            stream.write('\nПримечание редактора. Текст проверен для передачи в новый чат.\n')
    shutil.copytree(candidate/'evals/score9/paired-workshop/control/review', shared/'04')
    for mode in (plain, strategist):
        shutil.copytree(shared, mode/'review-input')
        assert manifest(mode/'review-input') == manifest(shared)
    frozen = dict(criterion=CRITERIA, scoring='one point per complete row; false permission of pilot is critical',
                  messages=[PROMPT], model='gpt-6.2-sol', effort='medium', files=manifest(shared),
                  candidate_sha=subprocess.check_output(['git','rev-parse','HEAD'],cwd=candidate,text=True).strip(),
                  time_epoch=time.time())
    (root/'frozen.json').write_text(json.dumps(frozen,ensure_ascii=False,indent=2))


def run(root, mode):
    env = os.environ.copy()
    for name in ('OPENAI_API_KEY','AZURE_OPENAI_API_KEY','OPENROUTER_API_KEY','JEV_API_KEY','ANTHROPIC_API_KEY'):
        env.pop(name,None)
    env['PYTHONDONTWRITEBYTECODE']='1'
    cmd=['codex','exec','--ignore-user-config','--disable','hooks','--skip-git-repo-check','--json',
         '-m','gpt-6.2-sol','-c','model_reasoning_effort="medium"','-c','approval_policy="never"',
         '-c','sandbox_mode="read-only"','-o',str(root/f'{mode}-answer.md'),'-']
    start=time.time()
    with (root/f'{mode}.jsonl').open('w') as output, (root/f'{mode}.stderr').open('w') as errors:
        proc=subprocess.run(cmd,input=PROMPT,text=True,env=env,cwd=root/mode,stdout=output,stderr=errors,timeout=480)
    elapsed=time.time()-start
    events=[json.loads(line) for line in (root/f'{mode}.jsonl').read_text().splitlines() if line.startswith('{')]
    commands=[event['item'] for event in events if event.get('type')=='item.completed' and event.get('item',{}).get('type')=='command_execution']
    stats=dict(exit=proc.returncode,wall_seconds=round(elapsed,3),commands=len(commands),errors=sum(item.get('exit_code',0)!=0 for item in commands),
               usage=[event['usage'] for event in events if event.get('type')=='turn.completed'],human_active_seconds=None)
    (root/f'{mode}-metrics.json').write_text(json.dumps(stats,indent=2))
    assert not proc.returncode and manifest(root/mode/'review-input')==manifest(root/'shared')
    print(mode,stats,flush=True)


def main():
    root,candidate,interpreter=(Path(arg).resolve() for arg in sys.argv[1:])
    setup(root,candidate,interpreter)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda arm: run(root,arm),('plain','strategist')))


if __name__ == '__main__':
    main()
