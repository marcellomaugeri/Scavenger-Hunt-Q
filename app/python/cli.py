#!/usr/bin/env python3
"""Control Scavenger Hunt Q using only the Python standard library."""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:7000', help='Game URL (default: local board); specify the board address from another computer')
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('start','reset','menu','tutorial','settings','pause','resume','status','log','watch'):
        sub.add_parser(name)
    sub.add_parser('duration').add_argument('seconds', type=int, choices=(30,60,120))
    sub.add_parser('voice', help='Send a recognised phrase without a microphone').add_argument('text')
    sim = sub.add_parser('simulate', help='TEST ONLY: requires runtime.json test_mode=true')
    sim.add_argument('player', choices=('red','blue'))
    sim.add_argument('label', help='Explicit detected object label')
    args = parser.parse_args()
    endpoint = '/api/status'
    data = None
    if args.command == 'log':
        endpoint = '/api/commands'
    elif args.command == 'simulate':
        endpoint = '/api/test/detection'
        data = {'player':args.player,'label':args.label}
    elif args.command not in ('status','watch'):
        endpoint = '/api/command'
        arguments = {'seconds':args.seconds} if args.command == 'duration' else {'text':args.text} if args.command == 'voice' else {}
        data = {'command':args.command,'arguments':arguments}
    while True:
        request = urllib.request.Request(args.url.rstrip('/')+endpoint, data=json.dumps(data).encode() if data is not None else None, headers={'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                result = json.load(response)
        except (urllib.error.URLError, TimeoutError) as exc:
            if isinstance(exc, urllib.error.HTTPError):
                print(exc.read().decode(), file=sys.stderr)
            else:
                print(f'Game unavailable: {exc}', file=sys.stderr)
            return 1
        if args.command == 'watch':
            runtime = result.get('runtime',{})
            print(f"{time.strftime('%H:%M:%S')} {result['phase']:12} round={result['current_round']}/{result['total_rounds']} red={result['scores']['red']} blue={result['scores']['blue']} left={result['round_remaining']:.1f}s camera={result['camera_ready']} frames={runtime.get('frames')} voice={runtime.get('voice_status')}", flush=True)
            time.sleep(1)
        else:
            print(json.dumps(result,indent=2))
            return 2 if isinstance(result, dict) and result.get('accepted') is False else 0

if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        pass
