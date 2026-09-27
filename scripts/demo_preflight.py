#!/usr/bin/env python3
"""Read-only laptop demo readiness check; no recording, cloud calls or messages."""
import argparse
import importlib
import os
from pathlib import Path
import shutil
import sys
from urllib.request import urlopen
import json

from laptop_launch import load_env, ROOT


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file',type=Path,default=ROOT/'.env')
    parser.add_argument('--pi-url',help='Optional localhost SSH bridge, e.g. http://127.0.0.1:8081')
    args=parser.parse_args()
    env=os.environ.copy()
    failures=[]
    try:
        load_env(args.env_file,env)
    except ValueError as error:
        parser.error(str(error))
    def check(name,good,detail=''):
        print(('OK   ' if good else 'FIX  ')+name+(': '+detail if detail else ''))
        if not good:failures.append(name)
    for name in ('GEMINI_API_KEY','ELEVENLABS_API_KEY','ELEVENLABS_AGENT_ID'):
        check(name,bool(env.get(name)),'set' if env.get(name) else 'missing')
    for name in ('numpy','sounddevice','elevenlabs'):
        try:
            importlib.import_module(name)
            check(name,True)
        except (ImportError,OSError):
            check(name,False,'install companion/demo/requirements.txt')
    check('offline speech',bool(shutil.which('say') or shutil.which('espeak-ng') or shutil.which('espeak')))
    try:
        import sounddevice as sd
        sd.check_input_settings(channels=1,dtype='int16',samplerate=16000)
        sd.check_output_settings(channels=1,dtype='int16',samplerate=22050)
        check('default microphone / speakers',True,'format supported; permission and actual sound still need rehearsal')
    except Exception as error:
        check('default microphone / speakers',False,str(error))
    if args.pi_url:
        if not args.pi_url.startswith(('http://127.0.0.1:','http://localhost:')):
            parser.error('--pi-url must use a localhost SSH tunnel')
        try:
            with urlopen(args.pi_url.rstrip('/')+'/status',timeout=3) as response:
                status=json.load(response)
            check('Pi camera',status.get('camera')=='Ready',str(status.get('camera')))
            with urlopen(args.pi_url.rstrip('/')+'/frame',timeout=3) as response:
                check('timestamped Pi frame',bool(response.headers.get('X-Frame-Stamp')),
                      'rebuild the Pi bridge if stamp headers are missing')
        except Exception as error:
            check('Pi bridge',False,str(error))
    else:
        print('TODO Pi camera, depth/planner, browser permissions and both cloud services need live rehearsal.')
    print('Demo SMS is always simulated. No real sender is constructed.')
    return 1 if failures else 0


if __name__=='__main__':
    raise SystemExit(main())
