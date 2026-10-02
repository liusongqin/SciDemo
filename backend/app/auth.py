from __future__ import annotations
import base64, html as html_lib, httpx, re, secrets, time, urllib.parse, xml.etree.ElementTree as ET
from fastapi import HTTPException, Request, Response
from .config import settings
from .storage import store

COOKIE="scidemo_session"
SSO_LOGIN="https://sso.buaa.edu.cn/login"
SSO_CAPTCHA="https://sso.buaa.edu.cn/captcha"
UC_ACTIVATE="https://uc.buaa.edu.cn/api/login?target=https%3A%2F%2Fuc.buaa.edu.cn%2F%23%2Fuser%2Flogin"
UC_STATUS="https://uc.buaa.edu.cn/api/uc/status"
_prelogins: dict[str,dict] = {}

def set_session_cookie(response: Response, token: str):
    response.set_cookie(COOKIE,token,max_age=7*86400,httponly=True,samesite="lax",secure=settings.session_cookie_secure,path="/")

def current_identity(request: Request) -> dict|None:
    return store.session_identity(request.cookies.get(COOKIE))

def require_identity(request: Request) -> dict:
    identity=current_identity(request)
    if not identity: raise HTTPException(401,"会话已过期")
    return identity

def cas_login_url(service_url: str) -> str:
    return f"{settings.cas_base_url.rstrip('/')}/login?{urllib.parse.urlencode({'service':service_url})}"

async def validate_cas_ticket(ticket: str,service_url: str) -> dict:
    url=f"{settings.cas_base_url.rstrip('/')}/serviceValidate"
    try:
        async with httpx.AsyncClient(timeout=12,trust_env=False) as client:
            response=await client.get(url,params={"service":service_url,"ticket":ticket})
            response.raise_for_status(); data=response.json()
    except ValueError:
        root=ET.fromstring(response.text)
        user=root.findtext(".//{http://www.yale.edu/tp/cas}user")
        if not user: raise HTTPException(401,"北航统一认证票据验证未通过")
        return {"student_id":user,"display_name":user}
    except (httpx.HTTPError,ET.ParseError) as exc:
        raise HTTPException(502,"统一认证服务暂时不可用") from exc
    if not data.get("verified"): raise HTTPException(401,"北航统一认证票据验证未通过")
    return {"student_id":data.get("student_id") or data.get("user"),"display_name":data.get("display_name") or data.get("user")}

def _execution(page: str) -> str:
    match=re.search(r'<input[^>]*name=["\']execution["\'][^>]*value=["\']([^"\']+)',page,re.I)
    return html_lib.unescape(match.group(1)) if match else ""

def _captcha_id(page: str) -> str:
    match=re.search(r"config\.captcha\s*=\s*\{\s*type:\s*['\"][^'\"]+['\"],\s*id:\s*['\"]([^'\"]+)",page)
    return match.group(1) if match else ""

def _form_fields(page: str) -> dict[str,str]:
    result={}
    for tag in re.findall(r"<input\b[^>]*>",page,re.I):
        attrs=dict(re.findall(r"([\w:.-]+)\s*=\s*['\"]([^'\"]*)['\"]",tag))
        name=attrs.get("name",""); kind=attrs.get("type","").lower()
        if name and name not in ("username","password") and kind not in ("submit","button","image"):
            if kind=="hidden" or attrs.get("value"): result[name]=html_lib.unescape(attrs.get("value",""))
    return result

async def direct_prelogin() -> dict:
    if not settings.buaa_direct_auth: raise HTTPException(503,"北航直接认证当前未启用")
    async with httpx.AsyncClient(timeout=15,follow_redirects=False,trust_env=False) as client:
        response=await client.get(SSO_LOGIN); response.raise_for_status(); page=response.text
        execution=_execution(page)
        if not execution: raise HTTPException(502,"无法读取北航统一认证登录上下文")
        captcha_id=_captcha_id(page); image=None
        if captcha_id:
            captcha_response=await client.get(SSO_CAPTCHA,params={"captchaId":captcha_id})
            captcha_response.raise_for_status()
            image=f"data:{captcha_response.headers.get('content-type','image/jpeg')};base64,{base64.b64encode(captcha_response.content).decode()}"
        prelogin_id=secrets.token_urlsafe(24)
        _prelogins[prelogin_id]={"expires":time.time()+300,"cookies":dict(client.cookies),"page":page,
                                 "execution":execution,"captcha_id":captcha_id}
    for key,value in list(_prelogins.items()):
        if value["expires"]<time.time(): _prelogins.pop(key,None)
    return {"prelogin_id":prelogin_id,"captcha_required":bool(captcha_id),"captcha_image":image}

async def direct_login(prelogin_id: str,student_id: str,password: str,captcha: str="") -> dict:
    if not settings.buaa_direct_auth: raise HTTPException(503,"北航直接认证当前未启用")
    context=_prelogins.pop(prelogin_id,None)
    if not context or context["expires"]<time.time(): raise HTTPException(400,"登录上下文已过期，请刷新验证码")
    if context["captcha_id"] and not captcha.strip(): raise HTTPException(422,"请输入验证码")
    data=_form_fields(context["page"])
    data.update({"username":student_id,"password":password,"execution":context["execution"],
                 "_eventId":"submit","submit":"登录","type":"username_password"})
    if captcha.strip(): data.update({"captcha":captcha.strip(),"captchaResponse":captcha.strip()})
    async with httpx.AsyncClient(timeout=20,follow_redirects=True,trust_env=False,cookies=context["cookies"]) as client:
        response=await client.post(SSO_LOGIN,data=data)
        body=response.text
        if response.status_code==401 or _execution(body):
            error_match=re.search(r'<(?:div|p|span)[^>]*(?:id=["\']errorDiv|class=["\'][^"\']*errors)[^>]*>(.*?)</(?:div|p|span)>',body,re.I|re.S)
            message=re.sub(r"<[^>]+>","",error_match.group(1)).strip() if error_match else "学号、密码或验证码错误"
            raise HTTPException(401,html_lib.unescape(message))
        await client.get(UC_ACTIVATE)
        status=await client.get(UC_STATUS,headers={"Accept":"application/json","X-Requested-With":"XMLHttpRequest"})
        try: payload=status.json()
        except ValueError as exc: raise HTTPException(401,"北航统一认证未返回有效用户身份") from exc
        user=payload.get("data") if payload.get("code")==0 else None
        if not user: raise HTTPException(401,"北航统一认证验证未通过")
        school_id=str(user.get("schoolid") or user.get("username") or student_id)
        return {"student_id":school_id,"display_name":user.get("name") or school_id}
