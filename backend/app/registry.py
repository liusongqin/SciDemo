"""Server-side validation; model schemas are guidance, never authorization."""
import math
import re
from .llm import TOOL_SPECS
from .science import safe_expression, RootInput

def validate_call(name: str, arguments: dict) -> dict:
    arguments=dict(arguments)
    arguments.pop('decision_summary',None)
    for key in ('expression','rhs'):
        value=arguments.get(key)
        if not isinstance(value,str): continue
        value=value.strip().replace('＝','=')
        definition=re.fullmatch(r'[A-Za-z]\s*\(\s*[xyzt]\s*\)\s*=\s*(.+)',value)
        if definition:
            value=definition.group(1).strip()
        elif '=' in value and value.count('=')==1:
            left,right=value.split('=',1); value=f'({left.strip()})-({right.strip()})'
        arguments[key]=value
    specs={s['function']['name']:s['function']['parameters'] for s in TOOL_SPECS}
    if name not in specs: raise ValueError('未注册的工具')
    schema=specs[name]
    if set(arguments)-set(schema['properties']): raise ValueError('工具含未知参数')
    if set(schema['required'])-set(arguments): raise ValueError('工具缺少必要参数')
    def check(value, spec):
        t=spec['type']
        if t=='string' and not isinstance(value,str): raise ValueError('参数必须为字符串')
        if t in ('integer','number'):
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value): raise ValueError('数值必须有限')
            if t=='integer' and not isinstance(value,int): raise ValueError('参数必须为整数')
        if t=='array':
            if not isinstance(value,list) or not 2<=len(value)<=500: raise ValueError('数组长度须为 2–500')
            for item in value: check(item,spec['items'])
        if t=='object':
            if not isinstance(value,dict): raise ValueError('参数必须为对象')
            if not set(value).issubset({'x','y','z','t'}): raise ValueError('变量名不在允许范围中')
            if not all(isinstance(item,(int,float)) and not isinstance(item,bool) and math.isfinite(item) for item in value.values()):
                raise ValueError('变量值必须为有限数值')
        if 'enum' in spec and value not in spec['enum']: raise ValueError('参数不在允许值中')
    for key,value in arguments.items():
        check(value,schema['properties'][key])
        if key in ('expression','rhs'): safe_expression(value)
    if 'samples' in arguments and not 3<=arguments['samples']<=2000: raise ValueError('采样点数须为 3–2000')
    if name=='find_root': return RootInput(**arguments).model_dump()
    if 't_span' in arguments and (len(arguments['t_span'])!=2 or arguments['t_span'][0]>=arguments['t_span'][1]): raise ValueError('时间区间须递增')
    return arguments
