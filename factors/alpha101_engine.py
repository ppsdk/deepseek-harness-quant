"""Restricted expression interpreter for the formulas in arXiv:1601.00991.

No Python eval, imports, attribute access or arbitrary function calls.
Missing inputs and undefined arithmetic remain NaN, including comparisons.
"""
from functools import lru_cache
import math
import re

import numpy as np
import pandas as pd


TOKEN = re.compile(r'\s*(\d*\.\d+|\d+\.?\d*|[A-Za-z_][A-Za-z_0-9.]*|\|\||&&|==|!=|<=|>=|[()+*/^<>,?:\-])')
ALLOWED_FUNCTIONS = {'abs', 'log', 'sign', 'rank', 'scale', 'signedpower', 'indneutralize',
 'min', 'max', 'correlation', 'covariance', 'delay', 'delta', 'ts_min', 'ts_max',
 'sum', 'stddev', 'product', 'ts_argmax', 'ts_argmin', 'ts_rank', 'decay_linear',
 'ma', 'ema', 'ref', 'rsi', 'cross', 'count'}

FUNCTION_ARITY = {**{k:(1,1) for k in ('abs','log','sign','rank')},
                  **{k:(2,2) for k in ALLOWED_FUNCTIONS-{'abs','log','sign','rank','scale','correlation','covariance'}},
                  'scale':(1,2),'correlation':(3,3),'covariance':(3,3)}

PREC = {'||': 1, '&&': 2, '==': 3, '!=': 3, '<': 3, '>': 3, '<=': 3, '>=': 3,
        '+': 4, '-': 4, '*': 5, '/': 5, '^': 6}


@lru_cache(maxsize=256)
def parse(formula):
    formula = formula.strip()
    tokens, offset = [], 0
    while offset < len(formula):
        m = TOKEN.match(formula, offset)
        if not m:
            raise ValueError('Alpha101 公式包含不支持的字符。')
        tokens.append(m[1].lower())
        offset = m.end()
    pos = 0

    def take(wanted=None):
        nonlocal pos
        if pos >= len(tokens) or (wanted is not None and tokens[pos] != wanted):
            raise ValueError('Alpha101 公式括号或参数不完整。')
        value = tokens[pos]
        pos += 1
        return value

    def expr(minimum=0):
        token = take()
        if token in ('+', '-'):
            left = ('unary', token, expr(6))
        elif token == '(':
            left = expr()
            take(')')
        elif re.fullmatch(r'\d*\.\d+|\d+\.?\d*', token):
            left = ('number', float(token))
        elif re.fullmatch(r'[a-z_][a-z_0-9.]*', token):
            if pos < len(tokens) and tokens[pos] == '(':
                take('(')
                args = []
                if pos < len(tokens) and tokens[pos] != ')':
                    args.append(expr())
                    while pos < len(tokens) and tokens[pos] == ',':
                        take(',')
                        args.append(expr())
                take(')')
                left = ('call', token, tuple(args))
            else:
                left = ('name', token)
        else:
            raise ValueError('Alpha101 公式格式错误。')
        while pos < len(tokens):
            op = tokens[pos]
            if op == '?' and minimum == 0:
                take('?')
                yes = expr()
                take(':')
                left = ('where', left, yes, expr())
            elif op in PREC and PREC[op] >= minimum:
                take()
                right = expr(PREC[op] + (0 if op == '^' else 1))
                left = ('binary', op, left, right)
            else:
                break
        return left

    tree = expr()
    if pos != len(tokens):
        raise ValueError('Alpha101 公式结尾包含多余内容。')
    return tree


