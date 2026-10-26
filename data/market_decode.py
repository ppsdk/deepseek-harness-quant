"""Isolated native JS decoder: a vendor V8 fatal error must not kill the UI."""
import json
import sys


def main():
    request = json.load(sys.stdin)
    import py_mini_racer
    script = request.get('script')
    if script is None:
        from akshare.stock.cons import zh_js_decode
        script = zh_js_decode
    js = py_mini_racer.MiniRacer()
    try:
        js.eval(script)
        result = js.call('d', request['payload'])
        sys.stdout.write(json.dumps(result, ensure_ascii=True, allow_nan=False))
    finally:
        if hasattr(js, 'close'):
            js.close()


if __name__ == '__main__':
    main()