def evaluate(formula, panels, extra_inputs=None):
    extra_inputs = extra_inputs or {}
    template = panels['close']
    memo = {}

    def frame(x):
        if isinstance(x, pd.DataFrame):
            return x
        return pd.DataFrame(x, index=template.index, columns=template.columns)

    def clean(x):
        return frame(x).replace([np.inf, -np.inf], np.nan).astype(float)

    def window(x):
        if isinstance(x, pd.DataFrame) or not math.isfinite(x) or x < 1:
            raise ValueError('Alpha101 时间窗口必须为正数。')
        return int(math.floor(x))

    def call(name, args):
        if name in ('ma','ema','ref','rsi','count'):
            from factors.research_library import read_config
            maximum=read_config('research_workflow')['formula']['max_window']
            value=args[1]
            if isinstance(value,pd.DataFrame) or not math.isfinite(value) or not float(value).is_integer() or not 1<=value<=maximum:
                raise ValueError('计算周期须为1至'+str(maximum)+'的整数，不能引用未来。')
            n=int(value);x=frame(args[0])
            if name=='ma':return x.rolling(n,min_periods=n).mean()
            if name=='ema':return x.ewm(span=n,adjust=False,min_periods=n).mean().where(x.notna())
            if name=='ref':return x.shift(n)
            if name=='count':return x.ne(0).astype(float).where(x.notna()).rolling(n,min_periods=n).sum()
            from factors.research_indicators import calculate
            return calculate(dict(operation='rsi',params={'n':n}),dict(close=x))
        if name=='cross':
            a,b=frame(args[0]),frame(args[1])
            known=a.notna()&b.notna()&a.shift().notna()&b.shift().notna()
            return (a.gt(b)&a.shift().le(b.shift())).astype(float).where(known)
        if name == 'abs':
            return abs(args[0])
        if name == 'log':
            x = frame(args[0])
            return np.log(x.where(x > 0))
        if name == 'sign':
            return np.sign(args[0])
        if name == 'rank':
            return frame(args[0]).rank(axis=1, pct=True, method='average')
        if name == 'scale':
            x = frame(args[0])
            return x.div(x.abs().sum(axis=1).replace(0, np.nan), axis=0) * (args[1] if len(args) > 1 else 1)
        if name == 'signedpower':
            return np.sign(args[0]) * abs(args[0]) ** args[1]
        if name == 'indneutralize':
            x, groups = frame(args[0]), args[1]
            if not isinstance(groups, pd.DataFrame):
                raise ValueError('缺少当时的行业分类，不能进行行业内比较。')
            out = x.copy() * np.nan
            for day in x.index:
                g = groups.loc[day]
                out.loc[day] = x.loc[day] - x.loc[day].groupby(g).transform('mean')
            return out
        if name in ('min', 'max') and isinstance(args[1], pd.DataFrame):
            return np.minimum(args[0], args[1]) if name == 'min' else np.maximum(args[0], args[1])
        if name in ('correlation', 'covariance'):
            x, y, n = frame(args[0]), frame(args[1]), window(args[2])
            return x.rolling(n, min_periods=n).corr(y) if name == 'correlation' else x.rolling(n, min_periods=n).cov(y)
        x, n = frame(args[0]), window(args[1])
        if name == 'delay':
            return x.shift(n)
        if name == 'delta':
            return x - x.shift(n)
        roll = x.rolling(n, min_periods=n)
        if name in ('ts_min', 'min'):
            return roll.min()
        if name in ('ts_max', 'max'):
            return roll.max()
        if name == 'sum':
            return roll.sum()
        if name == 'stddev':
            return roll.std(ddof=1)
        if name == 'product':
            return roll.apply(np.prod, raw=True)
        if name == 'ts_argmax':
            return roll.apply(lambda a: np.argmax(a) + 1, raw=True)
        if name == 'ts_argmin':
            return roll.apply(lambda a: np.argmin(a) + 1, raw=True)
        if name == 'ts_rank':
            return roll.rank(pct=True, method='average')
        if name == 'decay_linear':
            weights = np.arange(1, n + 1, dtype=float)
            return roll.apply(lambda a: a @ weights / weights.sum(), raw=True)
        raise ValueError('Alpha101 使用了未支持的函数：' + name)

    def visit(node):
        if node in memo:
            return memo[node]
        kind = node[0]
        if kind == 'number':
            result = node[1]
        elif kind == 'name':
            name = node[1]
            if name in extra_inputs:
                result = extra_inputs[name]
            elif name == 'returns':
                result = template.pct_change(fill_method=None)
            elif re.fullmatch(r'adv\d+', name):
                n = int(name[3:])
                result = panels['amount'].rolling(n, min_periods=n).mean()
            elif name.startswith('indclass.'):
                level = name.split('.')[1]
                if level not in ('sector', 'industry', 'subindustry') or level not in panels:
                    raise ValueError('缺少当时的行业分类：' + level)
                result = panels[level]
            elif name in ('open', 'close', 'high', 'low', 'volume', 'vwap', 'cap') and name in panels:
                result = panels[name]
            else:
                raise ValueError('缺少 Alpha101 数据：' + name)
        elif kind == 'call':
            if node[1] not in ALLOWED_FUNCTIONS:
                raise ValueError('Alpha101 不允许调用函数：' + node[1])
            low,high=FUNCTION_ARITY[node[1]]
            if not low<=len(node[2])<=high:raise ValueError(node[1]+' 的参数数量不正确。')
            result = call(node[1], [visit(arg) for arg in node[2]])
        elif kind == 'unary':
            x = visit(node[2])
            result = -x if node[1] == '-' else x
        elif kind == 'where':
            cond, yes, no = (frame(visit(n)) for n in node[1:])
            result = yes.where(cond.ne(0), no).where(cond.notna())
        elif kind == 'binary':
            op, a, b = node[1], visit(node[2]), visit(node[3])
            if op == '+': result = a + b
            elif op == '-': result = a - b
            elif op == '*': result = a * b
            elif op == '/': result = frame(a) / frame(b).replace(0, np.nan)
            elif op == '^': result = frame(a) ** b
            else:
                a, b = frame(a), frame(b)
                valid = a.notna() & b.notna()
                if op == '<': result = a < b
                elif op == '>': result = a > b
                elif op == '<=': result = a <= b
                elif op == '>=': result = a >= b
                elif op == '==': result = a == b
                elif op == '!=': result = a != b
                elif op == '||': result = a.ne(0) | b.ne(0)
                elif op == '&&': result = a.ne(0) & b.ne(0)
                else: raise ValueError('未知公式运算。')
                result = result.astype(float).where(valid)
        else:
            raise ValueError('未知公式结构。')
        # Industry labels are strings; all other matrices are numeric.
        if isinstance(result, pd.DataFrame) and not (kind == 'name' and node[1].startswith('indclass.')):
            result = clean(result)
        memo[node] = result
        return result

    with np.errstate(all='ignore'):
        return clean(visit(parse(formula)))
